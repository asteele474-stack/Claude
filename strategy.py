"""Committee Momentum: a rules-based ETF rotation strategy with a Claude risk committee.

How it works
  1. Regime filter   - SPY above its 200-day average = risk-on (whole universe eligible);
                       below it = risk-off (only bonds and gold eligible, else cash).
  2. Ranking         - each ETF is scored on blended 1/3/6-month momentum divided by its
                       volatility, so a steady climber beats a lucky spike. Anything with
                       negative momentum or below its 100-day average is excluded.
  3. Sizing          - the top picks are weighted by inverse volatility, then the whole
                       book is scaled to a target annual volatility, with per-name and
                       gross-exposure caps. Leftover is cash.
  4. Claude committee - a bull, a bear and a risk manager debate each pick and can only
                       keep, halve or drop it. Claude can never add a trade or increase size.

Usage
  python strategy.py              # print the plan, place nothing
  python strategy.py --execute    # rebalance the Alpaca *paper* account
  python strategy.py --no-committee
"""
import argparse
import json
import math
import os
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from dotenv import load_dotenv

UNIVERSE = [
    "SPY", "QQQ", "IWM", "EFA", "EEM",          # equities
    "XLK", "XLF", "XLE", "XLV", "XLU", "XLI",   # sectors
    "TLT", "IEF", "GLD", "DBC", "VNQ",          # bonds, gold, commodities, real estate
]
DEFENSIVE = ["TLT", "IEF", "GLD"]
REGIME_SYMBOL = "SPY"

TOP_N = 4
TARGET_VOL = 0.12        # annualised portfolio volatility target
MAX_WEIGHT = 0.30        # per-position cap
MAX_GROSS = 1.0          # no leverage
MIN_TRADE_FRACTION = 0.02  # skip rebalances smaller than 2% of equity
LOOKBACK_DAYS = 400      # calendar days of history to fetch (covers the 200-day average)

MODEL = "claude-opus-5-5"


# ---------------------------------------------------------------------------
# Signals (pure functions, shared with backtest.py)
# ---------------------------------------------------------------------------

def score_universe(closes: pd.DataFrame) -> pd.DataFrame:
    """Per-symbol signal table from a wide frame of daily closes (rows = dates)."""
    rets = closes.pct_change()
    last = closes.iloc[-1]
    mom = sum(closes.iloc[-1] / closes.iloc[-1 - n] - 1 for n in (21, 63, 126)) / 3
    vol = rets.iloc[-63:].std() * math.sqrt(252)
    table = pd.DataFrame({
        "price": last,
        "momentum": mom,
        "vol": vol,
        "above_sma100": last > closes.iloc[-100:].mean(),
    })
    table["score"] = table["momentum"] / table["vol"]
    return table


def risk_on(closes: pd.DataFrame) -> bool:
    spy = closes[REGIME_SYMBOL]
    return bool(spy.iloc[-1] > spy.iloc[-200:].mean())


def target_weights(closes: pd.DataFrame) -> tuple[dict[str, float], pd.DataFrame, bool]:
    """Return ({symbol: weight}, signal table, risk_on). Weights sum to <= MAX_GROSS."""
    closes = closes.dropna(axis=1, how="any")
    table = score_universe(closes)
    regime = risk_on(closes)

    eligible = table[(table["momentum"] > 0) & table["above_sma100"]]
    if not regime:
        eligible = eligible[eligible.index.isin(DEFENSIVE)]
    picks = eligible.sort_values("score", ascending=False).head(TOP_N)
    if picks.empty:
        return {}, table, regime

    raw = (1 / picks["vol"])
    w = raw / raw.sum()

    # Scale the book to the volatility target using the recent covariance.
    rets = closes[picks.index].pct_change().iloc[-63:].dropna()
    cov = rets.cov().values * 252
    port_vol = float(np.sqrt(w.values @ cov @ w.values))
    if port_vol > 0:
        w = w * min(TARGET_VOL / port_vol, MAX_GROSS)
    w = w.clip(upper=MAX_WEIGHT)
    if w.sum() > MAX_GROSS:
        w = w * MAX_GROSS / w.sum()
    return {s: round(float(x), 4) for s, x in w.items() if x > 0.001}, table, regime


# ---------------------------------------------------------------------------
# Claude risk committee (veto-only)
# ---------------------------------------------------------------------------

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "bull_case": {"type": "string"},
                    "bear_case": {"type": "string"},
                    "decision": {"type": "string", "enum": ["keep", "halve", "drop"]},
                    "reason": {"type": "string"},
                },
                "required": ["symbol", "bull_case", "bear_case", "decision", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}

