import os
import requests
from flask import Flask, render_template, request

app = Flask(__name__)

NEWS_API_KEY = os.getenv("NEWS_API_KEY")
BASE_URL     = "https://newsapi.org/v2"


def fetch_articles(endpoint: str, params: dict) -> tuple[list, str | None]:
    """Return (articles, error_message)."""
    if not NEWS_API_KEY:
        return [], "NEWS_API_KEY is not configured. Add it in the Secrets panel."
    try:
        params["apiKey"]   = NEWS_API_KEY
        params["pageSize"] = 9
        resp = requests.get(f"{BASE_URL}/{endpoint}", params=params, timeout=8)
        data = resp.json()
        if data.get("status") != "ok":
            return [], data.get("message", "Could not fetch news right now.")
        return data.get("articles", []), None
    except Exception as exc:
        return [], f"Network error: {exc}"


@app.route("/")
def index():
    articles, error = fetch_articles("top-headlines", {"language": "en"})
    return render_template("index.html",
                           articles=articles,
                           error=error,
                           active_category="Top Headlines")


@app.route("/kenya")
def kenya():
    articles, error = fetch_articles("top-headlines", {"country": "ke"})
    return render_template("index.html",
                           articles=articles,
                           error=error,
                           active_category="Kenya")


@app.route("/technology")
def technology():
    articles, error = fetch_articles("top-headlines",
                                     {"category": "technology", "language": "en"})
    return render_template("index.html",
                           articles=articles,
                           error=error,
                           active_category="Technology")


@app.route("/business")
def business():
    articles, error = fetch_articles("top-headlines",
                                     {"country": "ke", "category": "business"})
    return render_template("index.html",
                           articles=articles,
                           error=error,
                           active_category="Business")


@app.route("/sports")
def sports():
    articles, error = fetch_articles("top-headlines",
                                     {"country": "ke", "category": "sports"})
    return render_template("index.html",
                           articles=articles,
                           error=error,
                           active_category="Sports")


@app.route("/search")
def search():
    query = request.args.get("q", "").strip()
    if not query:
        return index()
    articles, error = fetch_articles("everything",
                                     {"q": query, "language": "en",
                                      "sortBy": "publishedAt"})
    return render_template("index.html",
                           articles=articles,
                           error=error,
                           active_category="Search",
                           search_query=query)


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8099))
    app.run(host="0.0.0.0", port=port, debug=False)
