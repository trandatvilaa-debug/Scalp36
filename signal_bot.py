import os
import requests
import pandas as pd
from datetime import datetime

# ═══════════════════════════════════════════
#   CONFIG
# ═══════════════════════════════════════════
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
SYMBOL           = os.environ.get("SYMBOL", "BTCUSDT")

# ═══════════════════════════════════════════
#   SETTINGS
# ═══════════════════════════════════════════
EMA_FAST = 9
EMA_SLOW = 21
RSI_LEN  = 14
RSI_OB   = 70
RSI_OS   = 30
RSI_MID  = 50
ATR_LEN  = 14
TP_MULT  = 2.0
SL_MULT  = 1.0
LIMIT    = 150

TF_15M = "15m"
TF_4H  = "4h"

BASE_URL = "https://testnet.binancefuture.com"

# ═══════════════════════════════════════════
#   FETCH DATA
# ═══════════════════════════════════════════
def get_klines(symbol, interval, limit=150):
    url = f"{BASE_URL}/fapi/v1/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()
    df = pd.DataFrame(data, columns=[
        "open_time","open","high","low","close","volume",
        "close_time","qav","num_trades","taker_base","taker_quote","ignore"
    ])
    for col in ["open","high","low","close","volume"]:
        df[col] = df[col].astype(float)
    return df

# ═══════════════════════════════════════════
#   INDICATORS (pure pandas, không cần TA lib)
# ═══════════════════════════════════════════
def calc_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def calc_rsi(series, period=14):
    delta    = series.diff()
    gain     = delta.clip(lower=0)
    loss     = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs  = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calc_atr(df, period=14):
    high       = df["high"]
    low        = df["low"]
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, adjust=False).mean()

def calc_indicators(df):
    df = df.copy()
    df["ema_fast"] = calc_ema(df["close"], EMA_FAST)
    df["ema_slow"] = calc_ema(df["close"], EMA_SLOW)
    df["rsi"]      = calc_rsi(df["close"], RSI_LEN)
    df["atr"]      = calc_atr(df, ATR_LEN)
    return df

# ═══════════════════════════════════════════
#   SIGNAL LOGIC
# ═══════════════════════════════════════════
def get_signal(df_15m, df_4h):
    df_15m = calc_indicators(df_15m)
    df_4h  = calc_indicators(df_4h)

    prev  = df_15m.iloc[-2]
    curr  = df_15m.iloc[-1]
    h4    = df_4h.iloc[-1]

    price = curr["close"]
    rsi   = curr["rsi"]
    atr   = curr["atr"]

    cross_up   = prev["ema_fast"] <= prev["ema_slow"] and curr["ema_fast"] > curr["ema_slow"]
    cross_down = prev["ema_fast"] >= prev["ema_slow"] and curr["ema_fast"] < curr["ema_slow"]

    htf_bull = h4["ema_fast"] > h4["ema_slow"]
    htf_bear = h4["ema_fast"] < h4["ema_slow"]
    htf_rsi  = h4["rsi"]

    buy_signal  = cross_up   and rsi > RSI_MID and rsi < RSI_OB and htf_bull
    sell_signal = cross_down and rsi < RSI_MID and rsi > RSI_OS and htf_bear

    if buy_signal:
        return "BUY",  price, price + atr * TP_MULT, price - atr * SL_MULT, rsi, htf_rsi, htf_bull
    if sell_signal:
        return "SELL", price, price - atr * TP_MULT, price + atr * SL_MULT, rsi, htf_rsi, htf_bull
    return None, price, None, None, rsi, htf_rsi, htf_bull

# ═══════════════════════════════════════════
#   TELEGRAM
# ═══════════════════════════════════════════
def send_telegram(msg):
    url     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}
    requests.post(url, json=payload, timeout=10).raise_for_status()

# ═══════════════════════════════════════════
#   MAIN
# ═══════════════════════════════════════════
def main():
    now = datetime.utcnow().strftime("%H:%M UTC")
    print(f"[{now}] Checking {SYMBOL}...")

    try:
        df_15m = get_klines(SYMBOL, TF_15M, LIMIT)
        df_4h  = get_klines(SYMBOL, TF_4H,  LIMIT)
    except Exception as e:
        print(f"Lỗi fetch data: {e}")
        return

    signal, price, tp, sl, rsi, htf_rsi, htf_bull = get_signal(df_15m, df_4h)

    htf_label = f"{'🟢 BULL' if htf_bull else '🔴 BEAR'} | RSI {htf_rsi:.1f}"

    if signal == "BUY":
        pct_tp = (tp - price) / price * 100
        pct_sl = (price - sl) / price * 100
        msg = (
            f"🟢 <b>BUY — {SYMBOL}</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"💰 Entry : <b>{price:.2f}</b>\n"
            f"🎯 TP    : <b>{tp:.2f}</b> (+{pct_tp:.2f}%)\n"
            f"🛑 SL    : <b>{sl:.2f}</b> (-{pct_sl:.2f}%)\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📊 RSI 15m : {rsi:.1f}\n"
            f"🔭 HTF 4H  : {htf_label}\n"
            f"⏰ {now}"
        )
        send_telegram(msg)
        print(f"✅ BUY sent! Entry:{price:.2f} TP:{tp:.2f} SL:{sl:.2f}")

    elif signal == "SELL":
        pct_tp = (price - tp) / price * 100
        pct_sl = (sl - price) / price * 100
        msg = (
            f"🔴 <b>SELL — {SYMBOL}</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"💰 Entry : <b>{price:.2f}</b>\n"
            f"🎯 TP    : <b>{tp:.2f}</b> (-{pct_tp:.2f}%)\n"
            f"🛑 SL    : <b>{sl:.2f}</b> (+{pct_sl:.2f}%)\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📊 RSI 15m : {rsi:.1f}\n"
            f"🔭 HTF 4H  : {htf_label}\n"
            f"⏰ {now}"
        )
        send_telegram(msg)
        print(f"✅ SELL sent! Entry:{price:.2f} TP:{tp:.2f} SL:{sl:.2f}")

    else:
        print(f"No signal | Price:{price:.2f} | RSI:{rsi:.1f} | HTF:{'BULL' if htf_bull else 'BEAR'} {htf_rsi:.1f}")

if __name__ == "__main__":
    main()
