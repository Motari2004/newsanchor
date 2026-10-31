import os
import requests
import psycopg2
import random
from psycopg2.extras import RealDictCursor
from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()

# --- FOLDER LOGIC ---
current_dir = os.path.dirname(os.path.abspath(__file__))
template_dir = os.path.join(current_dir, '..', 'template') 
if not os.path.exists(template_dir):
    template_dir = os.path.join(current_dir, '..', 'templates')

app = Flask(__name__, template_folder=template_dir)

# ============================================================
# CONFIGURATION
# ============================================================
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "f7da4fb81e024dcba2f28f19ec500cfc") 
BUFFER_ACCESS_TOKEN = os.getenv("BUFFER_ACCESS_TOKEN", "YejnMy656UwVGgapzVDWIIkB0JszNCzu7iExNEpNiJ6")
BUFFER_API_URL = "https://api.buffer.com" 
DATABASE_URL = os.getenv("DATABASE_URL")
BUFFER_CHANNEL_ID = os.getenv("BUFFER_CHANNEL_ID") 

NEWS_TOPICS = [
    {"name": "BBC News", "params": "sources=bbc-news"},
    {"name": "Technology", "params": "category=technology"},
    {"name": "Hacking/Security", "params": "q=hacking OR 'cyber security' OR 'data breach'"},
    {"name": "Health", "params": "category=health"},
    {"name": "Kenyan News", "params": "q=Kenya OR Nairobi"},
]
# ============================================================

def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def initialize_db():
    try:
        if not DATABASE_URL: return
        conn = get_db_connection()
        cur = conn.cursor()
        # Added 'is_published' column to track what has been sent to Twitter
        cur.execute("""
            CREATE TABLE IF NOT EXISTS posts (
                id SERIAL PRIMARY KEY,
                article_id BIGINT UNIQUE,
                title TEXT,
                url TEXT,
                is_published BOOLEAN DEFAULT FALSE,
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"DB Init Error: {e}")

def auto_discover_profile():
    global BUFFER_CHANNEL_ID
    fallback_id = os.getenv("BUFFER_CHANNEL_ID")
    headers = {"Authorization": f"Bearer {BUFFER_ACCESS_TOKEN}", "Content-Type": "application/json"}
    try:
        org_query = "{ account { organizations { id name } } }"
        org_res = requests.post(BUFFER_API_URL, json={"query": org_query}, headers=headers)
        org_res.raise_for_status()
        org_data = org_res.json()
        orgs = org_data.get("data", {}).get("account", {}).get("organizations", [])
        if not orgs: raise Exception("No organizations found.")
        org_id = orgs[0]['id']
        chan_query = "{ channels(input: { organizationId: \"%s\" }) { id name service } }" % org_id
        chan_res = requests.post(BUFFER_API_URL, json={"query": chan_query}, headers=headers)
        chan_res.raise_for_status()
        profiles = chan_res.json().get("data", {}).get("channels", [])
        twitter_profile = next((p for p in profiles if p['service'] == 'twitter'), None)
        if twitter_profile:
            BUFFER_CHANNEL_ID = twitter_profile['id']
    except Exception as e:
        BUFFER_CHANNEL_ID = fallback_id

initialize_db()
auto_discover_profile()

def perform_buffer_post(title, url):
    """Logic to send a post to Buffer"""
    headers = {"Authorization": f"Bearer {BUFFER_ACCESS_TOKEN}", "Content-Type": "application/json"}
    mutation = """
    mutation CreateImmediatePost($input: CreatePostInput!) {
      createPost(input: $input) {
        ... on PostActionSuccess { post { id } }
        ... on MutationError { message }
      }
    }
    """
    full_text = f"{title}\n\n{url}"
    if len(full_text) > 280: full_text = full_text[:277] + "..."
    
    variables = {
        "input": {
            "text": full_text,
            "channelId": BUFFER_CHANNEL_ID,
            "schedulingType": "automatic",
            "mode": "shareNow"
        }
    }
    res = requests.post(BUFFER_API_URL, json={"query": mutation, "variables": variables}, headers=headers)
    res_data = res.json()
    if "errors" in res_data: return False, res_data["errors"]
    res_body = res_data.get("data", {}).get("createPost", {})
    if "message" in res_body: return False, res_body["message"]
    return True, "Success"

@app.route('/')
def dashboard():
    return render_template('index.html')

@app.route('/api/fetch-news')
def fetch_news():
    """Dashboard view: shows all currently stored unposted news"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT title, url FROM posts WHERE is_published = FALSE ORDER BY fetched_at DESC")
        articles = cur.fetchall()
        conn.close()
        return jsonify({"articles": articles}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================================================
# BULK SYNC ENDPOINT (Run this every 6-12 hours)
# ============================================================
@app.route('/api/cron/sync-news')
def sync_news():
    """Fetches news from ALL topics and saves them to DB if not already there"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        total_saved = 0
        
        for topic in NEWS_TOPICS:
            url = f"https://newsapi.org/v2/top-headlines?{topic['params']}&language=en&apiKey={NEWS_API_KEY}"
            response = requests.get(url).json()
            articles = response.get("articles", [])
            
            for art in articles:
                url_link = art.get("url")
                if not url_link: continue
                
                # Only insert if it doesn't exist in DB
                cur.execute("SELECT id FROM posts WHERE article_id = %s", (hash(url_link),))
                if not cur.fetchone():
                    cur.execute(
                        "INSERT INTO posts (article_id, title, url, is_published) VALUES (%s, %s, %s, %s)", 
                        (hash(url_link), art.get("title"), url_link, False)
                    )
                    total_saved += 1
        
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "message": f"Synced {total_saved} new articles to database"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ============================================================
# PUBLISH ENDPOINT (Run this every 30 minutes)
# ============================================================
@app.route('/api/cron/publish-latest')
def cron_publish():
    """Finds ONE unposted article from the last 24h and posts it"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # 1. Find the oldest unposted article from the last 24 hours
        one_day_ago = datetime.utcnow() - timedelta(hours=24)
        cur.execute(
            "SELECT id, title, url FROM posts WHERE is_published = FALSE AND fetched_at > %s ORDER BY fetched_at ASC LIMIT 1", 
            (one_day_ago,)
        )
        target = cur.fetchone()
        
        if not target:
            # 2. If DB is empty, trigger a sync immediately so we have something to post
            conn.close()
            print("DB empty or no fresh news. Triggering sync...")
            requests.get(f"http://localhost:5000/api/cron/sync-news") # Adjust URL to your production URL
            
            # Try one more time after sync
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT id, title, url FROM posts WHERE is_published = FALSE ORDER BY fetched_at ASC LIMIT 1")
            target = cur.fetchone()
            
        if not target:
            conn.close()
            return jsonify({"status": "skipped", "message": "No news available to post"}), 200

        # 3. Post to Buffer
        success, msg = perform_buffer_post(target['title'], target['url'])
        
        if success:
            # 4. Mark as published in DB
            cur.execute("UPDATE posts SET is_published = TRUE WHERE id = %s", (target['id'],))
            conn.commit()
            conn.close()
            return jsonify({"status": "success", "message": f"Published: {target['title']}"}), 200
        else:
            conn.close()
            return jsonify({"status": "error", "message": f"Buffer failed: {msg}"}), 500

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True, port=5000)
