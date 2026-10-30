---
name: trader
description: Run the Alpaca + Claude paper-trading tools - the Committee Momentum ETF strategy (strategy.py), its backtest (backtest.py), or the simple single-ticker agent (main.py). Use when the user says "trade", "run the trader", "trader skill", "rebalance", "run the strategy", "backtest", "should I buy [ticker]", or "/trader [ticker]".
---

# Trader

Three tools at the repo root, all on the Alpaca **paper** account:

| Ask | Command | What it does |
|---|---|---|
| "run the strategy", "rebalance" | `python strategy.py` (dry run), then `--execute` | Committee Momentum: regime filter + risk-adjusted momentum over 16 ETFs, volatility-targeted sizing, then a Claude bull/bear/risk committee that can only keep, halve or drop picks |
| "backtest" | `python backtest.py [--years N]` | Rules-only backtest vs SPY buy-and-hold, monthly rebalance, 10 bps costs |
| "trade [ticker]" | `TICKER=<T> python main.py` | One-shot BUY/SELL/HOLD on one ticker, 1 share |

## Steps

1. **Environment.** `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` and (unless `--no-committee`) `ANTHROPIC_API_KEY` must be set, from `.env` or the environment. Don't print their values. If missing, say which and stop. Install deps if needed: `pip install -r requirements.txt`.
2. **Strategy runs: dry run first.** Run `python strategy.py`, show the regime, the targets, each committee verdict and the order list. Only run `python strategy.py --execute` after the user confirms those orders.
3. **main.py submits an order immediately.** Confirm the ticker first unless the user already said to go ahead.
4. **Report** the outcome plainly. On errors, show the error and the likely cause (bad keys, network policy blocking `*.alpaca.markets`, missing data for a symbol).

## Guardrails

- Never switch `paper=True` to live trading unless the user explicitly asks, and confirm again before any live run.
- Don't change position sizing, caps or the universe without the user asking.
- Backtest results describe the past only; say so, and say it is not financial advice if the user asks about real money.
