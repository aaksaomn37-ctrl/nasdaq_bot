import os
import json
import asyncio
import yfinance as yf
import pandas as pd

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# =========================================================
# SETTINGS
# =========================================================

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

WATCHLIST_FILE = "watchlist.json"
STATE_FILE = "signal_state.json"

MAX_STOCKS = 20

INTERVAL = "5m"
PERIOD = "5d"

SCAN_WAIT_SECONDS = 60

# نسبة تقارب المتوسطات
EARLY_PROXIMITY = 0.006
FULL_PROXIMITY = 0.010

# ارتفاع الحجم مقارنة بمتوسط الحجم
VOLUME_FACTOR = 1.20


# =========================================================
# FILE HELPERS
# =========================================================

def load_json(filename, default):
    try:
        if os.path.exists(filename):
            with open(filename, "r") as f:
                return json.load(f)
    except Exception as e:
        print(f"Error reading {filename}: {e}")

    return default


def save_json(filename, data):
    try:
        with open(filename, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Error saving {filename}: {e}")


def load_watchlist():
    data = load_json(WATCHLIST_FILE, [])

    if not isinstance(data, list):
        return []

    return [str(x).upper().strip() for x in data if str(x).strip()]


def save_watchlist(stocks):
    save_json(WATCHLIST_FILE, stocks)


def load_state():
    return load_json(STATE_FILE, {})


def save_state(state):
    save_json(STATE_FILE, state)


# =========================================================
# TELEGRAM SECURITY
# =========================================================

def authorized(update: Update):
    if not update.effective_chat:
        return False

    if not CHAT_ID:
        return False

    return str(update.effective_chat.id) == str(CHAT_ID)


# =========================================================
# TELEGRAM COMMANDS
# =========================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return

    text = (
        "🤖 بوت مراقبة الأسهم جاهز\n\n"
        "الأوامر:\n"
        "/add NVDA - إضافة سهم\n"
        "/remove NVDA - حذف سهم\n"
        "/list - عرض القائمة\n"
        "/clear - مسح القائمة\n"
        "/status - حالة البوت\n\n"
        f"الحد الأقصى: {MAX_STOCKS} سهم"
    )

    await update.message.reply_text(text)


async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return

    if not context.args:
        await update.message.reply_text("اكتب مثلاً:\n/add NVDA")
        return

    symbol = context.args[0].upper().strip()

    if not symbol.replace("-", "").replace(".", "").isalnum():
        await update.message.reply_text("❌ رمز السهم غير صحيح.")
        return

    stocks = load_watchlist()

    if symbol in stocks:
        await update.message.reply_text(f"ℹ️ {symbol} موجود بالفعل في القائمة.")
        return

    if len(stocks) >= MAX_STOCKS:
        await update.message.reply_text(
            f"❌ وصلت للحد الأقصى وهو {MAX_STOCKS} سهم."
        )
        return

    # فحص أن الرمز يعطي بيانات
    try:
        test = await asyncio.to_thread(
            lambda: yf.Ticker(symbol).history(
                period="1d",
                interval="5m",
                auto_adjust=False
            )
        )

        if test is None or test.empty:
            await update.message.reply_text(
                f"❌ لم أجد بيانات للسهم {symbol}."
            )
            return

    except Exception:
        await update.message.reply_text(
            f"❌ تعذر التحقق من {symbol} الآن."
        )
        return

    stocks.append(symbol)
    save_watchlist(stocks)

    await update.message.reply_text(
        f"✅ تمت إضافة {symbol}\n"
        f"📊 عدد الأسهم: {len(stocks)}/{MAX_STOCKS}"
    )


async def remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return

    if not context.args:
        await update.message.reply_text("اكتب مثلاً:\n/remove NVDA")
        return

    symbol = context.args[0].upper().strip()

    stocks = load_watchlist()

    if symbol not in stocks:
        await update.message.reply_text(
            f"ℹ️ {symbol} غير موجود في القائمة."
        )
        return

    stocks.remove(symbol)
    save_watchlist(stocks)

    await update.message.reply_text(
        f"🗑 تم حذف {symbol}\n"
        f"📊 عدد الأسهم: {len(stocks)}/{MAX_STOCKS}"
    )


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return

    stocks = load_watchlist()

    if not stocks:
        await update.message.reply_text(
            "📋 القائمة فارغة.\n\n"
            "لإضافة سهم:\n/add NVDA"
        )
        return

    lines = ["📋 الأسهم التي تتم مراقبتها:\n"]

    for i, symbol in enumerate(stocks, 1):
        lines.append(f"{i}. {symbol}")

    lines.append(f"\n📊 العدد: {len(stocks)}/{MAX_STOCKS}")

    await update.message.reply_text("\n".join(lines))


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return

    save_watchlist([])

    await update.message.reply_text("🗑 تم مسح قائمة الأسهم.")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return

    stocks = load_watchlist()

    await update.message.reply_text(
        "🟢 البوت يعمل\n\n"
        f"📊 الأسهم: {len(stocks)}/{MAX_STOCKS}\n"
        f"⏱ الفريم: {INTERVAL}\n"
        f"🔄 الفحص: كل {SCAN_WAIT_SECONDS} ثانية"
    )


# =========================================================
# TECHNICAL ANALYSIS
# =========================================================

def calculate_vwap(df):
    typical_price = (
        df["High"] +
        df["Low"] +
        df["Close"]
    ) / 3

    volume = df["Volume"].replace(0, pd.NA)

    cumulative_volume = volume.cumsum()

    vwap = (
        (typical_price * volume).cumsum()
        / cumulative_volume
    )

    return vwap


def analyze_stock(symbol):

    try:
        df = yf.Ticker(symbol).history(
            period=PERIOD,
            interval=INTERVAL,
            auto_adjust=False
        )

        if df is None or df.empty or len(df) < 210:
            return None

        close = df["Close"]

        df["EMA9"] = close.ewm(span=9, adjust=False).mean()
        df["EMA21"] = close.ewm(span=21, adjust=False).mean()
        df["EMA50"] = close.ewm(span=50, adjust=False).mean()
        df["EMA100"] = close.ewm(span=100, adjust=False).mean()
        df["EMA200"] = close.ewm(span=200, adjust=False).mean()

        df["VWAP"] = calculate_vwap(df)

        df["VOL_AVG"] = df["Volume"].rolling(20).mean()

        row = df.iloc[-1]
        previous = df.iloc[-2]

        price = float(row["Close"])

        ema9 = float(row["EMA9"])
        ema21 = float(row["EMA21"])
        ema50 = float(row["EMA50"])
        ema100 = float(row["EMA100"])
        ema200 = float(row["EMA200"])
        vwap = float(row["VWAP"])

        volume = float(row["Volume"])
        volume_avg = float(row["VOL_AVG"])

        if price <= 0:
            return None

        # -------------------------------------------------
        # التقارب المبكر EMA 9 / 21 / 50
        # -------------------------------------------------

        early_values = [
            ema9,
            ema21,
            ema50
        ]

        early_spread = (
            max(early_values) -
            min(early_values)
        ) / price

        early_close = early_spread <= EARLY_PROXIMITY

        # -------------------------------------------------
        # التقارب الكامل EMA + VWAP
        # -------------------------------------------------

        full_values = [
            ema9,
            ema21,
            ema50,
            ema100,
            ema200,
            vwap
        ]

        full_spread = (
            max(full_values) -
            min(full_values)
        ) / price

        full_close = full_spread <= FULL_PROXIMITY

        # -------------------------------------------------
        # الاتجاه
        # -------------------------------------------------

        bullish = (
            ema9 > ema21 and
            ema21 > ema50 and
            price > vwap
        )

        bearish = (
            ema9 < ema21 and
            ema21 < ema50 and
            price < vwap
        )

        # ميل EMA9
        ema9_up = ema9 > float(previous["EMA9"])
        ema9_down = ema9 < float(previous["EMA9"])

        # -------------------------------------------------
        # السيولة والحجم
        # -------------------------------------------------

        volume_rising = (
            volume_avg > 0 and
            volume >= volume_avg * VOLUME_FACTOR
        )

        money_in = (
            volume_rising and
            price > float(previous["Close"])
        )

        money_out = (
            volume_rising and
            price < float(previous["Close"])
        )

        # -------------------------------------------------
        # الإشارات
        # -------------------------------------------------

        signal = "NONE"

        # شراء مؤكد
        if (
            full_close and
            bullish and
            ema9_up and
            volume_rising
        ):
            signal = "BUY"

        # بيع مؤكد
        elif (
            full_close and
            bearish and
            ema9_down and
            volume_rising
        ):
            signal = "SELL"

        # استعداد مبكر
        elif (
            early_close and
            bullish and
            ema9_up and
            money_in
        ):
            signal = "READY"

        # تحذير مبكر
        elif (
            early_close and
            bearish and
            ema9_down and
            money_out
        ):
            signal = "WARNING"

        return {
            "symbol": symbol,
            "price": price,
            "signal": signal,
            "volume": volume,
            "volume_avg": volume_avg,
            "early_spread": early_spread,
            "full_spread": full_spread
        }

    except Exception as e:
        print(f"{symbol} ERROR: {e}")
        return None


# =========================================================
# ALERT MESSAGE
# =========================================================

def make_message(result):

    symbol = result["symbol"]
    price = result["price"]
    signal = result["signal"]

    if signal == "READY":
        title = "استعداد 🔥"

    elif signal == "BUY":
        title = "شراء ✈️🟩"

    elif signal == "WARNING":
        title = "احذر 🪂"

    elif signal == "SELL":
        title = "بيع 💣"

    else:
        return None

    return (
        f"{title}\n"
        f"📈 {symbol}\n"
        f"💵 السعر: {price:.2f}\n"
        f"⏱ الفريم: {INTERVAL}"
    )


# =========================================================
# SCANNER
# =========================================================

async def scanner_loop(application):

    # انتظار تشغيل البوت
    await asyncio.sleep(10)

    print("🚀 Scanner started")

    state = load_state()

    while True:

        stocks = load_watchlist()

        if not stocks:
            print("Watchlist empty")

        for symbol in stocks:

            try:
                result = await asyncio.to_thread(
                    analyze_stock,
                    symbol
                )

                if not result:
                    continue

                signal = result["signal"]

                print(
                    symbol,
                    round(result["price"], 2),
                    signal
                )

                old_signal = state.get(symbol, "NONE")

                # إرسال التنبيه فقط عند ظهور إشارة جديدة
                if signal != "NONE" and signal != old_signal:

                    message = make_message(result)

                    if message:
                        await application.bot.send_message(
                            chat_id=CHAT_ID,
                            text=message
                        )

                state[symbol] = signal
                save_state(state)

            except Exception as e:
                print(f"Scanner error {symbol}: {e}")

            # مهلة بسيطة بين الأسهم
            await asyncio.sleep(1)

        print(
            f"⏳ Waiting {SCAN_WAIT_SECONDS} seconds..."
        )

        await asyncio.sleep(SCAN_WAIT_SECONDS)


# =========================================================
# START BOT
# =========================================================

async def post_init(application):
    asyncio.create_task(
        scanner_loop(application)
    )


def main():

    if not TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing"
        )

    if not CHAT_ID:
        raise RuntimeError(
            "TELEGRAM_CHAT_ID is missing"
        )

    application = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start_command)
    )

    application.add_handler(
        CommandHandler("add", add_command)
    )

    application.add_handler(
        CommandHandler("remove", remove_command)
    )

    application.add_handler(
        CommandHandler("list", list_command)
    )

    application.add_handler(
        CommandHandler("clear", clear_command)
    )

    application.add_handler(
        CommandHandler("status", status_command)
    )

    print("🤖 Telegram bot starting...")

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
