from app import app

def handler(request, context):
    return app(request, context)
