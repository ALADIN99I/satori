# Differences Between the Full-Day Simulator and the Real System

This document outlines the key architectural and functional differences between the full-day trading simulator (`full_day_simulation.py`) and the live trading system (`main.py` and `src/live_trader.py`).

## 1. Core Architectural Differences

- **Entry Point:**
  - **Simulator:** The entry point is `full_day_simulation.py`, which runs a self-contained simulation for a specific historical date.
  - **Real System:** The entry point is `main.py`, which launches the `LiveTrader` class from `src/live_trader.py` to run in a continuous, real-time loop.

- **Main Class:**
  - **Simulator:** Uses the `FullDayTradingSimulation` class, designed to manage a time-driven simulation from start to finish.
  - **Real System:** Uses the `LiveTrader` class, built to run indefinitely and interact with a live market environment.

## 2. Time Management

- **Simulator:**
  - Operates on a **simulated clock**.
  - Time advances in discrete intervals based on the `cycle_period_minutes` setting (e.g., every 40 minutes) via a `while` loop that increments a `current_time` variable.
  - This allows for the controlled replay of a single trading day from a historical date.

- **Real System:**
  - Operates in **real-time**, using the system's current UTC time (`datetime.utcnow()`).
  - It runs in an infinite `while True:` loop, pausing for the duration of `cycle_period_seconds` at the end of each cycle using `time.sleep()`.

## 3. Data Sourcing

- **Simulator:**
  - Fetches **historical market data** from MT5 for the specific simulation date.
  - The `get_historical_price_for_time` function is central, using `mt5.copy_rates_from()` to retrieve the price for a given symbol at a precise moment in the simulation.
  - It also uses cached historical economic calendar data relevant to the simulation date.

- **Real System:**
  - Fetches **live, real-time market data** from an active MT5 terminal.
  - It uses `mt5.symbol_info_tick()` to get the latest bid/ask prices for its `get_real_time_market_data_for_positions` method.
  - It retrieves current and upcoming economic events from a live source or a frequently updated cache.

## 4. Trade Execution

- **Simulator:**
  - **Simulates** trade execution entirely in memory.
  - When a trade is "executed," it is simply a dictionary added to an in-memory list (`self.open_positions`).
  - It **does not** connect to a brokerage or execute any real trades.

- **Real System:**
  - Executes **real trades** on a live or demo trading account via the MT5 terminal.
  - It uses the `TradeExecutor` class, which interfaces with the MT5 API to open and close positions using functions like `mt5.order_send()`.
  - The results of trades are real and have a financial impact on the connected account.

## 5. Trading Engine

- **Simulator:**
  - Uses the `SimulationUFOTradingEngine`, a specialized subclass of the main trading engine.
  - This engine's key feature is the `set_simulation_time()` method, which allows the simulator to inject a historical timestamp. It overrides methods like `should_trade_now` and `should_close_for_session_end` to use this simulated time.

- **Real System:**
  - Uses the standard `UFOTradingEngine`, which is designed for a live environment.
  - All its decisions are based on the current, real-world time and market conditions.

## 6. Portfolio Management

- **Simulator:**
  - Manages a **simulated portfolio** that exists only in memory.
  - It starts with a fixed `initial_balance` and tracks the portfolio's value as trades are simulated.
  - P&L is tracked internally and is not real.

- **Real System:**
  - The portfolio is the **actual trading account** on the MT5 server.
  - It retrieves real-time account information, such as balance and equity, directly from the broker via `mt5.account_info()`.
  - P&L is real and is fetched directly from the `profit` attribute of positions returned by `mt5.positions_get()`.

## 7. Role of the Mock MT5 Library

- **`src/mock_metatrader5.py`** is a fallback mechanism for development, particularly on non-Windows systems where the `MetaTrader5` library cannot be installed.
- The **simulator** is designed to use a real MT5 connection to fetch historical data but can function with the mock library for basic testing.
- The **real system** absolutely requires a live MT5 connection to function and cannot operate meaningfully with the mock library.

## 8. Detailed Functional Differences

This section covers more granular differences in how specific functionalities are implemented, which represent significant deviations in strategy and behavior.

- **P&L Calculation:**
  - **Simulator:** Implements its own P&L logic in the `update_portfolio_value` method. It includes a custom pip value calculation (`get_pip_value_multiplier`) to handle different asset types (e.g., JPY pairs vs. others). P&L is a purely in-memory calculation.
  - **Real System:** Relies entirely on the broker for P&L information. It fetches the current profit or loss of open positions directly from the MT5 terminal (`position.profit`). It does not perform any P&L calculations itself.

