"""Application factory for the coordinator service.

The coordinator is the central portal in the federated-search architecture:
the frontend calls it, and it fans queries out to the hospital nodes,
aggregates results, and (later) enforces auth / privacy policy.

Usage:
    from app import create_app
    app = create_app()
"""
from flask import Flask
from flask_cors import CORS

from config import Config


def create_app(config_object: type = Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_object)

    # Allow the frontend service to call this API cross-origin.
    origins = config_object.CORS_ORIGINS
    CORS(app, origins="*" if origins == "*" else [o.strip() for o in origins.split(",")])

    # Register route blueprints. Routes/contracts are defined in app/routes.py.
    from app.routes import bp as api_bp
    app.register_blueprint(api_bp)

    return app
