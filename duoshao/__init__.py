import os
from dotenv import load_dotenv
from flask import Flask
from flask_login import LoginManager
from .models import db, User, Notification

# Load environment variables from a .env file in the project root, if present.
# This is the file to edit for API keys / credentials — no code changes needed.
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

login_manager = LoginManager()
login_manager.login_view = "auth.login"


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")
    basedir = os.path.abspath(os.path.dirname(__file__))
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(basedir, "..", "instance", "duoshao.db")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Payment gateways — opt-in via environment variables (see duoshao/payments.py docstring)
    app.config["PAYPAL_CLIENT_ID"] = os.environ.get("PAYPAL_CLIENT_ID")
    app.config["PAYPAL_SECRET"] = os.environ.get("PAYPAL_SECRET")
    app.config["PAYPAL_MODE"] = os.environ.get("PAYPAL_MODE", "sandbox")
    app.config["STRIPE_SECRET_KEY"] = os.environ.get("STRIPE_SECRET_KEY")
    app.config["STRIPE_PUBLISHABLE_KEY"] = os.environ.get("STRIPE_PUBLISHABLE_KEY")

    # Outbound email — opt-in via environment variables (see duoshao/email_utils.py docstring)
    app.config["MAIL_SERVER"] = os.environ.get("MAIL_SERVER")
    app.config["MAIL_PORT"] = os.environ.get("MAIL_PORT", 587)
    app.config["MAIL_USE_TLS"] = os.environ.get("MAIL_USE_TLS", "true").lower() != "false"
    app.config["MAIL_USERNAME"] = os.environ.get("MAIL_USERNAME")
    app.config["MAIL_PASSWORD"] = os.environ.get("MAIL_PASSWORD")
    app.config["MAIL_DEFAULT_SENDER"] = os.environ.get("MAIL_DEFAULT_SENDER")
    app.config["STAFF_NOTIFICATION_EMAIL"] = os.environ.get("STAFF_NOTIFICATION_EMAIL")

    os.makedirs(os.path.join(basedir, "..", "instance"), exist_ok=True)

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

    @app.context_processor
    def inject_cart_count():
        return {"cart_count": cart_count()}

    @app.context_processor
    def inject_notification_count():
        from flask_login import current_user
        if current_user.is_authenticated and current_user.is_client:
            count = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
            return {"unread_notifications": count}
        return {"unread_notifications": 0}

    @app.template_global()
    def asset_version(static_relpath):
        """Returns the file's last-modified time as a cache-busting query string value.
        Used as {{ url_for('static', filename='css/style.css') }}?v={{ asset_version('css/style.css') }}
        so browsers fetch the new file immediately after a deploy instead of serving a
        stale cached copy — no manual version number to remember to bump."""
        full_path = os.path.join(app.static_folder, static_relpath)
        try:
            return int(os.path.getmtime(full_path))
        except OSError:
            return 0

    with app.app_context():
        db.create_all()

    return app
