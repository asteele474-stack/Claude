import os
from dotenv import load_dotenv
from anthropic import Anthropic
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from datetime import datetime, timedelta

# 1. Configuration & Client Setup
load_dotenv()
trading_client = TradingClient(os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY"), paper=True)
data_client = StockHistoricalDataClient(os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY"))
claude_client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

TICKER = "AAPL"

# 2. Fetch Market Data for Context
end_time = datetime.now()
start_time = end_time - timedelta(days=5)
request_params = StockBarsRequest(symbol_or_symbols=TICKER, timeframe=TimeFrame.Day, start=start_time)
bars = data_client.get_stock_bars(request_params).df

# Format pricing data for the prompt
market_summary = bars[['close', 'volume']].tail(3).to_string()

# 3. Prompt Claude for Decision
prompt = f"""
You are an algorithmic trading agent. Analyze the following recent data for {TICKER}:
{market_summary}

Respond STRICTLY in the following format with no other text:
ACTION: [BUY/SELL/HOLD]
REASON: [One short sentence explaining your logic]
"""

response = claude_client.messages.create(
    model="claude-opus-4-8",
    max_tokens=200,
    messages=[{"role": "user", "content": prompt}]
)

if response.stop_reason == "refusal":
    print("Claude declined to make a trading decision for this request. No orders placed.")
    decision_text = ""
else:
    decision_text = next((b.text for b in response.content if b.type == "text"), "")
    print(f"--- Claude's Assessment ---\n{decision_text}\n---------------------------")

# 4. Parse and Execute Order via Alpaca
if "ACTION: BUY" in decision_text:
    order_data = MarketOrderRequest(
        symbol=TICKER,
        qty=1,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY
    )
    trading_client.submit_order(order_data=order_data)
    print(f"Successfully placed BUY order for 1 share of {TICKER}.")
elif "ACTION: SELL" in decision_text:
    order_data = MarketOrderRequest(
        symbol=TICKER,
        qty=1,
        side=OrderSide.SELL,
        time_in_force=TimeInForce.DAY
    )
    trading_client.submit_order(order_data=order_data)
    print(f"Successfully placed SELL order for 1 share of {TICKER}.")
else:
    print("Action determined as HOLD. No orders placed.")
