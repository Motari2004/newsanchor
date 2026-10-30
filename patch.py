import os
import shutil

def setup_project():
    # 1. Create Folder Structure
    folders = ['api', 'templates']
    for folder in folders:
        if not os.path.exists(folder):
            os.makedirs(folder)
            print(f"Created folder: {folder}")

    # 2. Create vercel.json (The Router)
    # This tells Vercel to send ALL traffic to the serverless function
    vercel_json = """{
  "version": 2,
  "rewrites": [
    { "source": "/(.*)", "destination": "/api/index.py" }
  ]
}"""
    with open("vercel.json", "w") as f:
        f.write(vercel_json)
    print("Created vercel.json")

    # 3. Create api/index.py (The Bridge)
    # This is the only file Vercel actually executes
    api_index = """from app import app

def handler(request, context):
    return app(request, context)
"""
    with open("api/index.py", "w") as f:
        f.write(api_index)
    print("Created api/index.py")

    # 4. Create requirements.txt
    requirements = """flask
requests
psycopg2-binary
python-dotenv
"""
    with open("requirements.txt", "w") as f:
        f.write(requirements)
    print("Created requirements.txt")

    # 5. Handle Templates Folder
    # Flask REQUIRES the folder to be named 'templates' (plural)
    if os.path.exists("template"):
        shutil.move("template", "templates")
        print("Renamed 'template' to 'templates' for Flask compatibility")

    print("\nSETUP_OK")
    print("Next steps:")
    print("1. Ensure your main logic is in app.py in the root folder.")
    print("2. Push these changes to GitHub.")
    print("3. Add your ENV variables to the Vercel Dashboard.")

if __name__ == "__main__":
    setup_project()
