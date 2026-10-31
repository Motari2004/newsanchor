import os
import requests
import psycopg2
import hashlib  # Replaced random hash with stable hashlib
from psycopg2.extras import RealDictCursor
from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()

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
                article_id TEXT UNIQUE, 
                title TEXT,
                url TEXT,
                is_published BOOLEAN DEFAULT FALSE,
                error_log TEXT,
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        try:
            cur.execute("ALTER TABLE posts ADD COLUMN error_log TEXT;")
        except:
            pass 
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

def generate_stable_id(url):
    """Creates a unique ID that NEVER changes even if the server restarts"""
    return hashlib.md5(url.encode('utf-8')).hexdigest()

def perform_buffer_post(title, url):
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
    try:
        res = requests.post(BUFFER_API_URL, json={"query": mutation, "variables": variables}, headers=headers, timeout=10)
        res_data = res.json()
        if "errors" in res_data: return False, res_data["errors"]
        res_body = res_data.get("data", {}).get("createPost", {})
        if "message" in res_body: return False, res_body["message"]
        return True, "Success"
    except Exception as e:
        return False, str(e)

@app.route('/')
def dashboard():
    return render_template('index.html')

@app.route('/api/fetch-news')
def fetch_news():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT title, url, error_log FROM posts WHERE is_published = FALSE ORDER BY fetched_at DESC")
        articles = cur.fetchall()
        conn.close()
        return jsonify({"articles": articles}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def run_sync_logic():
    """Internal function to handle syncing without making HTTP requests to itself"""
    conn = get_db_connection()
    cur = conn.cursor()
    total_saved = 0
    for topic in NEWS_TOPICS:
        try:
            url = f"https://newsapi.org/v2/top-headlines?{topic['params']}&language=en&apiKey={NEWS_API_KEY}"
            response = requests.get(url).json()
            articles = response.get("articles", [])
            for art in articles:
                url_link = art.get("url")
                if not url_link: continue
                stable_id = generate_stable_id(url_link)
                cur.execute("SELECT id FROM posts WHERE article_id = %s", (stable_id,))
                if not cur.fetchone():
                    cur.execute(
                        "INSERT INTO posts (article_id, title, url, is_published) VALUES (%s, %s, %s, %s)", 
                        (stable_id, art.get("title"), url_link, False)
                    )
                    total_saved += 1
        except Exception as e:
            print(f"Error syncing topic {topic['name']}: {e}")
    conn.commit()
    conn.close()
    return total_saved

@app.route('/api/cron/sync-news')
def sync_news():
    try:
        count = run_sync_logic()
        return jsonify({"status": "success", "message": f"Synced {count} new articles"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/cron/publish-latest')
def cron_publish():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        one_day_ago = datetime.utcnow() - timedelta(hours=24)
        cur.execute(
            "SELECT id, title, url FROM posts WHERE is_published = FALSE AND fetched_at > %s ORDER BY fetched_at ASC LIMIT 1", 
            (one_day_ago,)
        )
        target = cur.fetchone()
        
        if not target:
            conn.close()
            run_sync_logic() # Call function directly, NOT via URL
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT id, title, url FROM posts WHERE is_published = FALSE ORDER BY fetched_at ASC LIMIT 1")
            target = cur.fetchone()
            
        if not target:
            conn.close()
            return jsonify({"status": "skipped", "message": "No news available to post"}), 200

        success, msg = perform_buffer_post(target['title'], target['url'])
        is_duplicate = "already got this one scheduled" in str(msg).lower() or "duplicate" in str(msg).lower()
        
        if success or is_duplicate:
            status_msg = "Published" if success else "Skipped (Duplicate)"
            cur.execute("UPDATE posts SET is_published = TRUE, error_log = %s WHERE id = %s", (msg, target['id']))
            conn.commit()
            conn.close()
            return jsonify({"status": "success", "message": f"{status_msg}: {target['title']}"}), 200
        else:
            cur.execute("UPDATE posts SET error_log = %s WHERE id = %s", (msg, target['id']))
            conn.commit()
            conn.close()
            return jsonify({"status": "error", "message": f"Buffer failed: {msg}"}), 500

    except Exception as e:
        if 'conn' in locals(): conn.close()
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True, port=5000)
