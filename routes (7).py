from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, login_user, current_user
from ..models import db, User, Customer, Sale, Quotation, SourcingRequest, Notification, Product
from ..email_utils import mail_enabled
from ..services import convert_quotation_to_sale
from ..notify import notify_staff, notify_client

account_bp = Blueprint("account", __name__)


@account_bp.before_request
def restrict_to_clients():
    from flask_login import current_user as cu
    if request.endpoint == "account.register":
        return  # public
    if not cu.is_authenticated or cu.role != "client":
        abort(403)


@account_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        company = request.form.get("company", "").strip()
        phone = request.form.get("phone", "").strip()
        country = request.form.get("country", "").strip()

        if not name or not email or not password:
            flash("Please fill in your name, email, and a password.", "error")
            return render_template("account/register.html")

        if User.query.filter_by(username=email).first():
            flash("An account with that email already exists. Try logging in instead.", "error")
            return render_template("account/register.html")

        customer = Customer(name=name, company=company, phone=phone, email=email, country=country)
        db.session.add(customer)
        db.session.flush()

        user = User(username=email, full_name=name, role="client", customer_id=customer.id)
        user.set_password(password)
        # If no email service is configured, there's no way for the client to click a verification
        # link, so don't hold that against them — treat it as verified rather than permanently unverified.
        user.email_verified = not mail_enabled()
        db.session.add(user)
        db.session.commit()

        if mail_enabled():
            from ..auth.routes import send_verification_email
            send_verification_email(user)

        login_user(user)
        if mail_enabled():
            flash("Welcome! Your account has been created — check your email to verify your address.", "success")
        else:
            flash("Welcome! Your account has been created.", "success")
        return redirect(url_for("account.dashboard"))

    return render_template("account/register.html")


@account_bp.route("/")
@login_required
def dashboard():
    customer = current_user.customer
    orders = Sale.query.filter_by(customer_id=customer.id).order_by(Sale.created_at.desc()).all() if customer else []
    quotes = Quotation.query.filter_by(customer_id=customer.id).order_by(Quotation.created_at.desc()).all() if customer else []

    query = request.args.get("q", "").strip()
    items_q = Product.query.filter_by(active=True)
    if query:
        like = f"%{query}%"
        items_q = items_q.filter(db.or_(Product.name.ilike(like), Product.description.ilike(like)))
    items = items_q.order_by(Product.category, Product.name).all()
    categories = sorted(set(i.category for i in Product.query.filter_by(active=True).all() if i.category))

    return render_template("account/dashboard.html", customer=customer, orders=orders, quotes=quotes,
                            items=items, categories=categories, query=query)


@account_bp.route("/notifications")
@login_required
def notifications():
    notes = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.created_at.desc()).all()
    unread = [n for n in notes if not n.is_read]
    for n in unread:
        n.is_read = True
    if unread:
        db.session.commit()
    return render_template("account/notifications.html", notifications=notes)


@account_bp.route("/orders/<int:sale_id>")
@login_required
def order_detail(sale_id):
    sale = Sale.query.get_or_404(sale_id)
    if not current_user.customer or sale.customer_id != current_user.customer.id:
        abort(403)
    return render_template("account/order_detail.html", sale=sale)


@account_bp.route("/quotations/<int:quote_id>")
@login_required
def quotation_detail(quote_id):
    quote = Quotation.query.get_or_404(quote_id)
    if not current_user.customer or quote.customer_id != current_user.customer.id:
        abort(403)
    return render_template("account/quotation_detail.html", quote=quote)


@account_bp.route("/quotations/<int:quote_id>/accept", methods=["POST"])
@login_required
def quotation_accept(quote_id):
    quote = Quotation.query.get_or_404(quote_id)
    if not current_user.customer or quote.customer_id != current_user.customer.id:
        abort(403)
    if quote.status in ("accepted", "rejected"):
        flash("This quotation has already been actioned.", "error")
        return redirect(url_for("account.quotation_detail", quote_id=quote.id))

    sale = convert_quotation_to_sale(quote, source="online")
    quote.status = "accepted"
    db.session.commit()
    notify_staff(
        f"Quotation {quote.quote_number} accepted — new order #{sale.id}",
        f"{quote.display_customer} accepted quotation {quote.quote_number} and it's now Order #{sale.id}, "
        f"total ${sale.total:.2f}.\n\nFulfillment method: {sale.fulfillment_method or 'not set'}\n\n"
        f"Head to the admin panel to arrange fulfillment.",
    )
    flash(f"Order placed! Your order number is #{sale.id}.", "success")
    return redirect(url_for("account.order_detail", sale_id=sale.id))


