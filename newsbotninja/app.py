import os
import sqlite3
import requests
from datetime import datetime
from functools import wraps

from flask import (
    Flask, render_template, request, jsonify,
    redirect, url_for, flash
)
from flask_login import (
    LoginManager, UserMixin,
    login_user, logout_user, login_required, current_user
)
from flask_mail import Mail, Message
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.getenv("SESSION_SECRET", "dev-secret-change-me")

# ── Flask-Mail ────────────────────────────────────────────────────────────────
app.config["MAIL_SERVER"]         = os.getenv("MAIL_SERVER",  "smtp.gmail.com")
app.config["MAIL_PORT"]           = int(os.getenv("MAIL_PORT", "587"))
app.config["MAIL_USE_TLS"]        = os.getenv("MAIL_USE_TLS", "true").lower() == "true"
app.config["MAIL_USERNAME"]       = os.getenv("MAIL_USERNAME")
app.config["MAIL_PASSWORD"]       = os.getenv("MAIL_PASSWORD")
app.config["MAIL_DEFAULT_SENDER"] = os.getenv("MAIL_USERNAME")
mail = Mail(app)

# ── Flask-Login ───────────────────────────────────────────────────────────────
login_manager = LoginManager(app)
login_manager.login_view    = "login"
login_manager.login_message = "Please log in to access that page."

# ── NewsAPI ───────────────────────────────────────────────────────────────────
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
BASE_URL     = "https://newsapi.org/v2"
ADMIN_EMAIL  = "odangalevi@gmail.com"

# ── SQLite DB ─────────────────────────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(__file__), "users.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT    NOT NULL UNIQUE,
            email         TEXT    NOT NULL UNIQUE,
            password_hash TEXT    NOT NULL,
            is_admin      INTEGER NOT NULL DEFAULT 0,
            created_at    TEXT    DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()

init_db()

# ── User class ────────────────────────────────────────────────────────────────
class User(UserMixin):
    def __init__(self, id, username, email, is_admin, created_at=None):
        self.id         = id
        self.username   = username
        self.email      = email
        self.is_admin   = bool(is_admin)
        self.created_at = created_at

    def get_id(self):
        return str(self.id)

@login_manager.user_loader
def load_user(user_id):
    conn = get_db()
    row  = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    if row:
        return User(row["id"], row["username"], row["email"], row["is_admin"], row["created_at"])
    return None

# ── Admin decorator ───────────────────────────────────────────────────────────
def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash("Admin access required.", "error")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated

# ── Mail helper ───────────────────────────────────────────────────────────────
def send_admin_notification(new_username, new_email):
    if not app.config.get("MAIL_USERNAME") or not app.config.get("MAIL_PASSWORD"):
        print(f"[MAIL] Credentials not set — skipping notification for {new_email}")
        return
    try:
        msg = Message(
            subject=f"🥷 New Newsbotninja registration: {new_username}",
            recipients=[ADMIN_EMAIL],
            body=(
                f"A new user has registered on Newsbotninja 🥷\n\n"
                f"Username : {new_username}\n"
                f"Email    : {new_email}\n"
                f"Time     : {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n"
            ),
        )
        mail.send(msg)
    except Exception as exc:
        print(f"[MAIL] Failed to send notification: {exc}")

# ── Core news fetcher ─────────────────────────────────────────────────────────
def fetch_articles(endpoint: str, params: dict) -> tuple[list, str | None]:
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


@app.route("/health")
def health():
    articles, error = fetch_articles(
        "top-headlines", {"category": "health", "language": "en", "pageSize": 10}
    )
    return render_news_page(articles, error, "Health", "health")


@app.route("/entertainment")
def entertainment():
    articles, error = fetch_articles(
        "top-headlines", {"category": "entertainment", "language": "en", "pageSize": 10}
    )
    return render_news_page(articles, error, "Entertainment", "entertainment")


@app.route("/world")
def world():
    articles, error = fetch_articles(
        "top-headlines", {"category": "general", "language": "en", "pageSize": 20}
    )
    return render_news_page(articles, error, "World", "world")


@app.route("/science")
def science():
    articles, error = fetch_articles(
        "top-headlines", {"category": "science", "language": "en", "pageSize": 10}
    )
    return render_news_page(articles, error, "Science", "science")


