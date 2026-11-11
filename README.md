# Alpaca Autonomous Day Trader

Autonomous US Equity Day Trading Bot with built-in Web Status Dashboard.
Designed for 24/7 cloud execution on **Koyeb**, **Railway**, **Render**, or **Docker**.

## Features
- **Strategy**: Opening Range Breakout (ORB) on 5-minute bars.
- **Risk Management**:
  - Allocated Budget: $1,000
  - Max Loss / Risk per Trade: $20 (2%)
  - Max Position Size: $500 (50%)
  - Automatic 1.5R - 2.0R Take-Profit and Stop-Loss Bracket Orders.
  - Auto-Flatten at 15:55 ET (zero overnight exposure).
- **Zero Token Cost**: Pure Python math & deterministic execution.
- **Web Dashboard**: Live status, New York market clock, portfolio balance, and rolling logs.

## Environment Variables
Configure these in Koyeb Dashboard:
- `ALPACA_API_KEY`: Your Alpaca Paper API Key (`PKHGWV...`)
- `ALPACA_SECRET_KEY`: Your Alpaca Paper Secret Key (`8wdwPV...`)
- `ALPACA_PAPER_TRADE`: `true`
- `PORT`: `8000` (default)
