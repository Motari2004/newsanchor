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
# CONFIGURATION
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
    """Finds the REAL Channel ID specifically for TWITTER"""
    global BUFFER_CHANNEL_ID
    
    fallback_id = os.getenv("BUFFER_CHANNEL_ID")
    print("🔍 Auto-discovering Buffer Profile ID for Twitter...")
    
    headers = {"Authorization": f"Bearer {BUFFER_ACCESS_TOKEN}", "Content-Type": "application/json"}
    
    try:
        # STEP 1: Get Organization ID
        org_query = "{ account { organizations { id name } } }"
        org_res = requests.post(BUFFER_API_URL, json={"query": org_query}, headers=headers)
        org_res.raise_for_status()
        org_data = org_res.json()
        
        orgs = org_data.get("data", {}).get("account", {}).get("organizations", [])
        if not orgs:
            raise Exception("No organizations found in Buffer account.")
        
        org_id = orgs[0]['id']
        print(f"🏢 Found Organization: {orgs[0]['name']} ({org_id})")

        # STEP 2: Get All Channels for that Organization
        chan_query = "{ channels(input: { organizationId: \"%s\" }) { id name service } }" % org_id
        chan_res = requests.post(BUFFER_API_URL, json={"query": chan_query}, headers=headers)
        chan_res.raise_for_status()
        chan_data = chan_res.json()
        
        profiles = chan_data.get("data", {}).get("channels", [])
        
        # --- TARGET TWITTER SPECIFICALLY ---
        twitter_profile = next((p for p in profiles if p['service'] == 'twitter'), None)
        
        if twitter_profile:
            BUFFER_CHANNEL_ID = twitter_profile['id']
            print(f"✅ Auto-discovered Twitter Profile: {twitter_profile['name']}")
            print(f"🚀 Using REAL ID: {BUFFER_CHANNEL_ID}")
        elif profiles:
            # If no twitter found, fallback to .env or the first available
            BUFFER_CHANNEL_ID = fallback_id if fallback_id else profiles[0]['id']
            print(f"⚠️ Twitter not found. Using fallback/first profile: {BUFFER_CHANNEL_ID}")
        else:
            BUFFER_CHANNEL_ID = fallback_id
            print("⚠️ No channels found in organization, using fallback from .env")

    except Exception as e:
        BUFFER_CHANNEL_ID = fallback_id
        print(f"❌ Auto-discovery failed: {e}. Falling back to .env ID: {BUFFER_CHANNEL_ID}")


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
        title = data.get("title", "Check this out!")
        url_link = data.get("url")
        image_url = data.get("urlToImage")

        if not url_link:
            return jsonify({"status": "error", "details": "Missing article URL."}), 400

        headers = {"Authorization": f"Bearer {BUFFER_ACCESS_TOKEN}", "Content-Type": "application/json"}
        
        # The mutation remains the same, but we ensure variables are strictly typed
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
        
        # Twitter likes short text + link
        full_text = f"{title}\n\n{url_link}"
        if len(full_text) > 280: 
            full_text = full_text[:277] + "..."

        # IMPORTANT: We ensure the structure is EXACTLY as required by Buffer's schema
        variables = {
            "input": {
                "text": full_text,
                "channelId": BUFFER_CHANNEL_ID,
                "schedulingType": "automatic",
                "mode": "shareNow"
            }
        }

        # Add media ONLY if it exists and is a valid URL
        if image_url and image_url.startswith('http'):
            variables["input"]["media"] = [{"url": image_url}]

        # Send request
        res = requests.post(
            BUFFER_API_URL, 
            json={"query": mutation, "variables": variables}, 
            headers=headers
        )
        res_data = res.json()

        # 1. Check for top-level GraphQL errors (Auth or Syntax)
        if "errors" in res_data:
            print(f"GraphQL Error: {res_data['errors']}")
            return jsonify({"status": "error", "details": res_data["errors"]}), 400

        # 2. Check for Mutation-level errors (Buffer logic errors)
        create_post_res = res_data.get("data", {}).get("createPost", {})
        if "message" in create_post_res:
            print(f"Buffer Logic Error: {create_post_res['message']}")
            return jsonify({"status": "error", "details": create_post_res["message"]}), 400

        # 3. Success! Save to DB
        if DATABASE_URL:
            try:
                conn = get_db_connection()
                cur = conn.cursor()
                cur.execute("INSERT INTO posts (article_id, title, url) VALUES (%s, %s, %s)", (hash(url_link), title, url_link))
                conn.commit()
                conn.close()
            except Exception as db_e:
                print(f"DB Save Error (ignored): {db_e}")

        return jsonify({"status": "success", "message": "Posted INSTANTLY to Twitter!"}), 200

    except Exception as e:
        print(f"System Error: {str(e)}")
        return jsonify({"status": "error", "details": str(e)}), 500

    
    
    
    
    
    
    

if __name__ == "__main__":
    app.run(debug=True, port=5000)
