from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_user, logout_user, login_required, current_user
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from ..models import db, User
from ..email_utils import send_email, mail_enabled

auth_bp = Blueprint("auth", __name__)

RESET_TOKEN_MAX_AGE = 3600  # 1 hour


def _serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"])


def generate_token(user_id, salt):
    return _serializer().dumps(user_id, salt=salt)


def verify_token(token, salt, max_age=RESET_TOKEN_MAX_AGE):
    try:
        return _serializer().loads(token, salt=salt, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("admin.dashboard") if current_user.is_worker else url_for("account.dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            if not user.active:
                flash("This account has been deactivated. Contact your administrator.", "error")
                return render_template("auth/login.html")
            login_user(user)
            if user.is_worker:
                return redirect(url_for("admin.dashboard"))
            return redirect(url_for("account.dashboard"))
        flash("Invalid username or password.", "error")
    return render_template("auth/login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("public.home"))


# ---------------- Email verification (clients) ----------------
@auth_bp.route("/verify-email/<token>")
def verify_email(token):
    user_id = verify_token(token, salt="verify-email", max_age=60 * 60 * 24 * 3)  # 3 days
    if not user_id:
        flash("That verification link is invalid or has expired.", "error")
        return redirect(url_for("auth.login"))
    user = User.query.get(user_id)
    if user:
        user.email_verified = True
        db.session.commit()
        flash("Your email is verified — thanks!", "success")
    return redirect(url_for("auth.login"))


def send_verification_email(user):
    token = generate_token(user.id, salt="verify-email")
    link = url_for("auth.verify_email", token=token, _external=True)
    body = (
        f"Hi {user.full_name},\n\n"
        f"Welcome to Duoshao Trading. Please verify your email by clicking the link below:\n\n"
        f"{link}\n\n"
        f"This link expires in 3 days.\n\nDuoshao Trading Co., Ltd."
    )
    send_email(user.username, "Verify your Duoshao Trading account", body)


# ---------------- Forgot / reset password ----------------
@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        identifier = request.form.get("username", "").strip()
        user = User.query.filter_by(username=identifier).first()
        # Always show the same message whether or not the account exists, to avoid leaking who has an account.
        if user and user.active:
            token = generate_token(user.id, salt="reset-password")
            link = url_for("auth.reset_password", token=token, _external=True)
            body = (
                f"Hi {user.full_name},\n\n"
                f"We received a request to reset your Duoshao Trading password. Click the link below to choose a new one:\n\n"
                f"{link}\n\n"
                f"This link expires in 1 hour. If you didn't request this, you can safely ignore this email.\n\n"
                f"Duoshao Trading Co., Ltd."
            )
            send_email(user.username, "Reset your Duoshao Trading password", body)
        if mail_enabled():
            flash("If an account with that username/email exists, a reset link has been sent.", "success")
        else:
            flash(
                "Email isn't configured on this server yet, so we can't send a reset link automatically — "
                "please contact an administrator to reset your password directly.",
                "error",
            )
        return redirect(url_for("auth.login"))
    return render_template("auth/forgot_password.html")


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    user_id = verify_token(token, salt="reset-password", max_age=RESET_TOKEN_MAX_AGE)
    if not user_id:
        flash("That reset link is invalid or has expired. Please request a new one.", "error")
        return redirect(url_for("auth.forgot_password"))
    user = User.query.get(user_id)
    if not user:
        flash("Account not found.", "error")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if len(password) < 6:
            flash("Password must be at least 6 characters.", "error")
            return render_template("auth/reset_password.html", token=token)
        if password != confirm:
            flash("Passwords don't match.", "error")
            return render_template("auth/reset_password.html", token=token)
        user.set_password(password)
        db.session.commit()
        flash("Password updated — you can now log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html", token=token)
