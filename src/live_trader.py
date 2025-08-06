import time
import pandas as pd
import re
import json
from datetime import datetime
try:
    import MetaTrader5 as mt5
except ImportError:
    from . import mock_metatrader5 as mt5
from .data_collector import MT5DataCollector
from .agents.data_analyst_agent import DataAnalystAgent
from .agents.market_researcher_agent import MarketResearcherAgent
from .agents.trader_agent import TraderAgent
from .agents.risk_manager_agent import RiskManagerAgent
from .agents.fund_manager_agent import FundManagerAgent
from .communication import CommunicationBus
from .ufo_calculator import UfoCalculator
from .llm.llm_client import LLMClient
from .trade_executor import TradeExecutor
from .ufo_trading_engine import UFOTradingEngine
from .dynamic_reinforcement_engine import DynamicReinforcementEngine

class LiveTrader:
    def __init__(self, config):
        self.config = config
        self.position_pnl_tracker = {} # To track P&L for trailing stops
        
        # Helper function to parse config values with comments
        def parse_config_value(value, default):
            if isinstance(value, str):
                # Remove inline comments and extra spaces
                clean_value = value.split('#')[0].split('(')[0].strip()
                try:
                    return float(clean_value) if '.' in clean_value else int(clean_value)
                except ValueError:
                    return default
            return value
        
        # Read cycle period from config (default 40 minutes if not specified)
        cycle_period_raw = config['trading'].get('cycle_period_minutes', '40')
        self.cycle_period_minutes = parse_config_value(cycle_period_raw, 40)
        self.cycle_period_seconds = self.cycle_period_minutes * 60
        
        # Continuous monitoring variables
        self.last_position_update = None
        self.position_update_frequency_seconds = 300  # Update positions every 5 minutes
        self.continuous_monitoring_enabled = True
        self.portfolio_history = []  # Track portfolio value over time
        
        self.llm_client = LLMClient(api_key=config['openrouter']['api_key'])

        self.mt5_collector = MT5DataCollector(
            login=config['mt5']['login'],
            password=config['mt5']['password'],
            server=config['mt5']['server'],
            path=config['mt5']['path']
        )

        self.trade_executor = TradeExecutor(self.mt5_collector, self.config)
        self.ufo_engine = UFOTradingEngine(config)

        self.agents = {
            "data_analyst": DataAnalystAgent("DataAnalyst", self.mt5_collector),
            "researcher": MarketResearcherAgent("MarketResearcher", self.llm_client),
            "trader": TraderAgent("Trader", self.llm_client, self.mt5_collector),
            "risk_manager": RiskManagerAgent("RiskManager", self.llm_client, self.mt5_collector, self.config),
            "fund_manager": FundManagerAgent("FundManager", self.llm_client)
        }

        self.communication_bus = CommunicationBus()
        self.ufo_calculator = UfoCalculator(config['trading']['currencies'].split(','))

        # Initialize Dynamic Reinforcement Engine and force-enable it for simulator behavior
        self.dynamic_reinforcement_engine = DynamicReinforcementEngine(config)
        self.dynamic_reinforcement_engine.enabled = True
        print("✅ Dynamic Reinforcement Engine force-enabled for simulator-style execution.")

    def validate_and_correct_currency_pair(self, pair):
        """
        Validate and correct currency pair format. From simulator.
        Handles cases like: CADUSD -> USDCAD (inverted), invalid pairs.
        """
        valid_pairs = [
            'EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF',
            'EURAUD', 'EURCAD', 'EURCHF', 'EURGBP', 'EURJPY', 'EURNZD',
            'GBPAUD', 'GBPCAD', 'GBPCHF', 'GBPJPY', 'GBPNZD',
            'AUDCAD', 'AUDCHF', 'AUDJPY', 'AUDNZD',
            'CADCHF', 'CADJPY', 'CHFJPY', 'NZDCAD', 'NZDCHF', 'NZDJPY',
            'NZDUSD'
        ]

        clean_pair = pair.replace(self.config['mt5'].get('symbol_suffix', ''), '').replace('/', '').upper()

        if clean_pair in valid_pairs:
            return clean_pair, False # Correct, not inverted

        if len(clean_pair) >= 6:
            base = clean_pair[:3]
            quote = clean_pair[3:6]
            inverted_pair = quote + base
            if inverted_pair in valid_pairs:
                print(f"⚠️ Correcting inverted pair: {clean_pair} -> {inverted_pair}")
                return inverted_pair, True # Corrected, was inverted

        print(f"❌ Invalid currency pair: {pair} (cleaned: {clean_pair})")
        return None, False

    def calculate_ufo_entry_price(self, symbol, direction, ufo_data):
        """
        Calculate optimal entry price based on UFO methodology and live market data.
        Adapted from the simulator.
        """
        try:
            if not self.mt5_collector.is_connected():
                self.mt5_collector.connect()

            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                print(f"⚠️ Could not get live tick for {symbol}, cannot calculate entry price.")
                return None

            base_price = tick.ask if direction == 'BUY' else tick.bid

            if ufo_data:
                clean_symbol = symbol.replace(self.config['mt5'].get('symbol_suffix', ''), '')
                if len(clean_symbol) >= 6:
                    base_currency = clean_symbol[:3]
                    quote_currency = clean_symbol[3:6]

                    primary_tf = mt5.TIMEFRAME_M5
                    raw_ufo_data = ufo_data.get('raw_data', ufo_data)

                    if primary_tf in raw_ufo_data:
                        strength_data = raw_ufo_data[primary_tf]
                        base_strength = strength_data.get(base_currency, [0])[-1]
                        quote_strength = strength_data.get(quote_currency, [0])[-1]
                        strength_diff = base_strength - quote_strength

                        price_adjustment = 0.0
                        if abs(strength_diff) > 1.0:
                            if direction == 'BUY' and strength_diff > 0:
                                price_adjustment = -base_price * 0.0001  # 1 pip improvement
                            elif direction == 'SELL' and strength_diff < 0:
                                price_adjustment = base_price * 0.0001  # 1 pip improvement

                        optimal_price = base_price + price_adjustment
                        print(f"UFO Entry Price for {symbol}: Base={base_price:.5f}, Adj={price_adjustment:.5f}, Optimal={optimal_price:.5f}")
                        return max(optimal_price, base_price * 0.98) # Safety net

            return base_price

        except Exception as e:
            print(f"⚠️ Error calculating UFO entry price for {symbol}: {e}")
            return None

    def get_pip_value_multiplier(self, symbol):
        """Get correct pip value multiplier for different currency pairs, as in the simulator."""
        symbol_clean = symbol.replace('-ECN', '').upper()

        # JPY pairs use 1000 multiplier (pip = 0.01)
        jpy_pairs = ['USDJPY', 'EURJPY', 'GBPJPY', 'AUDJPY', 'NZDJPY', 'CHFJPY', 'CADJPY']
        if any(jpy_pair in symbol_clean for jpy_pair in jpy_pairs):
            return 1000

        # Most other forex pairs use 10000 multiplier (pip = 0.0001)
        return 10000

    def _manage_open_positions_simulator_style(self):
        """
        Manages open positions using the logic from the full_day_simulation.
        This includes custom P&L calculation and rule-based closures.
        """
        try:
            open_positions = self.agents['risk_manager'].portfolio_manager.get_positions()
            if open_positions is None or open_positions.empty:
                if self.position_pnl_tracker:
                    print("All positions closed, clearing P&L tracker.")
                    self.position_pnl_tracker.clear()
                return

            # Get current prices for all position symbols
            market_data = self.get_real_time_market_data_for_positions(open_positions)

            # Clean up tracker from closed positions
            open_tickets = [pos.ticket for _, pos in open_positions.iterrows()]
            for ticket in list(self.position_pnl_tracker.keys()):
                if ticket not in open_tickets:
                    print(f"Position {ticket} closed, removing from P&L tracker.")
                    del self.position_pnl_tracker[ticket]

            positions_to_close = []

            for _, position in open_positions.iterrows():
                ticket = position.ticket
                symbol = position.symbol

                if symbol not in market_data:
                    print(f"⚠️ No market data for {symbol}, cannot manage position {ticket}.")
                    continue

                # For BUY (type 0), we close at 'bid'. For SELL (type 1), we close at 'ask'.
                current_price = market_data[symbol]['bid'] if position.type == 0 else market_data[symbol]['ask']

                # --- P&L Calculation (from simulator) ---
                pip_multiplier = self.get_pip_value_multiplier(symbol)
                price_diff = current_price - position.price_open
                if position.type == 1: # SELL
                    price_diff = -price_diff

                pnl = price_diff * position.volume * pip_multiplier

                # --- Trailing Stop Logic (from simulator) ---
                if ticket not in self.position_pnl_tracker:
                    self.position_pnl_tracker[ticket] = {'peak_pnl': pnl}
                elif pnl > self.position_pnl_tracker[ticket]['peak_pnl']:
                    self.position_pnl_tracker[ticket]['peak_pnl'] = pnl

                peak_pnl = self.position_pnl_tracker[ticket]['peak_pnl']

                # --- Closing Conditions (from simulator) ---
                close_reason = None
                position_age_hours = (datetime.utcnow() - pd.to_datetime(position.time, unit='s')).total_seconds() / 3600

                if pnl > 75:
                    close_reason = f"take profit (P&L: ${pnl:.2f})"
                elif pnl < -50:
                    close_reason = f"stop loss (P&L: ${pnl:.2f})"
                elif position_age_hours > 4:
                    close_reason = f"time-based exit (age: {position_age_hours:.1f} hours)"
                elif peak_pnl > 30 and pnl < peak_pnl * 0.7:
                    close_reason = f"trailing stop (P&L dropped to ${pnl:.2f} from peak of ${peak_pnl:.2f})"

                if close_reason:
                    positions_to_close.append({'ticket': ticket, 'symbol': symbol, 'reason': close_reason})

            # --- Execute Closures ---
            if positions_to_close:
                print(f"\n--- Simulator-Style Position Management ---")
                for closure in positions_to_close:
                    print(f"🎯 Closing {closure['symbol']} ({closure['ticket']}): {closure['reason']}")
                    self.trade_executor.close_trade(closure['ticket'])

        except Exception as e:
            print(f"❌ Error in simulator-style position management: {e}")

    def _perform_dynamic_reinforcement(self, ufo_data):
        """
        Performs dynamic reinforcement checks and executions, simulator-style.
        """
        if not self.dynamic_reinforcement_engine.enabled:
            return

        print("🎯 Checking for Dynamic Reinforcement opportunities...")
        try:
            open_positions = self.agents['risk_manager'].portfolio_manager.get_positions()
            if open_positions is None or open_positions.empty:
                return

            current_market_data = self.get_real_time_market_data_for_positions(open_positions)

            # The DRE in the simulator uses a pandas DataFrame, so we convert the MT5 position objects
            # to a compatible format.
            sim_positions_list = []
            for _, pos in open_positions.iterrows():
                sim_positions_list.append({
                    'ticket': pos.ticket, 'symbol': pos.symbol, 'direction': 'BUY' if pos.type == 0 else 'SELL',
                    'volume': pos.volume, 'entry_price': pos.price_open, 'current_price': pos.price_current,
                    'pnl': pos.profit, 'timestamp': pd.to_datetime(pos.time, unit='s')
                })

            market_events = self.dynamic_reinforcement_engine.detect_market_events(
                sim_positions_list, current_market_data, ufo_data
            )

            if not market_events:
                print("No dynamic reinforcement events detected.")
                return

            print(f"🎯 Dynamic Reinforcement: {len(market_events)} market events detected")
            for event in market_events:
                position_dict = event.get('position')
                if not position_dict:
                    continue

                reinforcement_plan, message = self.dynamic_reinforcement_engine.calculate_dynamic_reinforcement(
                    position_dict, event, current_market_data, ufo_data
                )

                if reinforcement_plan:
                    print(f"  ⚡ {event['type']}: {position_dict['symbol']} - {message}")
                    print(f"    📊 Reinforcement: {reinforcement_plan['additional_lots']:.2f} lots")

                    # Execute the reinforcement trade
                    trade_type = mt5.ORDER_TYPE_BUY if position_dict['direction'] == 'BUY' else mt5.ORDER_TYPE_SELL
                    self.trade_executor.execute_ufo_trade(
                        symbol=position_dict['symbol'],
                        trade_type=trade_type,
                        volume=reinforcement_plan['additional_lots'],
                        comment=f"Dynamic {reinforcement_plan.get('type', 'Reinforcement')}"
                    )
                else:
                    print(f"  ⏸️ {position_dict['symbol']}: {message}")

        except Exception as e:
            print(f"❌ Error during dynamic reinforcement: {e}")


    def _run_continuous_monitoring(self, ufo_data):
        """
        Runs the faster, inner loop for continuous monitoring tasks.
        This includes managing open positions and dynamic reinforcement.
        """
        print("\n--- Continuous Monitoring ---")
        self._manage_open_positions_simulator_style()
        self._perform_dynamic_reinforcement(ufo_data)
        print("--- End Continuous Monitoring ---\n")

    def _run_main_trading_cycle(self):
        """
        Runs the main, slower trading cycle for analysis and new trade decisions.
        """
        print("\n" + "="*60)
        print(f"🚀 STARTING NEW TRADING CYCLE - {pd.Timestamp.now()} 🚀")
        print("="*60)

        # 1. Data Collection (Simulator Style: All Symbols)
        print("📊 PHASE 1: Data Collection (Simulator Style)")
        symbols = self.config['trading']['symbols'].split(',')
        symbol_suffix = self.config['mt5'].get('symbol_suffix', '')
        all_price_data = {}
        timeframes = [mt5.TIMEFRAME_M5, mt5.TIMEFRAME_M15, mt5.TIMEFRAME_H1, mt5.TIMEFRAME_H4, mt5.TIMEFRAME_D1]
        timeframe_bars = {
            mt5.TIMEFRAME_M5: 240, mt5.TIMEFRAME_M15: 80, mt5.TIMEFRAME_H1: 20,
            mt5.TIMEFRAME_H4: 120, mt5.TIMEFRAME_D1: 100
        }

        for symbol in symbols:
            symbol_with_suffix = symbol + symbol_suffix
            data = self.agents['data_analyst'].execute({
                'source': 'mt5', 'symbol': symbol_with_suffix,
                'timeframes': timeframes, 'num_bars': timeframe_bars
            })
            if data:
                all_price_data[symbol] = data

        if not all_price_data:
            print("Could not fetch any price data for the main cycle.")
            return None # Return None to indicate failure
        print(f"✅ Collected data for {len(all_price_data)} symbols")

        # 2. UFO Calculation (Simulator Style: Reshaped Data)
        print("🛸 PHASE 2: UFO Analysis (Simulator Style)")
        reshaped_data = {}
        for symbol, timeframe_data in all_price_data.items():
            for timeframe, df in timeframe_data.items():
                if timeframe not in reshaped_data:
                    reshaped_data[timeframe] = pd.DataFrame()
                reshaped_data[timeframe][symbol] = df['close']

        incremental_sums_dict = {}
        for timeframe, price_df in reshaped_data.items():
            variation_data = self.ufo_calculator.calculate_percentage_variation(price_df)
            incremental_sums_dict[timeframe] = self.ufo_calculator.calculate_incremental_sum(variation_data)

        ufo_data = self.ufo_calculator.generate_ufo_data(incremental_sums_dict)
        print("✅ UFO analysis completed.")

        # 3. Agentic Workflow for new trade decisions
        print("🤖 PHASE 3: Agentic Workflow")
        economic_events = self.agents['data_analyst'].execute({'source': 'economic_calendar'})
        open_positions = self.agents['risk_manager'].portfolio_manager.get_positions()
        research_result = self.agents['researcher'].execute(ufo_data, economic_events)

        diversification_config = {
            'min_positions_for_session': self.ufo_engine.min_positions_for_session,
            'target_positions_when_available': self.ufo_engine.target_positions_when_available,
            'max_concurrent_positions': self.ufo_engine.max_concurrent_positions
        }

        trade_decision_str = self.agents['trader'].execute(
            research_result['consensus'], open_positions, diversification_config=diversification_config
        )
        risk_assessment = self.agents['risk_manager'].execute(trade_decision_str)
        authorization = self.agents['fund_manager'].execute(trade_decision_str, risk_assessment)

        # 4. Output and Execution
        position_count = len(open_positions) if open_positions is not None and not open_positions.empty else 0
        print("\n--- Main Cycle Decisions ---")
        print(f"Timestamp: {pd.Timestamp.now()}")
        print(f"Diversification: {position_count}/{self.ufo_engine.max_concurrent_positions}")
        print(f"Research: {research_result['consensus']}")
        print(f"Decision: {trade_decision_str}")
        print(f"Risk: {risk_assessment}")
        print(f"Authorization: {authorization}")

        should_execute = "APPROVE" in authorization.upper()
        if should_execute:
            account_info = self.mt5_collector.connect() and mt5.account_info()
            should_trade, trade_reason = self.ufo_engine.should_open_new_trades(
                current_positions=open_positions,
                portfolio_status={'balance': account_info.balance, 'equity': account_info.equity} if account_info else None,
                ufo_data=ufo_data
            )

            if not should_trade:
                print(f"UFO Engine blocked new trades: {trade_reason}")
            else:
                print(f"🎯 UFO Engine approved new trades: {trade_reason}")
                self._execute_trades_from_decision(trade_decision_str, ufo_data, symbol_suffix)

        return ufo_data # Return data for the monitoring loop

    def _execute_trades_from_decision(self, trade_decision_str, ufo_data, symbol_suffix):
        """Helper to parse and execute trades from an LLM decision string."""
        try:
            json_match = re.search(r'{.*}', trade_decision_str, re.DOTALL)
            if not json_match:
                print("No JSON object found in the LLM decision.")
                return

            json_str = re.sub(r'//.*?\n', '\n', json_match.group(0))
            json_str = re.sub(r',\s*([}\]])', r'\1', json_str)
            parsed_data = json.loads(json_str)

            actions_list = parsed_data.get('actions', parsed_data.get('trade_plan', parsed_data.get('trades', [])))
            if 'trades' in parsed_data and 'actions' not in parsed_data: # Convert format if needed
                actions_list = [
                    {'action': 'new_trade', 'currency_pair': t['currency_pair'], 'direction': t.get('direction', 'BUY').upper(), 'volume': t.get('lot_size', 0.1)}
                    for t in actions_list
                ]

            print("Executing trades with simulator logic (validation, optimal price, raw volume)...")
            for action in actions_list:
                if action.get('action') == 'new_trade':
                    symbol = action.get('symbol') or action.get('currency_pair', '')
                    direction = action.get('direction', '').upper()
                    volume = action.get('volume') or action.get('lot_size', 0.1)

                    base_symbol = symbol.replace("/", "")
                    corrected_symbol, was_inverted = self.validate_and_correct_currency_pair(base_symbol)

                    if corrected_symbol is None: continue

                    if was_inverted:
                        direction = 'SELL' if direction == 'BUY' else 'BUY'
                        print(f"⚠️ Direction inverted to {direction} due to pair correction.")

                    full_symbol = corrected_symbol + symbol_suffix
                    trade_type = mt5.ORDER_TYPE_BUY if direction == 'BUY' else mt5.ORDER_TYPE_SELL
                    optimal_price = self.calculate_ufo_entry_price(full_symbol, direction, ufo_data)
                    
                    if optimal_price is None:
                        print(f"⚠️ Could not calculate optimal entry price for {full_symbol}. Skipping trade.")
                        continue

                    final_volume = max(0.01, volume)
                    print(f"📊 Executing trade: {full_symbol} {direction} {final_volume} lots @ optimal price {optimal_price:.5f}")
                    self.trade_executor.execute_trade(
                        symbol=full_symbol, trade_type=trade_type, volume=final_volume, price=optimal_price,
                        comment=action.get('comment', 'UFO Sim-Style Trade')
                    )

                elif action.get('action') == 'close_trade':
                    print(f"Closing trade by LLM request: {action.get('trade_id')}")
                    self.trade_executor.close_trade(action['trade_id'])

        except Exception as e:
            print(f"Error during UFO trade execution: {e}")

    def run(self):
        """
        Runs the live trading loop with a two-level structure to clone the simulator's behavior.
        - Outer loop: Runs the main analysis and new trade decisions every `cycle_period_minutes`.
        - Inner loop: Runs continuous monitoring every `position_update_frequency_seconds`.
        """
        last_main_cycle_time = 0
        last_monitoring_time = 0
        ufo_data_from_last_cycle = None

        while True:
            try:
                now = time.time()

                # --- Active Session Check ---
                if not self.ufo_engine.is_active_session():
                    print(f"Outside active trading session. Waiting 1 minute...")
                    time.sleep(60)
                    continue

                # --- Portfolio and Session End Checks (High Priority) ---
                # These checks run frequently to react quickly to major events.
                if now - last_monitoring_time > 60: # Check every minute
                    account_info = self.mt5_collector.connect() and mt5.account_info()
                    if account_info:
                        stop_breached, stop_reason = self.ufo_engine.check_portfolio_equity_stop(account_info.balance, account_info.equity)
                        if stop_breached:
                            print(f"🚨 UFO PORTFOLIO STOP TRIGGERED: {stop_reason}")
                            self.trade_executor.close_all_positions()
                            print("🚨 All positions closed. Waiting 5 minutes...")
                            time.sleep(300)
                            continue

                    economic_events = self.agents['data_analyst'].execute({'source': 'economic_calendar'})
                    should_close, close_reason = self.ufo_engine.should_close_for_session_end(economic_events)
                    if should_close:
                        print(f"🌅 UFO SESSION END TRIGGERED: {close_reason}")
                        self.trade_executor.close_all_positions()
                        print("All positions closed. Waiting 5 minutes...")
                        time.sleep(300)
                        continue

                # --- Inner Loop: Continuous Monitoring ---
                if now - last_monitoring_time >= self.position_update_frequency_seconds:
                    self._run_continuous_monitoring(ufo_data_from_last_cycle)
                    last_monitoring_time = now

                # --- Outer Loop: Main Trading Cycle ---
                if now - last_main_cycle_time >= self.cycle_period_seconds:
                    cycle_ufo_data = self._run_main_trading_cycle()
                    if cycle_ufo_data is not None:
                        ufo_data_from_last_cycle = cycle_ufo_data
                    last_main_cycle_time = now
                
                # Sleep briefly to prevent high CPU usage
                time.sleep(10)

            except KeyboardInterrupt:
                print("\nTrading interrupted by user. Exiting...")
                self.mt5_collector.disconnect()
                break
            except Exception as e:
                print(f"❌❌❌ An unexpected error occurred in the main loop: {e}")
                import traceback
                traceback.print_exc()
                print("Waiting 60 seconds before retrying...")
                time.sleep(60)

    def get_real_time_market_data_for_positions(self, open_positions):
        """
        Collect real-time market data for all open positions
        This replaces the empty current_market_data = {} with actual price data
        """
        current_market_data = {}
        
        if open_positions is None or len(open_positions) == 0:
            return current_market_data
            
        try:
            # Connect to MT5 to get current prices
            if not self.mt5_collector.connect():
                print("⚠️ Failed to connect to MT5 for market data collection")
                return current_market_data
                
            # Extract unique symbols from positions
            symbols_to_fetch = set()
            for _, position in open_positions.iterrows():
                symbols_to_fetch.add(position['symbol'])
            
            # Get current tick data for each symbol
            for symbol in symbols_to_fetch:
                try:
                    tick = mt5.symbol_info_tick(symbol)
                    if tick is not None:
                        current_market_data[symbol] = {
                            'close': tick.bid,  # Use bid for current price
                            'ask': tick.ask,
                            'bid': tick.bid,
                            'spread': tick.ask - tick.bid,
                            'timestamp': pd.Timestamp.now()
                        }
                        print(f"📊 Real-time data: {symbol} @ {tick.bid:.5f} (spread: {(tick.ask - tick.bid):.5f})")
                    else:
                        print(f"⚠️ No tick data available for {symbol}")
                        # Fallback: try to get recent bar data
                        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 1)
                        if rates is not None and len(rates) > 0:
                            current_market_data[symbol] = {
                                'close': rates[0]['close'],
                                'ask': rates[0]['close'] + 0.0001,  # Estimated spread
                                'bid': rates[0]['close'],
                                'spread': 0.0001,
                                'timestamp': pd.Timestamp.now()
                            }
                            print(f"📊 Fallback data: {symbol} @ {rates[0]['close']:.5f} (from M1 bar)")
                        
                except Exception as e:
                    print(f"❌ Error getting market data for {symbol}: {e}")
                    continue
            
            self.mt5_collector.disconnect()
            print(f"✅ Collected real-time market data for {len(current_market_data)} symbols")
            
        except Exception as e:
            print(f"❌ Error in market data collection: {e}")
            
        return current_market_data
    
    def check_portfolio_status(self):
        """
        Checks overall portfolio status using UFO methodology.
        """
        try:
            positions = self.agents['risk_manager'].portfolio_manager.get_positions()
            if positions is None or len(positions) == 0:
                return
                
            portfolio_value = self.ufo_engine.calculate_portfolio_synthetic_value()
            print(f"Portfolio synthetic value: {portfolio_value:.2f}%")
            
            if portfolio_value <= -5.0:  # Portfolio stop loss threshold
                print("Portfolio stop loss triggered - closing all positions")
                for position in positions:
                    self.trade_executor.close_trade(position.ticket)
                    
        except Exception as e:
            print(f"Error checking portfolio status: {e}")