@account_bp.route("/quotations/<int:quote_id>/reject", methods=["POST"])
@login_required
def quotation_reject(quote_id):
    quote = Quotation.query.get_or_404(quote_id)
    if not current_user.customer or quote.customer_id != current_user.customer.id:
        abort(403)
    if quote.status in ("accepted", "rejected"):
        flash("This quotation has already been actioned.", "error")
        return redirect(url_for("account.quotation_detail", quote_id=quote.id))

    quote.status = "rejected"
    db.session.commit()
    notify_staff(
        f"Quotation {quote.quote_number} declined",
        f"{quote.display_customer} declined quotation {quote.quote_number} "
        f"(total was ${quote.total:.2f}). You may want to follow up.",
    )
    flash("Quotation declined.", "success")
    return redirect(url_for("account.dashboard"))


@account_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    customer = current_user.customer

    if request.method == "POST":
        form_type = request.form.get("form_type")

        if form_type == "personal":
            if customer:
                customer.name = request.form.get("name", "").strip() or customer.name
                customer.company = request.form.get("company", "").strip()
                customer.phone = request.form.get("phone", "").strip()
                customer.country = request.form.get("country", "").strip()
                customer.address = request.form.get("address", "").strip()
            current_user.full_name = request.form.get("name", "").strip() or current_user.full_name
            db.session.commit()
            flash("Your details have been updated.", "success")
            return redirect(url_for("account.profile"))

        elif form_type == "email":
            new_email = request.form.get("email", "").strip().lower()
            password = request.form.get("current_password", "")
            if not new_email:
                flash("Enter a new email address.", "error")
                return redirect(url_for("account.profile"))
            if not current_user.check_password(password):
                flash("Current password is incorrect — email not changed.", "error")
                return redirect(url_for("account.profile"))
            if new_email == current_user.username:
                flash("That's already your current email.", "error")
                return redirect(url_for("account.profile"))
            if User.query.filter_by(username=new_email).first():
                flash("Another account already uses that email.", "error")
                return redirect(url_for("account.profile"))

            current_user.username = new_email
            if customer:
                customer.email = new_email
            # A changed email needs re-verifying — unless we have no way to send that
            # verification (no mail configured), in which case there's no benefit to
            # blocking them on an email they can never confirm.
            current_user.email_verified = not mail_enabled()
            db.session.commit()

            if mail_enabled():
                from ..auth.routes import send_verification_email
                send_verification_email(current_user)
                flash("Email updated — check your new inbox for a verification link.", "success")
            else:
                flash("Email updated.", "success")
            return redirect(url_for("account.profile"))

        elif form_type == "password":
            current_password = request.form.get("current_password", "")
            new_password = request.form.get("new_password", "")
            confirm_password = request.form.get("confirm_password", "")
            if not current_user.check_password(current_password):
                flash("Current password is incorrect.", "error")
                return redirect(url_for("account.profile"))
            if len(new_password) < 6:
                flash("New password must be at least 6 characters.", "error")
                return redirect(url_for("account.profile"))
            if new_password != confirm_password:
                flash("New passwords don't match.", "error")
                return redirect(url_for("account.profile"))
            current_user.set_password(new_password)
            db.session.commit()
            flash("Password changed.", "success")
            return redirect(url_for("account.profile"))

    return render_template("account/profile.html", customer=customer)


@account_bp.route("/resend-verification", methods=["POST"])
@login_required
def resend_verification():
    if current_user.email_verified:
        flash("Your email is already verified.", "success")
    elif not mail_enabled():
        flash("Email isn't configured on this server yet — contact an administrator to verify your account manually.", "error")
    else:
        from ..auth.routes import send_verification_email
        send_verification_email(current_user)
        flash("Verification email sent — check your inbox.", "success")
    return redirect(request.referrer or url_for("account.profile"))


@account_bp.route("/request", methods=["GET", "POST"])
@login_required
def sourcing_request():
    customer = current_user.customer
    if request.method == "POST":
        req = SourcingRequest(
            customer_id=customer.id if customer else None,
            customer_name=customer.name if customer else current_user.full_name,
            contact=request.form.get("contact") or (customer.phone if customer else ""),
            country=customer.country if customer else "",
            product_name=request.form.get("product_name", "").strip(),
            category=request.form.get("category", "").strip(),
            quantity=request.form.get("quantity", "").strip(),
            notes=request.form.get("notes", "").strip(),
        )
        db.session.add(req)
        db.session.commit()
        notify_staff(
            f"New sourcing request: {req.product_name}",
            f"New sourcing request from a logged-in client.\n\n"
            f"From: {req.customer_name} ({req.contact})\n"
            f"Product: {req.product_name}\n"
            f"Category: {req.category or '—'}\n"
            f"Quantity: {req.quantity or '—'}\n"
            f"Notes: {req.notes or '—'}\n\n"
            f"View it in the admin panel under Sourcing Requests.",
        )
        flash("Your sourcing request has been submitted. We'll be in touch.", "success")
        return redirect(url_for("account.dashboard"))
    return render_template("account/request.html", customer=customer)
