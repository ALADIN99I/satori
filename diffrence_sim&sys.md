# Differences Between the Full-Day Simulator and the Real System

This document outlines the key architectural and functional differences between the full-day trading simulator (`full_day_simulation.py`) and the live trading system (`main.py` and `src/live_trader.py`).

## 1. Core Architectural Differences

- **Entry Point:**
  - **Simulator:** The entry point is `full_day_simulation.py`, which runs a simulation for a specific historical date.
  - **Real System:** The entry point is `main.py`, which launches the `LiveTrader` class from `src/live_trader.py` to run in a continuous, real-time loop.

- **Main Class:**
  - **Simulator:** Uses the `FullDayTradingSimulation` class, which is designed to manage a self-contained, time-driven simulation.
  - **Real System:** Uses the `LiveTrader` class, which is built to run indefinitely and interact with a live market environment.

## 2. Time Management

- **Simulator:**
  - Operates on a **simulated clock**.
  - Time advances in discrete intervals based on the `cycle_period_minutes` setting (e.g., every 40 minutes).
  - This allows for the controlled replay of a single trading day from a historical date.

- **Real System:**
  - Operates in **real-time**, using the system's current UTC time (`datetime.utcnow()`).
  - It runs in a continuous loop, pausing for the duration of `cycle_period_seconds` between each cycle.

## 3. Data Sourcing

- **Simulator:**
  - Fetches **historical market data** from MT5 for the specific simulation date.
  - The `get_historical_price_for_time` function is used to retrieve the price for a given symbol at a precise moment in the simulation.
  - It also uses cached historical economic calendar data relevant to the simulation date.

- **Real System:**
  - Fetches **live, real-time market data** from an active MT5 terminal.
  - It uses `mt5.symbol_info_tick()` to get the latest bid/ask prices.
  - It retrieves current and upcoming economic events.

## 4. Trade Execution

- **Simulator:**
  - **Simulates** trade execution entirely in memory.
  - When a trade is "executed," it is added to an in-memory list (`self.open_positions`).
  - Profit and loss (P&L) are calculated based on the difference between the entry price and the simulated market price.
  - It **does not** connect to a brokerage or execute any real trades.

- **Real System:**
  - Executes **real trades** on a live or demo trading account.
  - It uses the `TradeExecutor` class, which interfaces with the MT5 terminal to open and close positions.
  - The results of trades are real and have a financial impact on the connected account.

## 5. Trading Engine

- **Simulator:**
  - Uses the `SimulationUFOTradingEngine`, a specialized subclass of the main trading engine.
  - This engine is aware of the simulation time and overrides key decision-making methods (e.g., `should_trade_now`, `should_close_for_session_end`) to use the simulated clock instead of the real clock.

- **Real System:**
  - Uses the `UFOTradingEngine`, which is designed for a live environment.
  - All its decisions are based on the current, real-world time and market conditions.

## 6. Portfolio Management

- **Simulator:**
  - Manages a **simulated portfolio** that exists only in memory.
  - It starts with a fixed initial balance (`self.initial_balance`) and tracks the portfolio's value as trades are simulated.
  - P&L is tracked internally and is not real.

- **Real System:**
  - The portfolio is the **actual trading account** on the MT5 server.
  - It retrieves real-time account information, such as balance and equity, directly from the broker via the MT5 API.
  - P&L is real and affects the account's balance.

## 7. Role of the Mock MT5 Library

- **`src/mock_metatrader5.py`** is a fallback mechanism for development purposes, particularly on non-Windows systems where the `MetaTrader5` library cannot be installed.
- The **simulator is designed to use a real MT5 connection** to fetch historical data. It only uses the mock library if the import of the actual `MetaTrader5` library fails.
- The **real system requires a live MT5 connection** to function and cannot operate with the mock library.

## Summary Table

| Feature                 | Full-Day Simulator                                       | Real System                                            |
| ----------------------- | -------------------------------------------------------- | ------------------------------------------------------ |
| **Time Source**         | Simulated, discrete clock                                | Real-time system clock                                 |
| **Data Source**         | Historical MT5 data for a specific date                  | Live, real-time data from an active MT5 terminal       |
| **Trade Execution**     | In-memory simulation (no real trades)                    | Live trade execution on a real/demo account            |
| **Trading Engine**      | `SimulationUFOTradingEngine` (uses simulated time)       | `UFOTradingEngine` (uses real time)                    |
| **Portfolio**           | Simulated, in-memory object                              | Real MT5 trading account                               |
| **Economic Calendar**   | Historical data for the simulation date                  | Current and upcoming events                            |
