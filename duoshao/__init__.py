import os
from dotenv import load_dotenv
from flask import Flask
from flask_login import LoginManager
from supabase import create_client
from .models import db, User, Notification

# Load environment variables from a .env file in the project root, if present.
# This is the file to edit for API keys / credentials — no code changes needed.
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

login_manager = LoginManager()
login_manager.login_view = "auth.login"

SUPABASE_BUCKET = "product-images"


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")
    basedir = os.path.abspath(os.path.dirname(__file__))

    # Database
    # If DATABASE_URL is provided (e.g. Supabase PostgreSQL), use it.
    # Otherwise, fall back to SQLite for local development.
    database_url = os.environ.get("DATABASE_URL")

    if database_url:
        # Some providers use the old postgres:// prefix.
        # SQLAlchemy expects postgresql:// instead.
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)

        app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    else:
        # Local development: use the existing SQLite database.
        sqlite_path = os.path.join(basedir, "..", "instance", "duoshao.db")
        os.makedirs(os.path.dirname(sqlite_path), exist_ok=True)
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + sqlite_path

    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Supabase Storage
    # Used for permanent product-image storage on Render.
    app.config["SUPABASE_URL"] = os.environ.get("SUPABASE_URL")
    app.config["SUPABASE_SERVICE_KEY"] = os.environ.get("SUPABASE_SERVICE_KEY")
    app.config["SUPABASE_BUCKET"] = SUPABASE_BUCKET

    # Payment gateways — opt-in via environment variables
    app.config["PAYPAL_CLIENT_ID"] = os.environ.get("PAYPAL_CLIENT_ID")
    app.config["PAYPAL_SECRET"] = os.environ.get("PAYPAL_SECRET")
    app.config["PAYPAL_MODE"] = os.environ.get("PAYPAL_MODE", "sandbox")
    app.config["STRIPE_SECRET_KEY"] = os.environ.get("STRIPE_SECRET_KEY")
    app.config["STRIPE_PUBLISHABLE_KEY"] = os.environ.get("STRIPE_PUBLISHABLE_KEY")

    # Outbound email — opt-in via environment variables
    app.config["MAIL_SERVER"] = os.environ.get("MAIL_SERVER")
    app.config["MAIL_PORT"] = os.environ.get("MAIL_PORT", 587)
    app.config["MAIL_USE_TLS"] = (
        os.environ.get("MAIL_USE_TLS", "true").lower() != "false"
    )
    app.config["MAIL_USERNAME"] = os.environ.get("MAIL_USERNAME")
    app.config["MAIL_PASSWORD"] = os.environ.get("MAIL_PASSWORD")
    app.config["MAIL_DEFAULT_SENDER"] = os.environ.get("MAIL_DEFAULT_SENDER")
    app.config["STAFF_NOTIFICATION_EMAIL"] = os.environ.get(
        "STAFF_NOTIFICATION_EMAIL"
    )

    db.init_app(app)
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    from .public.routes import public_bp
    from .auth.routes import auth_bp
    from .admin.routes import admin_bp
    from .account.routes import account_bp
    from .cart.routes import cart_bp, cart_count

    app.register_blueprint(public_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(account_bp, url_prefix="/account")
    app.register_blueprint(cart_bp)

    # Health check endpoint for Render/UptimeRobot
    @app.route("/health")
    def health():
        return "OK", 200

    @app.context_processor
    def inject_cart_count():
        return {"cart_count": cart_count()}

    @app.context_processor
    def inject_notification_count():
        from flask_login import current_user

        if current_user.is_authenticated and current_user.is_client:
            count = Notification.query.filter_by(
                user_id=current_user.id,
                is_read=False
            ).count()
            return {"unread_notifications": count}

        return {"unread_notifications": 0}

    # ------------------------------------------------------------------
    # Supabase Storage helpers
    # ------------------------------------------------------------------

    def get_supabase_client():
        """
        Create and cache the Supabase client for this Flask application.
        The service key is server-side only and must never be exposed
        to browser/client-side code.
        """
        client = app.extensions.get("supabase")

        if client is None:
            supabase_url = app.config.get("SUPABASE_URL")
            supabase_key = app.config.get("SUPABASE_SERVICE_KEY")

            if not supabase_url or not supabase_key:
                raise RuntimeError(
                    "SUPABASE_URL and SUPABASE_SERVICE_KEY must be configured."
                )

            client = create_client(supabase_url, supabase_key)
            app.extensions["supabase"] = client

        return client

    @app.template_global()
    def product_image_url(path):
        """
        Convert a stored Supabase Storage path into its public URL.

        The database stores paths such as:
            catalog/item-1.jpg

        This function converts them into the public Supabase Storage URL.

        It also supports existing full URLs, which makes the transition
        safer if any database records already contain complete URLs.
        """
        if not path:
            return ""

        # If the database already contains a complete URL,
        # return it unchanged.
        if path.startswith("http://") or path.startswith("https://"):
            return path

        client = get_supabase_client()

        return client.storage.from_(
            app.config["SUPABASE_BUCKET"]
        ).get_public_url(path)

    @app.template_global()
    def asset_version(static_relpath):
        """Returns the file's last-modified time as a cache-busting query string value."""
        full_path = os.path.join(app.static_folder, static_relpath)

        try:
            return int(os.path.getmtime(full_path))
        except OSError:
            return 0

    # Create database tables automatically if they don't already exist.
    # With DATABASE_URL set on Render, this creates them in Supabase PostgreSQL.
    with app.app_context():
        db.create_all()

    return app
