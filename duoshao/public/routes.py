from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from ..models import db, Product, SourcingRequest
from ..notify import notify_staff

public_bp = Blueprint("public", __name__)


@public_bp.route("/")
def home():
    query = request.args.get("q", "").strip()
    items_q = Product.query.filter_by(active=True)
    if query:
        like = f"%{query}%"
        items_q = items_q.filter(db.or_(Product.name.ilike(like), Product.description.ilike(like)))
    # Sorted by category/name for now. A "most requested first" sort for the All tab
    # is a natural next step once we're tracking order/view counts per product.
    items = items_q.order_by(Product.category, Product.name).all()
    categories = sorted(set(i.category for i in Product.query.filter_by(active=True).all() if i.category))
    return render_template("public/home.html", items=items, categories=categories, query=query)


@public_bp.route("/product/<int:item_id>")
def product_detail(item_id):
    item = Product.query.get_or_404(item_id)
    if not item.active:
        abort(404)
    return render_template("public/product_detail.html", item=item)


@public_bp.route("/request", methods=["GET", "POST"])
def sourcing_request():
    if request.method == "POST":
        req = SourcingRequest(
            customer_name=request.form.get("customer_name", "").strip(),
            contact=request.form.get("contact", "").strip(),
            country=request.form.get("country", "").strip(),
            product_name=request.form.get("product_name", "").strip(),
            category=request.form.get("category", "").strip(),
            quantity=request.form.get("quantity", "").strip(),
            notes=request.form.get("notes", "").strip(),
        )
        if not req.customer_name or not req.contact or not req.product_name:
            flash("Please fill in your name, contact, and the product you need.", "error")
            return render_template("public/request.html", prefill_product=request.form.get("product_name", ""))
        db.session.add(req)
        db.session.commit()
        notify_staff(
            f"New sourcing request: {req.product_name}",
            f"New sourcing request submitted.\n\n"
            f"From: {req.customer_name} ({req.contact})\n"
            f"Country: {req.country or '—'}\n"
            f"Product: {req.product_name}\n"
            f"Category: {req.category or '—'}\n"
            f"Quantity: {req.quantity or '—'}\n"
            f"Notes: {req.notes or '—'}\n\n"
            f"View it in the admin panel under Sourcing Requests.",
        )
        return render_template("public/request_success.html", req=req)
    return render_template("public/request.html", prefill_product=request.args.get("product", ""))


@public_bp.route("/about")
def about():
    return render_template("public/about.html")
