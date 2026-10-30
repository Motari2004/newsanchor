import os
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__, template_folder='../templates')

# Configuration from .env
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
BUFFER_ACCESS_TOKEN = os.getenv("BUFFER_ACCESS_TOKEN")
BUFFER_CHANNEL_ID = os.getenv("BUFFER_CHANNEL_ID")
DATABASE_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

# --- NEW SETUP FUNCTION ---
@app.route('/setup-db')
def setup_db():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        # This creates the table if it doesn't exist
        cur.execute("""
            CREATE TABLE IF NOT EXISTS posts (
                id SERIAL PRIMARY KEY,
                article_id BIGINT UNIQUE,
                title TEXT,
                url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"status": "success", "message": "Table 'posts' created successfully!"}), 200
    except Exception as e:
        return jsonify({"status": "error", "details": str(e)}), 500
# ---------------------------

def fetch_and_publish():
    try:
        url = f"https://newsapi.org/v2/top-headlines?category=technology&language=en&apiKey={NEWS_API_KEY}"
        response = requests.get(url).json()
        if response.get("status") != "ok":
            return {"error": "NewsAPI error", "details": response}

        articles = response.get("articles", [])
        if not articles:
            return {"status": "no_news", "message": "No new articles found."}

        article = articles[0] 
        title = article.get("title")
        url_link = article.get("url")
        article_id = hash(url_link)

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM posts WHERE article_id = %s", (article_id,))
        exists = cur.fetchone()

        if exists:
            conn.close()
            return {"status": "skipped", "message": f"Article already posted: {title}"}

        buffer_payload = {"text": f"{title} \n\nRead more: {url_link}", "profile_ids": [BUFFER_CHANNEL_ID]}
        buffer_headers = {"Authorization": f"Bearer {BUFFER_ACCESS_TOKEN}"}
        buffer_res = requests.post("https://api.bufferapp.com/1/updates/create.json", json=buffer_payload, headers=buffer_headers)

        if buffer_res.status_code == 200:
            cur.execute("INSERT INTO posts (article_id, title, url) VALUES (%s, %s, %s)", (article_id, title, url_link))
            conn.commit()
            conn.close()
            return {"status": "success", "message": f"Posted: {title}"}
        else:
            conn.close()
            return {"error": "Buffer API error", "details": buffer_res.text}
    except Exception as e:
        return {"error": "System crash", "details": str(e)}

@app.route('/')
def dashboard():
    return render_template('index.html')

@app.route('/api')
def cron_trigger():
    return jsonify(fetch_and_publish()), 200

@app.route('/manual-post', methods=['POST'])
def manual_post():
    return jsonify(fetch_and_publish()), 200

@app.route('/logs')
def get_logs():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT title, url, created_at FROM posts ORDER BY created_at DESC LIMIT 20")
        logs = cur.fetchall()
        conn.close()
        return jsonify(logs), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def handler(request, context):
    return app(request, context)
