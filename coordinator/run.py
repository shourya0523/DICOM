"""Entrypoint for local development.

    python run.py

For production use a WSGI server, e.g.:
    gunicorn "app:create_app()" -b 0.0.0.0:5001
"""
from app import create_app
from config import Config

app = create_app()

if __name__ == "__main__":
    app.run(host=Config.HOST, port=Config.PORT, debug=Config.DEBUG)
