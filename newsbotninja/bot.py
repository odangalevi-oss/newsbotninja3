import os
import json
import requests
import tempfile
import textwrap
from io import BytesIO
from threading import Thread
from gtts import gTTS
from PIL import Image, ImageDraw, ImageFont
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ── Keep-alive server ────────────────────────────────────────────────────────
keep_alive_app = Flask('')

@keep_alive_app.route('/')
def home():
    return "Newsbotninja is alive 🥷"

def run_keep_alive():
    keep_alive_app.run(host='0.0.0.0', port=8099)

Thread(target=run_keep_alive, daemon=True).start()

# ── Config ───────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
NEWS_API_KEY   = os.getenv("NEWS_API_KEY")
BOT_NAME       = "Newsbotninja 🥷"
SAVED_FILE     = "newsbotninja/saved.json"

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN secret is not set. Add it in the Secrets panel.")
if not NEWS_API_KEY:
    raise RuntimeError("NEWS_API_KEY secret is not set. Add it in the Secrets panel.")

# In-memory store of last-fetched articles per user
last_articles: dict[str, list] = {}

# ── Saved-article helpers ────────────────────────────────────────────────────
def load_saved() -> dict:
    if os.path.exists(SAVED_FILE):
        with open(SAVED_FILE, "r") as f:
            return json.load(f)
    return {}

def write_saved(data: dict):
    os.makedirs(os.path.dirname(SAVED_FILE), exist_ok=True)
    with open(SAVED_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ── Command handlers ─────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Habari! I am {BOT_NAME}\n\n"
        "Commands:\n"
        "/news - Top Kenya headlines\n"
        "/eldoret - Rift Valley news\n"
        "/sports - Kenya sports news\n"
        "/world - Top international headlines\n"
        "/tech - Latest technology news\n"
        "/business - Kenya business & finance news\n"
        "/health - Kenya health news\n"
        "/entertainment - Kenya entertainment news\n"
        "/voice - 20-second audio news brief 🎙️\n"
        "/save 1 - Save article 1 from last results\n"
        "/later - View your saved reading list\n"
        "/clear - Clear your reading list\n"
        "/card 1 - Generate a news card image 🎨\n"
        "/search Ruto - Search any topic\n"
        "/weather Nairobi - Current weather for any city 🌤"
    )

async def news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_news(update, "country=ke&pageSize=3")

