import os
import requests
import psycopg2
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
# ============================================================

def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def initialize_db():
    try:
        if not DATABASE_URL: return
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS posts (
                id SERIAL PRIMARY KEY,
                article_id BIGINT UNIQUE,
                title TEXT,
                url TEXT,
                published_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
    print("🔍 Auto-discovering Buffer Profile ID for Twitter...")
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
            print(f"✅ Auto-discovered Twitter Profile: {twitter_profile['name']}")
        else:
            BUFFER_CHANNEL_ID = fallback_id
    except Exception as e:
        BUFFER_CHANNEL_ID = fallback_id
        print(f"❌ Auto-discovery failed: {e}")

initialize_db()
auto_discover_profile()

# --- HELPER: THE ACTUAL POSTING LOGIC ---
def perform_buffer_post(title, url, image_url):
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
    if image_url and image_url.startswith('http'):
        variables["input"]["media"] = [{"url": image_url}]
        
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
    try:
        url = f"https://newsapi.org/v2/top-headlines?sources=bbc-news&apiKey={NEWS_API_KEY}"
        response = requests.get(url).json()
        articles = response.get("articles", [])
        if DATABASE_URL:
            conn = get_db_connection()
            cur = conn.cursor()
            available = []
            for art in articles:
                url_link = art.get("url")
                if not url_link: continue
                cur.execute("SELECT id FROM posts WHERE article_id = %s", (hash(url_link),))
                if not cur.fetchone():
                    available.append({"title": art.get("title"), "url": url_link, "urlToImage": art.get("urlToImage")})
            conn.close()
            return jsonify({"articles": available}), 200
        return jsonify({"articles": articles}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/post-news', methods=['POST'])
def post_news():
    data = request.json
    success, msg = perform_buffer_post(data.get("title"), data.get("url"), data.get("urlToImage"))
    if success:
        if DATABASE_URL:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("INSERT INTO posts (article_id, title, url) VALUES (%s, %s, %s)", (hash(data.get("url")), data.get("title"), data.get("url")))
            conn.commit()
            conn.close()
        return jsonify({"status": "success", "message": msg}), 200
    return jsonify({"status": "error", "details": msg}), 400

# ============================================================
# NEW: AUTOMATION ENDPOINT
# ============================================================
@app.route('/api/cron/publish-latest')
def cron_publish():
    """Endpoint for external job: Finds 1 latest news from 24h, posts it, and marks it."""
    try:
        # 1. Fetch latest technology news
        url = f"https://newsapi.org/v2/top-headlines?category=technology&language=en&apiKey={NEWS_API_KEY}"
        response = requests.get(url).json()
        articles = response.get("articles", [])
        
        if not articles:
            return jsonify({"status": "error", "message": "No news found"}), 404

        # 2. Filter for articles from the last 24 hours and NOT in DB
        now = datetime.utcnow()
        one_day_ago = now - timedelta(hours=24)
        
        conn = get_db_connection()
        cur = conn.cursor()
        
        target_article = None
        for art in articles:
            # Parse NewsAPI date (Example: 2023-10-30T11:00:00Z)
            pub_date_str = art.get("publishedAt", "").replace("Z", "")
            try:
                pub_date = datetime.fromisoformat(pub_date_str)
            except:
                continue
            
            if pub_date > one_day_ago:
                # Check if already published in DB
                cur.execute("SELECT id FROM posts WHERE article_id = %s", (hash(art.get("url")),))
                if not cur.fetchone():
                    target_article = art
                    break # Take the very first (latest) match
        
        if not target_article:
            conn.close()
            return jsonify({"status": "skipped", "message": "No new articles in last 24h"}), 200

        # 3. Post to Twitter
        success, msg = perform_buffer_post(
            target_article.get("title"), 
            target_article.get("url"), 
            target_article.get("urlToImage")
        )
        
        if success:
            # 4. Mark as published in DB
            cur.execute("INSERT INTO posts (article_id, title, url) VALUES (%s, %s, %s)", 
                        (hash(target_article.get("url")), target_article.get("title"), target_article.get("url")))
            conn.commit()
            conn.close()
            return jsonify({"status": "success", "message": f"Published: {target_article.get('title')}"}), 200
        else:
            conn.close()
            return jsonify({"status": "error", "message": f"Buffer failed: {msg}"}), 500

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True, port=5000)