- **Trade Closing Logic:**
  - **Simulator:** Uses a set of hard-coded, per-trade rules to determine when to close a simulated position inside `update_portfolio_value`. This includes fixed P&L targets (e.g., take profit at +$75, stop loss at -$50), a time-based exit (close after 4 hours), and a simulated trailing stop.
  - **Real System:** Adheres strictly to the UFO methodology. Individual trades do not have fixed stop-losses or take-profits. The `UFOTradingEngine` makes portfolio-level decisions to close *all* positions based on conditions like the end of a trading session (`should_close_for_session_end`) or a breach of the total portfolio equity stop (`check_portfolio_equity_stop`).

- **Position Management (Reinforcement vs. Compensation):**
  - **Simulator:** Actively uses a `DynamicReinforcementEngine` and a "reinforcement" strategy (`simulate_realistic_position_tracking`) to add to existing positions based on certain criteria during the simulation.
  - **Real System:** Implements a more complex "compensation" strategy managed by the `UFOTradingEngine`. The `LiveTrader` calls `ufo_engine.execute_compensation_trade` to add to positions that have an immediate drawdown due to timing errors. Although the `DynamicReinforcementEngine` is initialized, it is **not actively used** in the main trading loop.

- **Risk Scaling and Volume Sizing:**
  - **Simulator:** Executes trades using the exact lot size (`volume`) proposed by the `TraderAgent` (LLM). It does not perform any additional risk analysis on the proposed volume.
  - **Real System:** Implements a critical **automatic risk scaling feature** in its main loop. Before execution, it calculates the total potential risk of all proposed trades. If the risk exceeds a predefined portfolio limit (e.g., 4.5%), it calculates a `risk_scale_factor` and scales down the volume of all new trades proportionally to stay within the safety limit. This is a crucial safety feature absent in the simulator.

- **Currency Pair Handling:**
  - **Simulator:** Contains a robust `validate_and_correct_currency_pair` function that cleans and corrects common formatting issues from the LLM, such as inverted pairs (e.g., correcting `CADUSD` to `USDCAD`).
  - **Real System:** Lacks this validation logic. It assumes the pair provided by the LLM is correctly formatted and simply appends the necessary broker suffix. This could be a significant point of failure if the LLM provides an invalid or incorrectly formatted pair.

- **Data Collection Strategy:**
  - **Simulator:** In each cycle, the `collect_market_data` function collects historical data for **all symbols** listed in the configuration file.
  - **Real System:** In its main loop, it collects live market data for only a **single `base_symbol`** (hard-coded as `EURUSD`) to perform its UFO analysis. This represents a fundamental strategic difference in how the market state is evaluated.

## Summary Table

| Feature                 | Full-Day Simulator                                       | Real System                                            |
| ----------------------- | -------------------------------------------------------- | ------------------------------------------------------ |
| **Time Source**         | Simulated, discrete clock using a `datetime` variable    | Real-time system clock (`datetime.utcnow()`)           |
| **Data Source**         | Historical MT5 data for a specific date (`mt5.copy_rates_from`) | Live, real-time data from an active MT5 terminal (`mt5.symbol_info_tick`) |
| **Trade Execution**     | In-memory simulation (trades are `dict` objects)         | Live trade execution on a real/demo account (`mt5.order_send`) |
| **P&L Calculation**     | Custom, in-memory logic with pip multipliers           | Relies on broker-provided data (`position.profit`)     |
| **Trade Closing**       | Per-trade rules (fixed SL/TP, time limits)               | Portfolio-level rules (session end, equity stop)       |
| **Position Management** | `DynamicReinforcementEngine` (Reinforcement)             | `UFOTradingEngine` (Compensation for timing errors)    |
| **Risk Scaling**        | None (uses raw volume from LLM)                          | **Yes** (auto-scales volume to meet portfolio risk limits) |
| **Pair Validation**     | **Yes** (robustly validates and corrects pairs)          | None (assumes LLM provides a valid pair)               |
| **Data Collection**     | All symbols in config                                    | Single `base_symbol` (`EURUSD`)                        |
| **Trading Engine**      | `SimulationUFOTradingEngine` (uses simulated time)       | `UFOTradingEngine` (uses real time)                    |
| **Portfolio**           | Simulated, in-memory object                              | Real MT5 trading account                               |
| **Economic Calendar**   | Historical data for the simulation date                  | Current and upcoming events                            |