async def eldoret(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_news(update, "q=Eldoret+OR+Rift+Valley&pageSize=3&language=en")

async def sports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_news(update, "country=ke&category=sports&pageSize=3")

async def world(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_news(update, "language=en&pageSize=3")

async def business(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_news(update, "country=ke&category=business&pageSize=3")

async def health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_news(update, "country=ke&category=health&pageSize=3")

async def entertainment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_news(update, "country=ke&category=entertainment&pageSize=3")

async def tech(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_news(update, "category=technology&language=en&pageSize=3")

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            f"{BOT_NAME}: Tell me what to search — e.g. /search Ruto or /search Premier League 🥷"
        )
        return
    query = "+".join(context.args)
    await send_news(update, f"q={query}&language=en&pageSize=3")

async def weather(update: Update, context: ContextTypes.DEFAULT_TYPE):
    city = " ".join(context.args) if context.args else "Nairobi"
    url  = f"https://wttr.in/{city.replace(' ', '+')}?format=j1"
    try:
        resp = requests.get(url, timeout=8)
        data = resp.json()
        c       = data["current_condition"][0]
        area    = data["nearest_area"][0]
        name    = area["areaName"][0]["value"]
        country = area["country"][0]["value"]
        temp    = c["temp_C"]
        feels   = c["FeelsLikeC"]
        desc    = c["weatherDesc"][0]["value"]
        humidity = c["humidity"]
        wind    = c["windspeedKmph"]
        msg = (
            f"🌤 *Weather in {name}, {country}*\n\n"
            f"🌡 Temperature: *{temp}°C* (feels like {feels}°C)\n"
            f"☁️ Condition: {desc}\n"
            f"💧 Humidity: {humidity}%\n"
            f"💨 Wind: {wind} km/h"
        )
    except Exception:
        msg = f"{BOT_NAME}: Could not get weather for *{city}* right now 🥷"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url  = f"https://newsapi.org/v2/top-headlines?country=ke&pageSize=3&apiKey={NEWS_API_KEY}"
    data = requests.get(url, timeout=10).json()

    if data["status"] != "ok" or not data["articles"]:
        await update.message.reply_text(f"{BOT_NAME}: Could not fetch news for voice brief 🥷")
        return

    script = "Newsbotninja briefing. Here are your top 3 Kenya stories today. "
    for i, art in enumerate(data["articles"], 1):
        title   = art["title"].split(" - ")[0]
        script += f"Story {i}. {title}. "

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        tts = gTTS(text=script, lang="en", slow=False)
        tts.save(f.name)
        await update.message.reply_voice(voice=open(f.name, "rb"))

async def save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id  = str(update.message.from_user.id)
    articles = last_articles.get(user_id, [])

    if not articles:
        await update.message.reply_text(
            f"{BOT_NAME}: Fetch some news first, then use /save 1, /save 2, or /save 3 🥷"
        )
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            f"{BOT_NAME}: Tell me which article — e.g. /save 1, /save 2 🥷"
        )
        return

    idx = int(context.args[0]) - 1
    if idx < 0 or idx >= len(articles):
        await update.message.reply_text(
            f"{BOT_NAME}: No article {idx + 1} in your last results. Try /save 1, /save 2, or /save 3 🥷"
        )
        return

    art   = articles[idx]
    saved = load_saved()
    user_list = saved.get(user_id, [])

    if any(a["url"] == art["url"] for a in user_list):
        await update.message.reply_text(f"{BOT_NAME}: Already saved that one! Check /later 🥷")
        return

    user_list.append({"title": art["title"], "url": art["url"]})
    saved[user_id] = user_list
    write_saved(saved)

    await update.message.reply_text(
        f"✅ Saved: *{art['title']}*\n\nType /later to see your full reading list.",
        parse_mode="Markdown"
    )

async def later(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id   = str(update.message.from_user.id)
    saved     = load_saved()
    user_list = saved.get(user_id, [])

    if not user_list:
        await update.message.reply_text(
            f"{BOT_NAME}: Your reading list is empty. Use /save 1 after fetching news 🥷"
        )
        return

    msg = f"📚 *Your Reading List — {len(user_list)} article(s):*\n\n"
    for i, art in enumerate(user_list, 1):
        msg += f"*{i}.* {art['title']}\n[Read →]({art['url']})\n\n"
    msg += "_Use /clear to empty your list._"

    await update.message.reply_text(msg, parse_mode="Markdown", disable_web_page_preview=True)

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id          = str(update.message.from_user.id)
    saved            = load_saved()
    saved[user_id]   = []
    write_saved(saved)
    await update.message.reply_text(f"{BOT_NAME}: Reading list cleared! 🥷")

# ── News-card image generator ────────────────────────────────────────────────
def make_news_card(title: str, source: str) -> BytesIO:
    W, H    = 900, 500
    BG      = (15, 15, 35)
    ACCENT  = (255, 165, 0)
    WHITE   = (255, 255, 255)
    SUBTEXT = (180, 180, 200)

    img  = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # Try system fonts, fall back to default
    try:
        FONT_BOLD   = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        FONT_NORMAL = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        font_brand    = ImageFont.truetype(FONT_BOLD,   28)
        font_headline = ImageFont.truetype(FONT_BOLD,   38)
        font_sub      = ImageFont.truetype(FONT_NORMAL, 22)
    except OSError:
        font_brand    = ImageFont.load_default()
        font_headline = ImageFont.load_default()
        font_sub      = ImageFont.load_default()

    draw.rectangle([0, 0, W, 6],          fill=ACCENT)
    draw.rectangle([0, H - 6, W, H],      fill=ACCENT)
    draw.text((40, 30),  "🥷 NEWSBOTNINJA", font=font_brand, fill=ACCENT)
    draw.line([(40, 72), (W - 40, 72)], fill=ACCENT, width=2)

    wrapped = textwrap.fill(title, width=32)
    draw.text((40, 110), wrapped, font=font_headline, fill=WHITE, spacing=12)

    draw.text((40,        H - 50), f"📰 {source}",       font=font_sub, fill=SUBTEXT)
    draw.text((W - 260,   H - 50), "t.me/Newsbotninja",  font=font_sub, fill=SUBTEXT)

    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

async def card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id  = str(update.message.from_user.id)
    articles = last_articles.get(user_id, [])

    if not articles:
        await update.message.reply_text(
            f"{BOT_NAME}: Fetch some news first, then use /card 1, /card 2 or /card 3 🥷"
        )
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            f"{BOT_NAME}: Tell me which article — e.g. /card 1, /card 2 🥷"
        )
        return

    idx = int(context.args[0]) - 1
    if idx < 0 or idx >= len(articles):
        await update.message.reply_text(
            f"{BOT_NAME}: No article {idx + 1}. Try /card 1, /card 2 or /card 3 🥷"
        )
        return

    art    = articles[idx]
    title  = art["title"].split(" - ")[0]
    source = (art.get("source") or {}).get("name", "News")

    await update.message.reply_text("🎨 Generating your news card...")
    img_buf = make_news_card(title, source)
    await update.message.reply_photo(photo=img_buf, caption=f"📰 {title}")

# ── Core news fetcher ────────────────────────────────────────────────────────
async def send_news(update: Update, query: str):
    url  = f"https://newsapi.org/v2/top-headlines?{query}&apiKey={NEWS_API_KEY}"
    data = requests.get(url, timeout=10).json()

    if data["status"] != "ok":
        await update.message.reply_text(
            f"{BOT_NAME}: Error — {data.get('message', 'News server down')}"
        )
        return

    if not data["articles"]:
        await update.message.reply_text(f"{BOT_NAME}: No news found right now 🥷")
        return

    user_id = str(update.message.from_user.id)
    last_articles[user_id] = data["articles"]

    msg = f"📰 *{BOT_NAME} Briefing:*\n\n"
    for i, art in enumerate(data["articles"], 1):
        title   = art["title"]
        desc    = art["description"] or title
        link    = art["url"]
        summary = " ".join(desc.split()[:18]) + "..."
        msg += f"*{i}. {title}*\n💡 {summary}\n[Read more]({link})\n\n"

    msg += "_Tap /save 1, /save 2, or /save 3 to bookmark an article._"
    await update.message.reply_text(msg, parse_mode="Markdown", disable_web_page_preview=True)

# ── Bot startup ──────────────────────────────────────────────────────────────
app = Application.builder().token(TELEGRAM_TOKEN).build()

app.add_handler(CommandHandler("start",         start))
app.add_handler(CommandHandler("news",          news))
app.add_handler(CommandHandler("eldoret",       eldoret))
app.add_handler(CommandHandler("sports",        sports))
app.add_handler(CommandHandler("world",         world))
app.add_handler(CommandHandler("tech",          tech))
app.add_handler(CommandHandler("business",      business))
app.add_handler(CommandHandler("health",        health))
app.add_handler(CommandHandler("entertainment", entertainment))
app.add_handler(CommandHandler("voice",         voice))
app.add_handler(CommandHandler("save",          save))
app.add_handler(CommandHandler("later",         later))
app.add_handler(CommandHandler("clear",         clear))
app.add_handler(CommandHandler("card",          card))
app.add_handler(CommandHandler("search",        search))
app.add_handler(CommandHandler("weather",       weather))

print(f"{BOT_NAME} is live 🥷")
app.run_polling()
