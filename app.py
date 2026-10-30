import os
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)

# Configuration from .env
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
BUFFER_ACCESS_TOKEN = os.getenv("BUFFER_ACCESS_TOKEN")
BUFFER_CHANNEL_ID = os.getenv("BUFFER_CHANNEL_ID")
DATABASE_URL = os.getenv("DATABASE_URL")
CRON_SECRET = os.getenv("CRON_SECRET")

# Database helper function
def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

# --- CORE LOGIC: THE PUBLISHER ---

def fetch_and_publish():
    """The main logic to find news, check DB, and post to Buffer."""
    try:
        # 1. Fetch top headlines from NewsAPI
        # Change 'technology' to your preferred category or use q= for specific keywords
        url = f"https://newsapi.org/v2/top-headlines?category=technology&language=en&apiKey={NEWS_API_KEY}"
        response = requests.get(url).json()
        
        if response.get("status") != "ok":
            return {"error": "NewsAPI error", "details": response}

        articles = response.get("articles", [])
        if not articles:
            return {"status": "no_news", "message": "No new articles found."}

        # We only try to post the most recent article per run to avoid spamming
        article = articles[0] 
        title = article.get("title")
        url_link = article.get("url")
        article_id = hash(url_link) # Create a unique numeric ID for the DB

        # 2. Check Database if this URL was already posted
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM posts WHERE article_id = %s", (article_id,))
        exists = cur.fetchone()

        if exists:
            conn.close()
            return {"status": "skipped", "message": f"Article already posted: {title}"}

        # 3. Post to Buffer
        # Buffer expects text in the 'text' field
        buffer_payload = {
            "text": f"{title} \n\nRead more: {url_link}",
            "profile_ids": [BUFFER_CHANNEL_ID]
        }
        buffer_headers = {"Authorization": f"Bearer {BUFFER_ACCESS_TOKEN}"}
        
        buffer_res = requests.post(
            "https://api.bufferapp.com/1/updates/create.json", 
            json=buffer_payload, 
            headers=buffer_headers
        )

        if buffer_res.status_code == 200:
            # 4. Save to Database so we don't post it again
            cur.execute(
                "INSERT INTO posts (article_id, title, url) VALUES (%s, %s, %s)",
                (article_id, title, url_link)
            )
            conn.commit()
            conn.close()
            return {"status": "success", "message": f"Posted: {title}"}
        else:
            conn.close()
            return {"error": "Buffer API error", "details": buffer_res.text}

    except Exception as e:
        return {"error": "System crash", "details": str(e)}

# --- ROUTES ---

@app.route('/')
def dashboard():
    """Serves the frontend UI."""
    return render_template('index.html')

@app.route('/api')
def cron_trigger():
    """The endpoint called by cron-job.org."""
    # Security: Check if the secret matches
    provided_secret = request.args.get("secret")
    if provided_secret != CRON_SECRET:
        return jsonify({"error": "Unauthorized"}), 401

    result = fetch_and_publish()
    return jsonify(result), 200

@app.route('/manual-post', methods=['POST'])
def manual_post():
    """Allows the dashboard 'Post Now' button to trigger a post."""
    result = fetch_and_publish()
    return jsonify(result), 200

@app.route('/logs')
def get_logs():
    """Fetches the history of posted news from the DB for the UI."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT title, url, created_at FROM posts ORDER BY created_at DESC LIMIT 20")
        logs = cur.fetchall()
        conn.close()
        return jsonify(logs), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True)