@app.route("/search")
def search():
    query = request.args.get("q", "").strip()
    if not query:
        return index()
    articles, error = fetch_articles(
        "everything", {"q": query, "language": "en", "sortBy": "publishedAt", "pageSize": 10}
    )
    return render_news_page(articles, error, "Search", "search", search_query=query)


# ── Auth routes ───────────────────────────────────────────────────────────────
@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not username or not email or not password:
            flash("All fields are required.", "error")
            return render_template("register.html")

        if len(password) < 6:
            flash("Password must be at least 6 characters.", "error")
            return render_template("register.html")

        conn = get_db()
        existing = conn.execute(
            "SELECT id FROM users WHERE email = ? OR username = ?", (email, username)
        ).fetchone()

        if existing:
            conn.close()
            flash("Username or email already in use.", "error")
            return render_template("register.html")

        # First user ever → admin
        count    = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        is_admin = 1 if count == 0 else 0

        pw_hash = generate_password_hash(password)
        conn.execute(
            "INSERT INTO users (username, email, password_hash, is_admin) VALUES (?, ?, ?, ?)",
            (username, email, pw_hash, is_admin),
        )
        conn.commit()
        user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()

        user = User(user_id, username, email, is_admin)
        login_user(user)

        # Notify admin (skip if registrant IS admin)
        if not is_admin:
            send_admin_notification(username, email)

        flash(f"Welcome, {username}! {'You are the admin.' if is_admin else 'Account created successfully.'}", "success")
        return redirect(url_for("index"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        conn = get_db()
        row  = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        conn.close()

        if row and check_password_hash(row["password_hash"], password):
            user = User(row["id"], row["username"], row["email"], row["is_admin"])
            login_user(user, remember=True)
            flash(f"Welcome back, {user.username}!", "success")
            next_page = request.args.get("next")
            return redirect(next_page or url_for("index"))

        flash("Invalid email or password.", "error")

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You've been logged out.", "success")
    return redirect(url_for("index"))


# ── Admin routes ──────────────────────────────────────────────────────────────
@app.route("/admin")
@login_required
@admin_required
def admin():
    conn  = get_db()
    users = conn.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
    count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    return render_template("admin.html", users=users, user_count=count)


@app.route("/admin/delete/<int:user_id>", methods=["POST"])
@login_required
@admin_required
def admin_delete_user(user_id):
    if user_id == current_user.id:
        flash("You cannot delete your own account.", "error")
        return redirect(url_for("admin"))
    conn = get_db()
    row  = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
    if row:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        flash(f"User '{row['username']}' deleted.", "success")
    else:
        flash("User not found.", "error")
    conn.close()
    return redirect(url_for("admin"))


# ── JSON API ──────────────────────────────────────────────────────────────────
@app.route("/api/news")
def api_news():
    page     = request.args.get("page", 1, type=int)
    category = request.args.get("category", "top").lower()

    category_map = {
        "kenya":         ("top-headlines", {"country": "ke", "pageSize": 10, "page": page}),
        "technology":    ("top-headlines", {"category": "technology",    "language": "en", "pageSize": 10, "page": page}),
        "business":      ("top-headlines", {"country": "ke", "category": "business",      "pageSize": 10, "page": page}),
        "sports":        ("top-headlines", {"country": "ke", "category": "sports",        "pageSize": 10, "page": page}),
        "health":        ("top-headlines", {"category": "health",        "language": "en", "pageSize": 10, "page": page}),
        "entertainment": ("top-headlines", {"category": "entertainment", "language": "en", "pageSize": 10, "page": page}),
        "world":         ("top-headlines", {"category": "general",       "language": "en", "pageSize": 20, "page": page}),
        "science":       ("top-headlines", {"category": "science",       "language": "en", "pageSize": 10, "page": page}),
    }

    if category in category_map:
        endpoint, params = category_map[category]
        articles, error  = fetch_articles(endpoint, params)
    else:
        articles, error  = get_news(page=page)

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
            "rain": "🌧️",  "drizzle": "🌦️", "thunder": "⛈️",
            "snow": "❄️",  "mist": "🌫️",    "fog": "🌫️",
        }
        icon = next((em for kw, em in icon_map.items() if kw in desc.lower()), "🌤️")
        return jsonify({"temp": c["temp_C"], "desc": desc, "icon": icon, "city": city})
    except Exception:
        return jsonify({"error": "unavailable"})


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8099))
    app.run(host="0.0.0.0", port=port, debug=False)
