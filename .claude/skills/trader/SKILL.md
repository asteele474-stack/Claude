---
name: trader
description: Run the Alpaca + Claude paper-trading agent (main.py) for a ticker - fetches recent daily bars, asks Claude for BUY/SELL/HOLD, and places a 1-share paper order. Use when the user says "trade", "run the trader", "trader skill", "should I buy [ticker]", or "/trader [ticker]".
---

# Trader

Runs `main.py` at the repo root: pulls the last 5 days of daily bars from Alpaca,
asks Claude for a one-line `ACTION: BUY|SELL|HOLD` decision, and submits a
1-share market order on the **paper** account (`paper=True`).

## Steps

1. **Ticker.** Use the ticker the user named (args), uppercased. Default `AAPL`.
2. **Environment.** Check `.env` exists with `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`
   and `ANTHROPIC_API_KEY` set (don't print the values). If missing, tell the
   user which ones and stop. Install deps if needed: `pip install -r requirements.txt`.
3. **Confirm.** The script submits a real order to the paper account. Unless the
   user already said to go ahead, confirm the ticker before running.
4. **Run.** `TICKER=<TICKER> python main.py`
5. **Report.** Relay Claude's ACTION and REASON and whether an order was placed.
   If it errored, show the error and the likely cause (bad keys, market data
   unavailable for the ticker, network).

## Guardrails

- Never change `paper=True` to live trading unless the user explicitly asks,
  and confirm again before running it live.
- Never change order size from 1 share without the user asking.
- This is not financial advice; say so if the user asks whether to act on it
  with real money.
