import ccxt
import pandas as pd
import pandas_ta as ta
import time
import asyncio
from telegram import Bot

# --- CONFIGURATION ---
TELEGRAM_BOT_TOKEN = 'YOUR_TELEGRAM_BOT_TOKEN'
TELEGRAM_CHAT_ID = 'YOUR_CHAT_ID' # Can be your personal ID or a channel ID
EXCHANGE_ID = 'binance' # using binance
TIMEFRAME = '15m'
LIMIT = 100             # Candles to fetch
TOP_N = 25              # Top coins to check
SL_LOOKBACK = 10        # Swing low/high lookback candles
RISK_REWARD = 2.0       # 1:2 RR

# Strategy Settings
KEY_VALUE = 2.0
ATR_PERIOD = 10

# Initialize Exchange (Binance Futures)
exchange = ccxt.binance({
    'options': {'defaultType': 'future'}, # Change to 'spot' if you want spot market
    'enableRateLimit': True
})

# Initialize Telegram Bot
bot = Bot(token=TELEGRAM_BOT_TOKEN)

async def send_telegram_message(message):
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
    except Exception as e:
        print(f"Telegram Error: {e}")

def get_top_gainers():
    """Fetches top 25 coins by 24h change from Binance"""
    try:
        tickers = exchange.fetch_tickers()
        # Filter for USDT pairs only and remove ignores
        valid_tickers = [
            symbol for symbol, data in tickers.items() 
            if '/USDT' in symbol and 'UP/' not in symbol and 'DOWN/' not in symbol
        ]
        
        # Sort by percentage change (descending)
        sorted_tickers = sorted(
            valid_tickers, 
            key=lambda x: tickers[x]['percentage'] if tickers[x]['percentage'] else -100, 
            reverse=True
        )
        
        return sorted_tickers[:TOP_N]
    except Exception as e:
        print(f"Error fetching top gainers: {e}")
        return []

def calculate_strategy(df):
    """Applies UT Bot + Heikin Ashi + Swing Logic"""
    # 1. Heikin Ashi Calculation
    df['HA_Close'] = (df['open'] + df['high'] + df['low'] + df['close']) / 4
    
    # HA Open requires loop
    ha_open = [ (df['open'].iloc[0] + df['close'].iloc[0]) / 2 ]
    for i in range(1, len(df)):
        ha_open.append((ha_open[i-1] + df['HA_Close'].iloc[i-1]) / 2)
    df['HA_Open'] = ha_open
    
    # 2. ATR & UT Bot
    # Use HA values for smoother ATR? Standard practice is usually Real High/Low for ATR to measure true volatility.
    # We will use Real candles for ATR to ensure Stop Loss distance is realistic to volatility.
    df['ATR'] = ta.atr(df['high'], df['low'], df['close'], length=ATR_PERIOD)
    df['nLoss'] = KEY_VALUE * df['ATR']

    # 3. Trailing Stop Loop
    trailing_stop = [0.0] * len(df)
    for i in range(1, len(df)):
        prev_stop = trailing_stop[i-1]
        price = df['HA_Close'].iloc[i]
        prev_price = df['HA_Close'].iloc[i-1]
        n_loss = df['nLoss'].iloc[i]

        if (price > prev_stop) and (prev_price > prev_stop):
            trailing_stop[i] = max(prev_stop, price - n_loss)
        elif (price < prev_stop) and (prev_price < prev_stop):
            trailing_stop[i] = min(prev_stop, price + n_loss)
        elif (price > prev_stop):
            trailing_stop[i] = price - n_loss
        else:
            trailing_stop[i] = price + n_loss
    
    df['TrailingStop'] = trailing_stop

    # 4. Generate Signals (Check last closed candle vs previous)
    # We look at index -2 (previous) and -1 (last closed) to detect CROSS
    last_row = df.iloc[-1]
    prev_row = df.iloc[-2]

    signal = None
    stop_loss = 0.0
    take_profit = 0.0
    entry_price = last_row['close'] # We enter at real market price

    # Buy Signal: HA Close crossed ABOVE Trailing Stop
    if (last_row['HA_Close'] > last_row['TrailingStop']) and (prev_row['HA_Close'] <= prev_row['TrailingStop']):
        signal = 'LONG'
        # SL = Lowest Low of last X candles
        # We look back SL_LOOKBACK periods from the signal candle
        recent_lows = df['low'].iloc[-SL_LOOKBACK:]
        stop_loss = recent_lows.min()
        
        # Calculate TP (1:2 Risk Reward)
        risk = entry_price - stop_loss
        take_profit = entry_price + (risk * RISK_REWARD)

    # Sell Signal: HA Close crossed BELOW Trailing Stop
    elif (last_row['HA_Close'] < last_row['TrailingStop']) and (prev_row['HA_Close'] >= prev_row['TrailingStop']):
        signal = 'SHORT'
        # SL = Highest High of last X candles
        recent_highs = df['high'].iloc[-SL_LOOKBACK:]
        stop_loss = recent_highs.max()
        
        # Calculate TP (1:2 Risk Reward)
        risk = stop_loss - entry_price
        take_profit = entry_price - (risk * RISK_REWARD)

    return signal, entry_price, stop_loss, take_profit

async def run_scanner():
    print(f"--- Starting Scan at {pd.Timestamp.now()} ---")
    top_coins = get_top_gainers()
    print(f"Scanning Top {len(top_coins)} coins: {top_coins}")

    for symbol in top_coins:
        try:
            # Fetch OHLCV Data
            bars = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=LIMIT)
            df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            
            if len(df) < LIMIT: continue # Skip if not enough data

            # Analyze
            signal, entry, sl, tp = calculate_strategy(df)

            if signal:
                # Format Alert Message
                emoji = "🟢" if signal == 'LONG' else "🔴"
                msg = (
                    f"{emoji} **UT BOT ALERT: {symbol}**\n"
                    f"Signal: **{signal}**\n"
                    f"Timeframe: 15m\n\n"
                    f"Entry: {entry:.4f}\n"
                    f"Stop Loss: {sl:.4f} (Swing {SL_LOOKBACK})\n"
                    f"Take Profit: {tp:.4f} (1:{int(RISK_REWARD)})\n"
                    f"Current Price: {entry:.4f}"
                )
                print(f"Signal found for {symbol}!")
                await send_telegram_message(msg)
            
            # Rate limit to avoid banning
            time.sleep(0.1) 

        except Exception as e:
            print(f"Error analyzing {symbol}: {e}")

    print("--- Scan Complete. Waiting for next candle... ---")

    except KeyboardInterrupt:
        print("Bot stopped by user.")