COMMITTEE_PROMPT = """You are the risk committee for a systematic ETF momentum strategy.
The rules have already chosen the positions below. For EACH position, argue a short bull
case and a short bear case, then a risk manager decides: keep, halve, or drop.

You may only reduce risk. Default to "keep": the rules are tested and you should override
them only for a concrete reason visible in the data (e.g. a parabolic move far above trend,
volatility far above its peers, or heavy overlap with another pick that concentrates risk).
Do not invent news or facts you cannot see here.

Market regime: {regime}
Proposed positions (weight = fraction of equity):
{positions}

Signal table for the whole universe:
{table}
"""


def run_committee(weights: dict[str, float], table: pd.DataFrame, regime: bool) -> dict[str, dict]:
    import anthropic

    client = anthropic.Anthropic()
    positions = "\n".join(f"- {s}: {w:.1%}" for s, w in weights.items())
    prompt = COMMITTEE_PROMPT.format(
        regime="risk-on" if regime else "risk-off",
        positions=positions,
        table=table.round(4).to_string(),
    )
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={
            "effort": "high",
            "format": {"type": "json_schema", "schema": VERDICT_SCHEMA},
        },
        messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason == "refusal":
        print("Committee declined to review; keeping the rules-based plan.")
        return {}
    text = next((b.text for b in response.content if b.type == "text"), "")
    verdicts = json.loads(text)["verdicts"]
    return {v["symbol"].upper(): v for v in verdicts}


def apply_verdicts(weights: dict[str, float], verdicts: dict[str, dict]) -> dict[str, float]:
    out = {}
    for s, w in weights.items():
        decision = verdicts.get(s, {}).get("decision", "keep")
        if decision == "drop":
            continue
        out[s] = round(w / 2, 4) if decision == "halve" else w
    return out


# ---------------------------------------------------------------------------
# Alpaca data + execution
# ---------------------------------------------------------------------------

def fetch_closes(symbols: list[str], days: int) -> pd.DataFrame:
    from alpaca.data.enums import Adjustment
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    client = StockHistoricalDataClient(os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY"))
    bars = client.get_stock_bars(StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=TimeFrame.Day,
        start=datetime.now() - timedelta(days=days),
        adjustment=Adjustment.ALL,
    )).df
    closes = bars["close"].unstack(level="symbol")
    closes.index = pd.to_datetime(closes.index).normalize()
    return closes.sort_index()


def plan_orders(weights: dict[str, float], prices: pd.Series, equity: float,
                held: dict[str, float]) -> list[tuple[str, str, int]]:
    """Whole-share orders (symbol, side, qty) to move universe holdings to target weights."""
    orders = []
    for s in sorted(set(weights) | (set(held) & set(UNIVERSE))):
        target_qty = math.floor(weights.get(s, 0) * equity / prices[s])
        delta = target_qty - int(held.get(s, 0))
        if delta == 0:
            continue
        # Always fully exit dropped names; otherwise skip tiny rebalances.
        if target_qty > 0 and abs(delta) * prices[s] < MIN_TRADE_FRACTION * equity:
            continue
        orders.append((s, "buy" if delta > 0 else "sell", abs(delta)))
    return sorted(orders, key=lambda o: o[1] != "sell")  # sells first to free cash


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--execute", action="store_true", help="submit orders to the Alpaca paper account")
    parser.add_argument("--no-committee", action="store_true", help="skip the Claude risk committee")
    args = parser.parse_args()
    load_dotenv()

    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    closes = fetch_closes(UNIVERSE, LOOKBACK_DAYS)
    weights, table, regime = target_weights(closes)
    print(f"Regime: {'RISK-ON' if regime else 'RISK-OFF'}")
    print(table.sort_values("score", ascending=False).round(3).to_string(), "\n")
    print("Rules-based targets:", weights or "all cash")

    if weights and not args.no_committee:
        verdicts = run_committee(weights, table, regime)
        for s, v in verdicts.items():
            print(f"  {s}: {v['decision'].upper()} - {v['reason']}")
        weights = apply_verdicts(weights, verdicts)
        print("After committee:", weights or "all cash")

    trading = TradingClient(os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY"), paper=True)
    equity = float(trading.get_account().equity)
    held = {p.symbol: float(p.qty) for p in trading.get_all_positions()}
    orders = plan_orders(weights, closes.iloc[-1], equity, held)

    print(f"\nPaper equity: ${equity:,.2f}")
    if not orders:
        print("Already on target. No orders.")
        return
    for s, side, qty in orders:
        print(f"  {side.upper():4} {qty:>5} {s}")
    if not args.execute:
        print("\nDry run. Re-run with --execute to place these paper orders.")
        return
    for s, side, qty in orders:
        trading.submit_order(MarketOrderRequest(
            symbol=s, qty=qty,
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        ))
    print(f"\nSubmitted {len(orders)} paper orders.")


if __name__ == "__main__":
    main()
