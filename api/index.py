import os
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv

load_dotenv()

# --- SAFETY FOLDER LOGIC ---
# Ensures Flask finds index.html regardless of Vercel's internal pathing
current_dir = os.path.dirname(os.path.abspath(__file__))
template_dir = os.path.join(current_dir, '..', 'template') 
if not os.path.exists(template_dir):
    template_dir = os.path.join(current_dir, '..', 'templates')

app = Flask(__name__, template_folder=template_dir)

# Config
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
BUFFER_ACCESS_TOKEN = os.getenv("BUFFER_ACCESS_TOKEN")
BUFFER_CHANNEL_ID = os.getenv("BUFFER_CHANNEL_ID")
DATABASE_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

# --- AUTO-SETUP DATABASE ---
def initialize_db():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
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
    except Exception as e:
        print(f"DB Init Error: {e}")

# Run initialization on startup
initialize_db()

@app.route('/')
def dashboard():
    try:
        return render_template('index.html')
    except Exception as e:
        return f"Template Error: {str(e)}. Check if index.html is in the template folder.", 500

@app.route('/api/fetch-news')
def fetch_news():
    try:
        # 1. Get News from API
        url = f"https://newsapi.org/v2/top-headlines?category=technology&language=en&apiKey={NEWS_API_KEY}"
        response = requests.get(url).json()
        if response.get("status") != "ok":
            return jsonify({"error": "NewsAPI error", "details": response}), 500

        articles = response.get("articles", [])
        
        # 2. Filter out already posted articles using Database
        conn = get_db_connection()
        cur = conn.cursor()
        
        available_articles = []
        for art in articles:
            url_link = art.get("url")
            if not url_link: continue
            article_id = hash(url_link)
            
            cur.execute("SELECT id FROM posts WHERE article_id = %s", (article_id,))
            if not cur.fetchone():
                available_articles.append({
                    "title": art.get("title"),
                    "url": url_link
                })
        
        conn.close()
        return jsonify({"articles": available_articles}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/post-news', methods=['POST'])
def post_news():
    try:
        data = request.json
        title = data.get("title")
        url_link = data.get("url")
        
        if not title or not url_link:
            return jsonify({"status": "error", "details": "Missing title or URL"}), 400
            
        article_id = hash(url_link)

        # 1. Post to Buffer GraphQL
        graphql_url = "https://api.bufferapp.com/graphql"
        headers = {"Authorization": f"Bearer {BUFFER_ACCESS_TOKEN}", "Content-Type": "application/json"}
        
        mutation = """
        mutation CreateUpdate($input: CreateUpdateInput!) {
          createUpdate(input: $input) { 
            update { id } 
            errors { message } 
          }
        }
        """
        variables = {
            "input": {
                "profileIds": [BUFFER_CHANNEL_ID],
                "text": f"{title} \n\nRead more: {url_link}",
                "shortenUrl": True
            }
        }

        res = requests.post(graphql_url, json={"query": mutation, "variables": variables}, headers=headers)
        res_data = res.json()

        # DEBUG: Check for top-level GraphQL errors (Auth/Syntax)
        if "errors" in res_data:
            return jsonify({"status": "error", "details": f"GraphQL Error: {res_data['errors']}"}), 400

        # DEBUG: Check for business-level errors (Channel ID/Permissions)
        data_block = res_data.get("data", {})
        create_update = data_block.get("createUpdate", {})
        
        if not create_update:
            return jsonify({"status": "error", "details": "Buffer returned no data block"}), 500
            
        errors = create_update.get("errors")
        if errors:
            return jsonify({"status": "error", "details": f"Buffer API Error: {errors}"}), 400

        # 2. Save to Database only if Buffer actually created the post
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("INSERT INTO posts (article_id, title, url) VALUES (%s, %s, %s)", (article_id, title, url_link))
        conn.commit()
        conn.close()

        return jsonify({"status": "success", "message": f"Successfully pushed to Buffer: {title}"}), 200
    except Exception as e:
        return jsonify({"status": "error", "details": str(e)}), 500

@app.route('/api')
def cron_trigger():
    # Simple trigger for cron tasks
    return jsonify({"message": "Cron trigger called. Use Dashboard for manual control."}), 200

def handler(request, context):
    return app(request, context)
