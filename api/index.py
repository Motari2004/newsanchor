from app import app

# Vercel looks for a function or an object that can handle the request.
# By importing 'app' from app.py, we tell Vercel to use your 
# Flask application to handle all incoming requests to the /api route.

def handler(request, context):
    """
    Vercel Serverless Handler.
    This function wraps the Flask app and allows it to run 
    within Vercel's serverless function environment.
    """
    return app(request, context)

# For local testing or specific Vercel configurations, 
# some users also export the app object directly.
app_instance = app
