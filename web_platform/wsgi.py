"""wsgi.py — Production entry point for gunicorn on EC2"""
from app import app

if __name__ == "__main__":
    app.run()
