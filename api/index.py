
from flask import Flask, jsonify, request, send_from_directory
import requests
import psycopg2
import os
from datetime import datetime

app = Flask(__name__)

# Environment Variables
NEWS_API_KEY = os.getenv('NEWS_API_KEY')
BUFFER_ACCESS_TOKEN = os.getenv('BUFFER_ACCESS_TOKEN')
BUFFER_CHANNEL_ID = os.getenv('BUFFER_CHANNEL_ID')
DATABASE_URL = os.getenv('DATABASE_URL') 
CRON_SECRET = os.getenv('CRON_SECRET')

def run_buffer_mutation(mutation, variables):
    headers = {"Authorization": f"Bearer {BUFFER_ACCESS_TOKEN}", "Content-Type": "application/json"}
    payload = {"query": mutation, "variables": variables}
    response = requests.post("https://api.buffer.com", json=payload, headers=headers)
    return response.json()

def core_posting_logic():
    # Fetch news
    news_url = f'https://newsapi.org/v2/top-headlines?country=us&apiKey={NEWS_API_KEY}'
    articles = requests.get(news_url).json().get('articles', [])
    
    if not articles:
        return None, "No news available"

    # Check Neon DB
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    selected_article = None
    
    for art in articles:
        cur.execute("SELECT id FROM posted_articles WHERE url = %s", (art['url'],))
        if cur.fetchone() is None:
            selected_article = art
            break
    
    if not selected_article:
        cur.close()
        conn.close()
        return None, "All news already posted"

    # Buffer Post
    post_mutation = """
    mutation CreatePost($text: String!, $channelId: ChannelId!, $imageUrl: String!) {
      createPost(input: {
        text: $text,
        channelId: $channelId,
        mode: shareNow,
        schedulingType: automatic,
        assets: [{ image: { url: $imageUrl } }]
      }) {
        ... on PostActionSuccess { post { id } }
        ... on MutationError { message }
      }
    }
    """
    raw_text = f"{selected_article['title']}\n\n{selected_article['url']}"
    safe_text = (raw_text[:277] + '...') if len(raw_text) > 280 else raw_text
    
    variables = {
        "text": safe_text,
        "channelId": BUFFER_CHANNEL_ID,
        "imageUrl": selected_article['urlToImage'] or 'https://via.placeholder.com/400x200'
    }

    run_buffer_mutation(post_mutation, variables)

    # Save to Neon DB (Including Title for the UI)
    cur.execute("INSERT INTO posted_articles (url, title) VALUES (%s, %s)", (selected_article['url'], selected_article['title']))
    conn.commit()
    cur.close()
    conn.close()
    
    return selected_article, "Success"

# --- ROUTES ---

@app.route('/')
def index():
    return send_from_directory('template', 'index.html')

@app.route('/api/logs')
def get_logs():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("SELECT title, url, posted_at FROM posted_articles ORDER BY posted_at DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    
    return jsonify([{"title": r[0], "url": r[1], "posted_at": str(r[2])} for r in rows])

@app.route('/api/post-now', methods=['POST'])
def manual_post():
    art, msg = core_posting_logic()
    if art:
        return jsonify({'status': 'success', 'message': f"Posted: {art['title']}"})
    return jsonify({'status': 'error', 'message': msg}), 400

@app.route('/api/cron')
def cron_trigger():
    user_key = request.args.get('key')
    if user_key != CRON_SECRET:
        return jsonify({'status': 'unauthorized'}), 401
    
    art, msg = core_posting_logic()
    if art:
        return jsonify({'status': 'success', 'posted': art['title']})
    return jsonify({'status': 'no_post', 'message': msg})

def handler(request, context):
    return app(request, context)
