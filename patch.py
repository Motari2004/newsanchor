import os

FILE_PATH = "api/index.py"

# We are replacing the entire auto_discover_profile function
START_MARKER = "def auto_discover_profile():"
END_MARKER = "except Exception as e:\n        print(f\"❌ Auto-discovery failed: {e}\")"

NEW_FUNCTION = """def auto_discover_profile():
    \"\"\"Finds the REAL Numeric Profile ID using the correct endpoint with .env fallback\"\"\"
    global BUFFER_CHANNEL_ID
    
    # Keep a copy of the .env ID in case the API fails
    fallback_id = os.getenv("BUFFER_CHANNEL_ID")
    
    print("🔍 Auto-discovering Buffer Profile ID via api.buffer.com...")
    
    headers = {"Authorization": f"Bearer {BUFFER_ACCESS_TOKEN}", "Content-Type": "application/json"}
    query = "{ me { profiles { id service username } } }"
    
    try:
        res = requests.post(BUFFER_API_URL, json={"query": query}, headers=headers)
        res.raise_for_status()
        data = res.json()
        profiles = data.get("data", {}).get("me", {}).get("profiles", [])
        
        if profiles:
            BUFFER_CHANNEL_ID = profiles[0]['id']
            print(f"✅ Auto-discovered Profile: {profiles[0]['service']} ({profiles[0]['username']})")
            print(f"🚀 Using REAL ID: {BUFFER_CHANNEL_ID}")
        else:
            BUFFER_CHANNEL_ID = fallback_id
            if BUFFER_CHANNEL_ID:
                print("⚠️ No profiles found via API, using BUFFER_CHANNEL_ID from .env")
            else:
                print("❌ No connected profiles found in Buffer AND no fallback ID in .env")
    except Exception as e:
        BUFFER_CHANNEL_ID = fallback_id
        print(f"❌ Auto-discovery failed: {e}. Falling back to .env ID: {BUFFER_CHANNEL_ID}")
"""

# FIX: Added encoding="utf-8" to handle emojis/special characters on Windows
try:
    with open(FILE_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    parts = content.split(START_MARKER)
    if len(parts) > 1:
        after_start = parts[1].split(END_MARKER)
        if len(after_start) > 1:
            final_content = parts[0] + NEW_FUNCTION + after_start[1]
            
            # FIX: Added encoding="utf-8" here as well
            with open(FILE_PATH, "w", encoding="utf-8") as f:
                f.write(final_content)
            print("PATCH_OK")
        else:
            print("Could not find END_MARKER. Check if the file content matches the original exactly.")
    else:
        print("Could not find START_MARKER. Check if the function 'def auto_discover_profile():' exists.")
except Exception as e:
    print(f"An error occurred: {e}")
