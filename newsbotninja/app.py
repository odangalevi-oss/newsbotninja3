import os
import requests
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

NEWS_API_KEY = os.getenv("NEWS_API_KEY")
BASE_URL     = "https://newsapi.org/v2"

# ── Core fetcher ─────────────────────────────────────────────────────────────
def fetch_articles(endpoint: str, params: dict) -> tuple[list, str | None]:
    """Fetch a single batch. Return (articles, error_message)."""
    if not NEWS_API_KEY:
        return [], "NEWS_API_KEY is not configured. Add it in the Secrets panel."
    try:
        params["apiKey"] = NEWS_API_KEY
        resp = requests.get(f"{BASE_URL}/{endpoint}", params=params, timeout=8)
        data = resp.json()
        if data.get("status") != "ok":
            return [], data.get("message", "Could not fetch news right now.")
        return data.get("articles", []), None
    except Exception as exc:
        return [], f"Network error: {exc}"


def get_news(page: int = 1) -> tuple[list, str | None]:
    """Fetch Kenya + US general headlines, merge, deduplicate by title."""
    kenya_articles,  err1 = fetch_articles(
        "top-headlines", {"country": "ke", "pageSize": 10, "page": page}
    )
    global_articles, err2 = fetch_articles(
        "top-headlines", {"country": "us", "category": "general", "pageSize": 10, "page": page}
    )
    error = err1 if err1 and err2 else None
    seen, merged = set(), []
    for article in kenya_articles + global_articles:
        key = (article.get("title") or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            merged.append(article)
    return merged, error


def render_news_page(articles, error, active_category, category_slug, search_query=None):
    """Shared template renderer — adds ticker headlines and slug."""
    ticker = [
        a["title"].split(" - ")[0]
        for a in articles[:6]
        if a.get("title")
    ]
    return render_template(
        "index.html",
        articles=articles,
        error=error,
        active_category=active_category,
        category_slug=category_slug,
        ticker_headlines=ticker,
        search_query=search_query,
    )


# ── Page routes ───────────────────────────────────────────────────────────────
@app.route("/")
def index():
    articles, error = get_news()
    return render_news_page(articles, error, "Top Headlines", "top")


@app.route("/category/kenya")
@app.route("/kenya")
def kenya():
    articles, error = fetch_articles("top-headlines", {"country": "ke", "pageSize": 10})
    return render_news_page(articles, error, "Kenya", "kenya")


@app.route("/technology")
def technology():
    articles, error = fetch_articles(
        "top-headlines", {"category": "technology", "language": "en", "pageSize": 10}
    )
    return render_news_page(articles, error, "Technology", "technology")


@app.route("/business")
def business():
    articles, error = fetch_articles(
        "top-headlines", {"country": "ke", "category": "business", "pageSize": 10}
    )
    return render_news_page(articles, error, "Business", "business")


@app.route("/sports")
def sports():
    articles, error = fetch_articles(
        "top-headlines", {"country": "ke", "category": "sports", "pageSize": 10}
    )
    return render_news_page(articles, error, "Sports", "sports")


@app.route("/search")
def search():
    query = request.args.get("q", "").strip()
    if not query:
        return index()
    articles, error = fetch_articles(
        "everything", {"q": query, "language": "en", "sortBy": "publishedAt", "pageSize": 10}
    )
    return render_news_page(articles, error, "Search", "search", search_query=query)


# ── JSON API ──────────────────────────────────────────────────────────────────
@app.route("/api/news")
def api_news():
    page     = request.args.get("page", 1, type=int)
    category = request.args.get("category", "top").lower()

    if category == "kenya":
        articles, error = fetch_articles(
            "top-headlines", {"country": "ke", "pageSize": 10, "page": page}
        )
    elif category == "technology":
        articles, error = fetch_articles(
            "top-headlines", {"category": "technology", "language": "en", "pageSize": 10, "page": page}
        )
    elif category == "business":
        articles, error = fetch_articles(
            "top-headlines", {"country": "ke", "category": "business", "pageSize": 10, "page": page}
        )
    elif category == "sports":
        articles, error = fetch_articles(
            "top-headlines", {"country": "ke", "category": "sports", "pageSize": 10, "page": page}
        )
    else:
        articles, error = get_news(page=page)

    return jsonify({"articles": articles, "error": error, "page": page})


@app.route("/api/weather")
def api_weather():
    city = request.args.get("city", "Nairobi")
    try:
        resp = requests.get(f"https://wttr.in/{city}?format=j1", timeout=5)
        data = resp.json()
        c    = data["current_condition"][0]
        desc = c["weatherDesc"][0]["value"]
        icon_map = {
            "sunny": "☀️", "clear": "🌙", "cloud": "☁️",
            "rain": "🌧️", "drizzle": "🌦️", "thunder": "⛈️",
            "snow": "❄️",  "mist": "🌫️",  "fog": "🌫️",
        }
        icon = next((em for kw, em in icon_map.items() if kw in desc.lower()), "🌤️")
        return jsonify({"temp": c["temp_C"], "desc": desc, "icon": icon, "city": city})
    except Exception:
        return jsonify({"error": "unavailable"})


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8099))
    app.run(host="0.0.0.0", port=port, debug=False)
