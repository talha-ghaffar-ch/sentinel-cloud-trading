"""
serve.py — Windows production server for Sentinel Web Platform
Runs Flask via Waitress (Windows-compatible WSGI server)
Usage: python serve.py
"""
import os
from dotenv import load_dotenv
load_dotenv()

from waitress import serve
from app import app

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", 80))

if __name__ == "__main__":
    print(f"\n  SENTINEL WEB PLATFORM")
    print(f"  Running on http://{HOST}:{PORT}")
    print(f"  Press CTRL+C to stop\n")
    serve(app, host=HOST, port=PORT, threads=6)
