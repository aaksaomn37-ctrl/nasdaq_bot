import os
import json
import asyncio
import yfinance as yf
import pandas as pd
from dotenv import load_dotenv
from telegram import Bot

# =========================
# إعدادات البوت
# =========================
load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# الأسهم التي يفحصها البوت
STOCKS = [x.strip() for x in open("nasdaq_symbols.txt") if x.strip()]

# الفريم المستخدم للمضاربة
INTERVAL = "5m"
PERIOD = "5d"

# =========================
# نسب التقارب - قابلة للتعديل
# =========================

# تقارب EMA 9 / 21 / 50 لإشارة الاستعداد

READY_DISTANCE = 0.50

# تقارب EMA 9 / 21 / 50 / 100 / 200 + VWAP
STRONG_DISTANCE = 1.00

# ارتفاع الحجم مقارنة بمتوسط الحجم
VOLUME_MULTIPLIER = 1.30

STATE_FILE = "signal_state.json"


# =========================
# إرسال رسالة تيليجرام
# =========================
async def send_telegram(message):
    if not TOKEN or not CHAT_ID:
        print("❌ خطأ: TOKEN أو CHAT_ID غير موجود")
        return

    bot = Bot(token=TOKEN)

    await bot.send_message(
        chat_id=CHAT_ID,
        text=message
    )


# =========================
# حساب نسبة تقارب مجموعة قيم
# =========================
def convergence_percent(values):
    values = [float(v) for v in values if pd.notna(v)]

    if not values:
        return 999

    highest = max(values)
    lowest = min(values)
    middle = sum(values) / len(values)

    if middle == 0:
        return 999

    return ((highest - lowest) / middle) * 100


# =========================
# تحميل آخر حالة للإشارات
# لمنع تكرار نفس التنبيه
# =========================
def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception:
        pass


# =========================
# تحليل سهم واحد
# =========================
def analyze_stock(symbol):

    print(f"🔎 فحص {symbol}...")

    try:
        ticker = yf.Ticker(symbol)

        df = ticker.history(
            period=PERIOD,
            interval=INTERVAL,
            auto_adjust=False
        )

        if df.empty or len(df) < 50:
            print(f"⚠️ بيانات غير كافية لـ {symbol}")
            return None

        # تنظيف البيانات
        df = df.dropna(
            subset=["Open", "High", "Low", "Close", "Volume"]
        ).copy()

        if len(df) < 50:
            return None

        # =========================
        # المتوسطات
        # =========================
        df["EMA9"] = df["Close"].ewm(span=9, adjust=False).mean()
        df["EMA21"] = df["Close"].ewm(span=21, adjust=False).mean()
        df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
        df["EMA100"] = df["Close"].ewm(span=100, adjust=False).mean()
        df["EMA200"] = df["Close"].ewm(span=200, adjust=False).mean()

        # =========================
        # VWAP
        # =========================
        typical_price = (
            df["High"] +
            df["Low"] +
            df["Close"]
        ) / 3

        trading_day = pd.Series(
            df.index.date,
            index=df.index
        )

        cumulative_pv = (
            (typical_price * df["Volume"])
            .groupby(trading_day)
            .cumsum()
        )

        cumulative_volume = (
            df["Volume"]
            .groupby(trading_day)
            .cumsum()
        )

        df["VWAP"] = cumulative_pv / cumulative_volume.replace(0, pd.NA)

        # =========================
        # حجم التداول والسيولة
        # =========================
        df["VOL_AVG"] = df["Volume"].rolling(20).mean()

        last = df.iloc[-1]
        previous = df.iloc[-2]

        price = float(last["Close"])

        ema9 = float(last["EMA9"])
        ema21 = float(last["EMA21"])
        ema50 = float(last["EMA50"])
        ema100 = float(last["EMA100"])
        ema200 = float(last["EMA200"])
        vwap = float(last["VWAP"])

        volume = float(last["Volume"])

        vol_avg = last["VOL_AVG"]

        if pd.isna(vol_avg) or vol_avg == 0:
            volume_ratio = 0
        else:
            volume_ratio = volume / float(vol_avg)

        # =========================
        # التقارب
        # =========================
        ready_distance = convergence_percent(
            [ema9, ema21, ema50]
        )

        strong_distance = convergence_percent(
            [ema9, ema21, ema50, ema100, ema200, vwap]
        )

        # =========================
        # الاتجاه
        # =========================

        ema9_up = ema9 > float(previous["EMA9"])
        ema21_up = ema21 > float(previous["EMA21"])
        ema50_up = ema50 > float(previous["EMA50"])

        ema9_down = ema9 < float(previous["EMA9"])
        ema21_down = ema21 < float(previous["EMA21"])
        ema50_down = ema50 < float(previous["EMA50"])

        bullish_short = (
            ema9 >= ema21 >= ema50
            and ema9_up
            and ema21_up
            and ema50_up
        )

        bearish_short = (
            ema9 <= ema21 <= ema50
            and ema9_down
            and ema21_down
            and ema50_down
        )

        bullish_full = (
            ema9 >= ema21 >= ema50 >= ema100 >= ema200
            and price >= vwap
        )

        bearish_full = (
            ema9 <= ema21 <= ema50 <= ema100 <= ema200
            and price <= vwap
        )

        # دخول السيولة
        liquidity_in = (
            volume_ratio >= VOLUME_MULTIPLIER
            and price > float(previous["Close"])
        )

        # خروج السيولة
        liquidity_out = (
            volume_ratio >= VOLUME_MULTIPLIER
            and price < float(previous["Close"])
        )

        signal = "NONE"

        # =========================
        # شراء مؤكد ✈️
        # =========================
        if (
            strong_distance <= STRONG_DISTANCE
            and bullish_full
            and liquidity_in
        ):
            signal = "BUY"

        # =========================
        # بيع / اتجاه هابط 💣
        # =========================
        elif (
            strong_distance <= STRONG_DISTANCE
            and bearish_full
            and liquidity_out
        ):
            signal = "SELL"

        # =========================
        # استعداد مبكر 🔥
        # =========================
        elif (
            ready_distance <= READY_DISTANCE
            and bullish_short
            and liquidity_in
        ):
            signal = "READY"

        # =========================
        # احذر 🪂
        # =========================
        elif (
            ready_distance <= READY_DISTANCE
            and bearish_short
            and liquidity_out
        ):
            signal = "WARNING"

        return {
            "symbol": symbol,
            "price": price,
            "signal": signal,
            "ready_distance": ready_distance,
            "strong_distance": strong_distance,
            "volume_ratio": volume_ratio,
            "vwap": vwap
        }

    except Exception as e:
        print(f"❌ خطأ في {symbol}: {e}")
        return None


