import os

# The path to your API file
FILE_PATH = "api/index.py"

# The old function block we are looking for (start and end markers)
START_MARKER = "def fetch_and_publish():"
END_MARKER = "except Exception as e:\n        return {\"error\": \"System crash\", \"details\": str(e)}"

# The new, improved function
NEW_FUNCTION = """def fetch_and_publish():
    try:
        # 1. Get News
        url = f"https://newsapi.org/v2/top-headlines?category=technology&language=en&apiKey={NEWS_API_KEY}"
        response = requests.get(url).json()
        if response.get("status") != "ok":
            return {"error": "NewsAPI error", "details": response}

        articles = response.get("articles", [])
        if not articles:
            return {"status": "no_news", "message": "No new articles found."}

        # 2. FIND THE FIRST UNPOSTED ARTICLE
        # Instead of just taking articles[0], we check the list until we find a new one
        conn = get_db_connection()
        cur = conn.cursor()
        
        chosen_article = None
        for art in articles:
            url_link = art.get("url")
            if not url_link: continue
            article_id = hash(url_link)
            
            cur.execute("SELECT id FROM posts WHERE article_id = %s", (article_id,))
            if not cur.fetchone():
                chosen_article = art
                break
        
        if not chosen_article:
            conn.close()
            return {"status": "skipped", "message": "All current top headlines have already been posted."}

        title = chosen_article.get("title")
        url_link = chosen_article.get("url")
        article_id = hash(url_link)

        # 3. Post to Buffer via GraphQL API
        graphql_url = "https://api.bufferapp.com/graphql"
        headers = {
            "Authorization": f"Bearer {BUFFER_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }
        
        mutation = \"\"\"
        mutation CreateUpdate($input: CreateUpdateInput!) {
          createUpdate(input: $input) {
            update { id }
            errors { message }
          }
        }
        \"\"\"
        
        variables = {
            "input": {
                "profileIds": [BUFFER_CHANNEL_ID],
                "text": f"{title} \\n\\nRead more: {url_link}",
                "shortenUrl": True
            }
        }

        buffer_res = requests.post(
            graphql_url, 
            json={"query": mutation, "variables": variables}, 
            headers=headers
        )

        res_data = buffer_res.json()
        
        if "errors" in res_data:
            conn.close()
            return {"error": "Buffer GraphQL Error", "details": res_data["errors"]}

        create_res = res_data.get("data", {}).get("createUpdate", {})
        if create_res.get("errors"):
            conn.close()
            return {"error": "Buffer API Error", "details": create_res["errors"]}

        # 4. Save to DB
        cur.execute("INSERT INTO posts (article_id, title, url) VALUES (%s, %s, %s)", (article_id, title, url_link))
        conn.commit()
        conn.close()
        return {"status": "success", "message": f"Posted: {title}"}

    except Exception as e:
        return {"error": "System crash", "details": str(e)}"""

def apply_patch():
    if not os.path.exists(FILE_PATH):
        print(f"Error: {FILE_PATH} not found.")
        return

    with open(FILE_PATH, "r") as f:
        content = f.read()

    if START_MARKER not in content:
        print("Error: Could not find the fetch_and_publish function to patch.")
        return

    # Find the boundaries of the function
    start_idx = content.find(START_MARKER)
    # Find the end of the function by looking for the matching error return block
    end_idx = content.find(END_MARKER)
    
    if end_idx == -1:
        print("Error: Could not find the end of the function.")
        return
    
    # Extend end_idx to cover the whole error return block
    end_idx += len(END_MARKER)

    # Replace old function with new function
    new_content = content[:start_idx] + NEW_FUNCTION + content[end_idx:]

    with open(FILE_PATH, "w") as f:
        f.write(new_content)

    print("PATCH_OK")

if __name__ == "__main__":
    apply_patch()
