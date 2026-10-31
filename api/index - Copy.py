import os
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv

load_dotenv()

# --- FOLDER LOGIC ---
current_dir = os.path.dirname(os.path.abspath(__file__))
template_dir = os.path.join(current_dir, '..', 'template') 
if not os.path.exists(template_dir):
    template_dir = os.path.join(current_dir, '..', 'templates')

app = Flask(__name__, template_folder=template_dir)

# ============================================================
# CONFIGURATION (Using the new endpoint)
# ============================================================
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "f7da4fb81e024dcba2f28f19ec500cfc") 
BUFFER_ACCESS_TOKEN = os.getenv("BUFFER_ACCESS_TOKEN", "YejnMy656UwVGgapzVDWIIkB0JszNCzu7iExNEpNiJ6")
BUFFER_API_URL = "https://api.buffer.com" 
DATABASE_URL = os.getenv("DATABASE_URL")

# This will be auto-filled on startup
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"DB Init Error: {e}")

def auto_discover_profile():
    """Finds the REAL Numeric Profile ID using the correct endpoint"""
    global BUFFER_CHANNEL_ID
    print("🔍 Auto-discovering Buffer Profile ID via api.buffer.com...")
    
    headers = {"Authorization": f"Bearer {BUFFER_ACCESS_TOKEN}", "Content-Type": "application/json"}
    query = "{ me { profiles { id service username } } }"
    
    try:
        res = requests.post(BUFFER_API_URL, json={"query": query}, headers=headers)
        data = res.json()
        profiles = data.get("data", {}).get("me", {}).get("profiles", [])
        
        if profiles:
            BUFFER_CHANNEL_ID = profiles[0]['id']
            print(f"✅ Auto-discovered Profile: {profiles[0]['service']} ({profiles[0]['username']})")
            print(f"🚀 Using REAL ID: {BUFFER_CHANNEL_ID}")
        else:
            print("❌ No connected profiles found. Check your Buffer account.")
    except Exception as e:
        print(f"❌ Auto-discovery failed: {e}")

# Start process
initialize_db()
auto_discover_profile()

@app.route('/')
def dashboard():
    return render_template('index.html')

@app.route('/api/fetch-news')
def fetch_news():
    try:
        url = f"https://newsapi.org/v2/top-headlines?category=technology&language=en&apiKey={NEWS_API_KEY}"
        response = requests.get(url).json()
        if response.get("status") != "ok":
            return jsonify({"error": "NewsAPI error", "details": response}), 500

        articles = response.get("articles", [])
        if DATABASE_URL:
            conn = get_db_connection()
            cur = conn.cursor()
            available = []
            for art in articles:
                url_link = art.get("url")
                if not url_link: continue
                aid = hash(url_link)
                cur.execute("SELECT id FROM posts WHERE article_id = %s", (aid,))
                if not cur.fetchone():
                    available.append({"title": art.get("title"), "url": url_link, "urlToImage": art.get("urlToImage")})
            conn.close()
            return jsonify({"articles": available}), 200
        
        return jsonify({"articles": [{"title": a['title'], "url": a['url'], "urlToImage": a.get('urlToImage')} for a in articles]}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/post-news', methods=['POST'])
def post_news():
    try:
        if not BUFFER_CHANNEL_ID:
            return jsonify({"status": "error", "details": "No Profile ID found."}), 400

        data = request.json
        title = data.get("title")
        url_link = data.get("url")
        image_url = data.get("urlToImage")

        headers = {"Authorization": f"Bearer {BUFFER_ACCESS_TOKEN}", "Content-Type": "application/json"}
        
        # USING THE NEW mutation createPost and mode: shareNow
        mutation = """
        mutation CreateImmediatePost($input: CreatePostInput!) {
          createPost(input: $input) {
            ... on PostActionSuccess {
              post { id }
            }
            ... on MutationError {
              message
            }
          }
        }
        """
        
        full_text = f"{title}\n\n{url_link}"
        if len(full_text) > 280: full_text = full_text[:277] + "..."

        variables = {
            "input": {
                "text": full_text,
                "channelId": BUFFER_CHANNEL_ID,
                "schedulingType": "automatic",
                "mode": "shareNow",
                "media": [{"url": image_url}] if image_url else []
            }
        }

        res = requests.post(BUFFER_API_URL, json={"query": mutation, "variables": variables}, headers=headers)
        res_data = res.json()

        if "errors" in res_data:
            return jsonify({"status": "error", "details": res_data["errors"]}), 400

        # Handle the Union type result from GraphQL
        create_post_res = res_data.get("data", {}).get("createPost", {})
        
        if "message" in create_post_res: # This is a MutationError
            return jsonify({"status": "error", "details": create_post_res["message"]}), 400

        if DATABASE_URL:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("INSERT INTO posts (article_id, title, url) VALUES (%s, %s, %s)", (hash(url_link), title, url_link))
            conn.commit()
            conn.close()

        return jsonify({"status": "success", "message": "Posted INSTANTLY via shareNow!"}), 200
    except Exception as e:
        return jsonify({"status": "error", "details": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True, port=5000)
