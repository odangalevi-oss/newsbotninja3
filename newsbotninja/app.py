import os
import sqlite3
import secrets as _secrets
import requests
from datetime import datetime
from functools import wraps

from flask import (
    Flask, render_template, request, jsonify,
    redirect, url_for, flash, send_from_directory
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS article_clicks (
            url          TEXT PRIMARY KEY,
            title        TEXT,
            click_count  INTEGER NOT NULL DEFAULT 0,
            last_clicked TEXT    DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS newsletter_subscribers (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            email         TEXT    NOT NULL UNIQUE,
            name          TEXT,
            token         TEXT    NOT NULL UNIQUE,
            subscribed_at TEXT    DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS comments (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            article_url   TEXT    NOT NULL,
            article_title TEXT,
            user_id       INTEGER,
            username      TEXT    NOT NULL,
            body          TEXT    NOT NULL,
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

# ── Token helper ─────────────────────────────────────────────────────────────
def generate_token():
    return _secrets.token_urlsafe(32)


# ── Context processor (makes sub_count available in all templates) ────────────
@app.context_processor
def inject_globals():
    conn = get_db()
    sub_count = conn.execute("SELECT COUNT(*) FROM newsletter_subscribers").fetchone()[0]
    conn.close()
    return {"sub_count": sub_count}


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
    query    = request.args.get("q", "").strip()
    sort_by  = request.args.get("sortBy", "publishedAt")
    date_range = request.args.get("dateRange", "")
    category   = request.args.get("category", "")

    if not query:
        return redirect(url_for("index"))

    if sort_by not in ("publishedAt", "relevancy", "popularity"):
        sort_by = "publishedAt"

    from datetime import timedelta
    params = {
        "q":        f"{query} {category}".strip() if category else query,
        "language": "en",
        "sortBy":   sort_by,
        "pageSize": 20,
    }

    if date_range == "today":
        params["from"] = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
    elif date_range == "week":
        params["from"] = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
    elif date_range == "month":
        params["from"] = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")

    articles, error = fetch_articles("everything", params)
    return render_template(
        "index.html",
        articles=articles,
        error=error,
        active_category="Search",
        category_slug="search",
        ticker_headlines=[],
        search_query=query,
        search_sort=sort_by,
        search_date=date_range,
        search_category=category,
    )


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


# ── Admin: delete user ────────────────────────────────────────────────────────
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


# ── Click tracking ────────────────────────────────────────────────────────────
@app.route("/api/track", methods=["POST"])
def track_click():
    data  = request.get_json(silent=True) or {}
    url   = (data.get("url") or "").strip()
    title = (data.get("title") or "").strip()
    if not url:
        return jsonify({"error": "url required"}), 400
    conn = get_db()
    conn.execute("""
        INSERT INTO article_clicks (url, title, click_count, last_clicked)
        VALUES (?, ?, 1, datetime('now'))
        ON CONFLICT(url) DO UPDATE SET
            click_count  = click_count + 1,
            last_clicked = datetime('now'),
            title        = excluded.title
    """, (url, title))
    conn.commit()
    row = conn.execute(
        "SELECT click_count FROM article_clicks WHERE url = ?", (url,)
    ).fetchone()
    conn.close()
    return jsonify({"ok": True, "count": row["click_count"] if row else 1})


@app.route("/api/trending")
def api_trending():
    limit = request.args.get("limit", 30, type=int)
    conn  = get_db()
    rows  = conn.execute(
        "SELECT url, title, click_count FROM article_clicks ORDER BY click_count DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return jsonify([
        {"url": r["url"], "title": r["title"], "count": r["click_count"]}
        for r in rows
    ])


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


# ── Newsletter ────────────────────────────────────────────────────────────────
def send_newsletter_digest(articles):
    conn        = get_db()
    subscribers = conn.execute("SELECT email, name, token FROM newsletter_subscribers").fetchall()
    conn.close()
    if not subscribers:
        return 0, "No subscribers yet"
    if not app.config.get("MAIL_USERNAME") or not app.config.get("MAIL_PASSWORD"):
        return 0, "Email credentials not configured — add MAIL_USERNAME and MAIL_PASSWORD in Render env vars"

    items_html = ""
    for i, a in enumerate(articles[:5], 1):
        title = (a.get("title") or "").split(" - ")[0]
        desc  = (a.get("description") or "")[:120]
        url   = a.get("url") or "#"
        src   = (a.get("source") or {}).get("name", "")
        items_html += f"""
        <tr>
          <td style="padding:16px 0;border-bottom:1px solid #1e2d45;">
            <p style="color:#64748b;font-size:11px;margin:0 0 4px;">{i}. {src}</p>
            <a href="{url}" style="color:#14b8a6;font-size:16px;font-weight:600;text-decoration:none;line-height:1.4;">{title}</a>
            <p style="color:#94a3b8;font-size:13px;margin:6px 0 0;">{desc}{"…" if len(a.get("description",""))>120 else ""}</p>
          </td>
        </tr>"""

    sent = 0
    for sub in subscribers:
        unsub_url = f"https://newsbotninja.onrender.com/newsletter/unsubscribe/{sub['token']}"
        greeting  = f", {sub['name']}" if sub["name"] else ""
        html_body = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#0d1117;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0d1117;padding:40px 20px;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">
        <tr>
          <td style="background:linear-gradient(135deg,#0f1923,#0a2436);padding:32px;border-radius:12px 12px 0 0;text-align:center;border-bottom:2px solid #14b8a6;">
            <h1 style="color:#14b8a6;margin:0;font-size:28px;letter-spacing:-0.5px;">🥷 Newsbotninja</h1>
            <p style="color:#64748b;margin:8px 0 0;font-size:14px;">Your daily trending digest</p>
          </td>
        </tr>
        <tr>
          <td style="background:#0f1923;padding:32px;border-radius:0 0 12px 12px;">
            <h2 style="color:#f1f5f9;margin:0 0 24px;font-size:18px;">🔥 Trending Now{greeting}</h2>
            <table width="100%" cellpadding="0" cellspacing="0">{items_html}</table>
            <div style="text-align:center;margin-top:32px;">
              <a href="https://newsbotninja.onrender.com"
                 style="background:#14b8a6;color:#0d1117;padding:14px 32px;border-radius:50px;text-decoration:none;font-weight:700;font-size:14px;display:inline-block;">
                Read All Stories →
              </a>
            </div>
            <p style="color:#334155;font-size:12px;text-align:center;margin-top:32px;border-top:1px solid #1e2d45;padding-top:20px;">
              You're receiving this because you subscribed to Newsbotninja.<br>
              <a href="{unsub_url}" style="color:#475569;">Unsubscribe</a>
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body></html>"""
        try:
            msg = Message(
                subject="🔥 Trending on Newsbotninja today",
                recipients=[sub["email"]],
                html=html_body,
            )
            mail.send(msg)
            sent += 1
        except Exception as exc:
            print(f"[NEWSLETTER] Failed → {sub['email']}: {exc}")
    return sent, None


@app.route("/newsletter/subscribe", methods=["POST"])
def newsletter_subscribe():
    if request.is_json:
        data  = request.get_json(silent=True) or {}
        email = (data.get("email") or "").strip().lower()
        name  = (data.get("name")  or "").strip()
    else:
        email = (request.form.get("email") or "").strip().lower()
        name  = (request.form.get("name")  or "").strip()

    if not email or "@" not in email:
        if request.is_json:
            return jsonify({"error": "Valid email required"}), 400
        flash("Please enter a valid email address.", "error")
        return redirect(request.referrer or url_for("index"))

    conn     = get_db()
    existing = conn.execute(
        "SELECT id FROM newsletter_subscribers WHERE email = ?", (email,)
    ).fetchone()
    if existing:
        conn.close()
        if request.is_json:
            return jsonify({"ok": True, "message": "Already subscribed!"})
        flash("You're already subscribed! 🎉", "success")
        return redirect(request.referrer or url_for("index"))

    token = generate_token()
    conn.execute(
        "INSERT INTO newsletter_subscribers (email, name, token) VALUES (?, ?, ?)",
        (email, name, token)
    )
    conn.commit()
    conn.close()

    if request.is_json:
        return jsonify({"ok": True, "message": "Subscribed! 🔥"})
    flash(f"Subscribed! 🔥 Trending news will land in {email}", "success")
    return redirect(request.referrer or url_for("index"))


@app.route("/newsletter/unsubscribe/<token>")
def newsletter_unsubscribe(token):
    conn = get_db()
    row  = conn.execute(
        "SELECT email FROM newsletter_subscribers WHERE token = ?", (token,)
    ).fetchone()
    if row:
        conn.execute("DELETE FROM newsletter_subscribers WHERE token = ?", (token,))
        conn.commit()
        flash("You've been unsubscribed from the Newsbotninja digest. 👋", "success")
    else:
        flash("That unsubscribe link is invalid or already used.", "error")
    conn.close()
    return redirect(url_for("index"))


# ── Comments ──────────────────────────────────────────────────────────────────
@app.route("/api/comments", methods=["GET", "POST"])
def api_comments():
    if request.method == "POST":
        if not current_user.is_authenticated:
            return jsonify({"error": "Login required to comment"}), 401
        data          = request.get_json(silent=True) or {}
        article_url   = (data.get("url")   or "").strip()
        article_title = (data.get("title") or "").strip()
        body          = (data.get("body")  or "").strip()
        if not article_url or not body:
            return jsonify({"error": "url and body are required"}), 400
        if len(body) > 1000:
            return jsonify({"error": "Comment too long (max 1000 chars)"}), 400
        conn = get_db()
        conn.execute("""
            INSERT INTO comments (article_url, article_title, user_id, username, body)
            VALUES (?, ?, ?, ?, ?)
        """, (article_url, article_title, current_user.id, current_user.username, body))
        conn.commit()
        cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        return jsonify({
            "ok": True,
            "comment": {
                "id": cid,
                "username": current_user.username,
                "body": body,
                "created_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
            }
        })
    # GET
    url   = request.args.get("url", "").strip()
    limit = request.args.get("limit", 30, type=int)
    if not url:
        return jsonify({"error": "url required"}), 400
    conn = get_db()
    rows = conn.execute("""
        SELECT id, username, body, created_at FROM comments
        WHERE article_url = ? ORDER BY created_at ASC LIMIT ?
    """, (url, limit)).fetchall()
    conn.close()
    return jsonify([
        {"id": r["id"], "username": r["username"], "body": r["body"],
         "created_at": r["created_at"][:16]}
        for r in rows
    ])


@app.route("/api/comments/counts")
def api_comment_counts():
    """Batch comment counts for up to 30 article URLs."""
    urls = request.args.getlist("url")[:30]
    if not urls:
        return jsonify({})
    conn   = get_db()
    counts = {}
    for url in urls:
        row = conn.execute(
            "SELECT COUNT(*) as c FROM comments WHERE article_url = ?", (url,)
        ).fetchone()
        counts[url] = row["c"] if row else 0
    conn.close()
    return jsonify(counts)


# ── Updated admin routes ───────────────────────────────────────────────────────
@app.route("/admin")
@login_required
@admin_required
def admin():
    conn        = get_db()
    users       = conn.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
    user_count  = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    sub_count_v = conn.execute("SELECT COUNT(*) FROM newsletter_subscribers").fetchone()[0]
    cmt_count   = conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
    conn.close()
    return render_template("admin.html", tab="users",
                           users=users, user_count=user_count,
                           subscriber_count=sub_count_v, comment_count=cmt_count,
                           comments=[], subscribers=[])


@app.route("/admin/comments")
@login_required
@admin_required
def admin_comments():
    conn     = get_db()
    comments = conn.execute("""
        SELECT id, article_url, article_title, username, body, created_at
        FROM comments ORDER BY created_at DESC LIMIT 300
    """).fetchall()
    cmt_count  = conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
    sub_count_v= conn.execute("SELECT COUNT(*) FROM newsletter_subscribers").fetchone()[0]
    user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    return render_template("admin.html", tab="comments",
                           comments=comments, comment_count=cmt_count,
                           subscriber_count=sub_count_v, user_count=user_count,
                           users=[], subscribers=[])


@app.route("/admin/comments/<int:cid>/delete", methods=["POST"])
@login_required
@admin_required
def admin_delete_comment(cid):
    conn = get_db()
    conn.execute("DELETE FROM comments WHERE id = ?", (cid,))
    conn.commit()
    conn.close()
    flash("Comment deleted.", "success")
    return redirect(url_for("admin_comments"))


@app.route("/admin/newsletter")
@login_required
@admin_required
def admin_newsletter():
    conn        = get_db()
    subscribers = conn.execute(
        "SELECT * FROM newsletter_subscribers ORDER BY subscribed_at DESC"
    ).fetchall()
    sub_count_v = conn.execute("SELECT COUNT(*) FROM newsletter_subscribers").fetchone()[0]
    cmt_count   = conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
    user_count  = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    return render_template("admin.html", tab="newsletter",
                           subscribers=subscribers, subscriber_count=sub_count_v,
                           comment_count=cmt_count, user_count=user_count,
                           users=[], comments=[])


@app.route("/admin/newsletter/send", methods=["POST"])
@login_required
@admin_required
def admin_newsletter_send():
    conn     = get_db()
    top_rows = conn.execute(
        "SELECT url, title FROM article_clicks ORDER BY click_count DESC LIMIT 5"
    ).fetchall()
    conn.close()
    if not top_rows:
        flash("No trending articles tracked yet — readers need to click some stories first!", "error")
        return redirect(url_for("admin_newsletter"))
    articles = [{"title": r["title"], "url": r["url"],
                 "description": "", "source": {"name": ""}} for r in top_rows]
    sent, err = send_newsletter_digest(articles)
    if err:
        flash(f"Failed: {err}", "error")
    else:
        flash(f"✅ Digest sent to {sent} subscriber{'s' if sent != 1 else ''}!", "success")
    return redirect(url_for("admin_newsletter"))


# ── Demo download ─────────────────────────────────────────────────────────────
@app.route("/download-demo")
def download_demo():
    return send_from_directory(
        os.path.join(os.path.dirname(__file__), "static"),
        "newsbotninja_demo.zip",
        as_attachment=True,
        download_name="newsbotninja_demo.zip",
    )


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8099))
    app.run(host="0.0.0.0", port=port, debug=False)
