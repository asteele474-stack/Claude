"""Backtest the rules-based part of Committee Momentum against buy-and-hold SPY.

The Claude committee is not simulated: it can't be replayed without hindsight, and it
can only shrink positions, so this is the strategy's "no veto" baseline.

Usage
  python backtest.py                  # ~8 years of Alpaca daily data
  python backtest.py --years 15
  python backtest.py --csv closes.csv # wide CSV: date column + one column per symbol
"""
import argparse
import math

import pandas as pd
from dotenv import load_dotenv

import strategy

REBALANCE_EVERY = 21     # trading days (~monthly)
COST_BPS = 10            # per unit of turnover (commission-free, so this is slippage)
WARMUP = 210             # days needed before the first signal


def run(closes: pd.DataFrame) -> pd.DataFrame:
    rets = closes.pct_change().fillna(0)
    weights: dict[str, float] = {}
    equity, rows = 1.0, []
    for i in range(WARMUP, len(closes)):
        day = closes.index[i]
        # Earn today's return on yesterday's weights.
        day_ret = sum(w * rets.at[day, s] for s, w in weights.items())
        equity *= 1 + day_ret
        if (i - WARMUP) % REBALANCE_EVERY == 0:
            new, _, _ = strategy.target_weights(closes.iloc[: i + 1])
            turnover = sum(abs(new.get(s, 0) - weights.get(s, 0)) for s in set(new) | set(weights))
            equity *= 1 - turnover * COST_BPS / 10_000
            weights = new
        rows.append({"date": day, "strategy": equity, "exposure": sum(weights.values())})
    out = pd.DataFrame(rows).set_index("date")
    spy = closes[strategy.REGIME_SYMBOL].loc[out.index]
    out["spy"] = spy / spy.iloc[0]
    return out


def stats(curve: pd.Series) -> dict[str, str]:
    daily = curve.pct_change().dropna()
    years = len(daily) / 252
    cagr = curve.iloc[-1] ** (1 / years) - 1
    vol = daily.std() * math.sqrt(252)
    drawdown = (curve / curve.cummax() - 1).min()
    return {
        "CAGR": f"{cagr:.1%}",
        "Volatility": f"{vol:.1%}",
        "Sharpe (rf=0)": f"{cagr / vol:.2f}" if vol else "n/a",
        "Max drawdown": f"{drawdown:.1%}",
        "Total return": f"{curve.iloc[-1] - 1:.1%}",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--years", type=float, default=8)
    parser.add_argument("--csv", help="read closes from a CSV instead of Alpaca")
    args = parser.parse_args()
    load_dotenv()

    if args.csv:
        closes = pd.read_csv(args.csv, index_col=0, parse_dates=True)
    else:
        closes = strategy.fetch_closes(strategy.UNIVERSE, int(args.years * 365) + 300)
    closes = closes.dropna(axis=1, thresh=int(len(closes) * 0.9)).ffill().dropna()

    result = run(closes)
    print(f"{result.index[0].date()} to {result.index[-1].date()}, {len(closes.columns)} ETFs\n")
    table = pd.DataFrame({"Committee Momentum": stats(result["strategy"]), "SPY buy & hold": stats(result["spy"])})
    print(table.to_string())
    print(f"\nAverage exposure: {result['exposure'].mean():.0%} (rest in cash)")
    print("Past performance does not predict future results.")


if __name__ == "__main__":
    main()