# =========================
# تجهيز رسالة الإشارة
# =========================
def build_message(result):

    symbol = result["symbol"]
    price = result["price"]
    signal = result["signal"]
    volume_ratio = result["volume_ratio"]
    ready_distance = result["ready_distance"]
    strong_distance = result["strong_distance"]

    if signal == "READY":

        return (
            f"🔥 استعداد\n"
            f"📈 {symbol}\n"
            f"💵 السعر: ${price:.2f}\n"
            f"📊 تقارب EMA 9/21/50: {ready_distance:.2f}%\n"
            f"💧 السيولة: {volume_ratio:.2f}x\n"
            f"⏱ الفريم: {INTERVAL}"
        )

    if signal == "BUY":

        return (
            f"✈️ شراء\n"
            f"📈 {symbol}\n"
            f"💵 السعر: ${price:.2f}\n"
            f"🎯 تقارب المتوسطات + VWAP: {strong_distance:.2f}%\n"
            f"💧 السيولة: {volume_ratio:.2f}x\n"
            f"⏱ الفريم: {INTERVAL}"
        )

    if signal == "WARNING":

        return (
            f"🪂 احذر\n"
            f"📉 {symbol}\n"
            f"💵 السعر: ${price:.2f}\n"
            f"📊 تقارب EMA 9/21/50: {ready_distance:.2f}%\n"
            f"💧 خروج سيولة: {volume_ratio:.2f}x\n"
            f"⏱ الفريم: {INTERVAL}"
        )

    if signal == "SELL":

        return (
            f"💣 بيع\n"
            f"📉 {symbol}\n"
            f"💵 السعر: ${price:.2f}\n"
            f"🎯 تقارب المتوسطات + VWAP: {strong_distance:.2f}%\n"
            f"💧 خروج سيولة: {volume_ratio:.2f}x\n"
            f"⏱ الفريم: {INTERVAL}"
        )

    return None


# =========================
# تشغيل الفحص
# =========================
async def main():

    print("🚀 بدء فحص الأسهم...")
    print("-----------------------------")

    state = load_state()

    for symbol in STOCKS:

        result = analyze_stock(symbol)

        if result is None:
            continue

        signal = result["signal"]

        print(
            f"{symbol} | "
            f"${result['price']:.2f} | "
            f"Signal: {signal} | "
            f"Volume: {result['volume_ratio']:.2f}x"
        )

        old_signal = state.get(symbol, "NONE")

        # إرسال التنبيه فقط عندما تتغير الإشارة
        if signal != "NONE" and signal != old_signal:

            message = build_message(result)

            if message:
                await send_telegram(message)
                print(f"📨 تم إرسال تنبيه {symbol}")

        state[symbol] = signal

    save_state(state)

    print("-----------------------------")
    print("✅ انتهى الفحص")


async def run_forever():
    while True:
        await main()
        print("⏳ انتظار 60 ثانية للفحص التالي...")
        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(run_forever())

