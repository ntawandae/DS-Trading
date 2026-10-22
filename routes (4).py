import math
import random
import secrets
import time

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, session
from flask_login import current_user
from sqlalchemy import func
from ..models import db, Product, SaleItem, SourcingRequest
from ..notify import notify_staff

public_bp = Blueprint("public", __name__)


# How long one visitor's shuffled order stays the same. Long enough that pressing Back from a
# product page doesn't reshuffle the grid; short enough that returning later shows new faces.
SHUFFLE_WINDOW_SECONDS = 30 * 60


def _popularity_weights(products):
    """Weight per product id: 1 for a brand-new/never-viewed item, rising with page views and
    online units sold. Square roots keep a runaway bestseller from crowding everything else out,
    and in-stock items get a nudge since those can actually be bought today."""
    sold = dict(
        db.session.query(SaleItem.product_id, func.coalesce(func.sum(SaleItem.quantity), 0))
        .filter(SaleItem.product_id.isnot(None))
        .group_by(SaleItem.product_id)
        .all()
    )
    weights = {}
    for p in products:
        w = 1 + math.sqrt(p.view_count or 0) + 3 * math.sqrt(sold.get(p.id, 0) or 0)
        if (p.quantity or 0) > 0:
            w *= 1.5
        weights[p.id] = w
    return weights


def _weighted_shuffle(products, weights, rng):
    """Random order where heavier (more popular) products tend to come first, but any product can
    surface. (Efraimidis–Spirakis weighted sampling.)"""
    keyed = [(rng.random() ** (1.0 / weights[p.id]), p) for p in products]
    keyed.sort(key=lambda kp: kp[0], reverse=True)
    return [p for _, p in keyed]


def _discovery_order(products):
    """Order for the default catalog view. Products are shuffled within each category (popular ones
    favoured), then categories are interleaved round-robin — so 100 phones can't bury the one
    projector, and every category gets shown near the top."""
    if not session.get("shuffle_seed"):
        session["shuffle_seed"] = secrets.token_hex(6)
    bucket = int(time.time() // SHUFFLE_WINDOW_SECONDS)
    rng = random.Random(f"{session['shuffle_seed']}:{bucket}")

    weights = _popularity_weights(products)
    by_cat = {}
    for p in products:
        by_cat.setdefault(p.category or "Other", []).append(p)

    queues = {c: _weighted_shuffle(items, weights, rng) for c, items in by_cat.items()}
    cat_order = list(queues)
    rng.shuffle(cat_order)

    ordered = []
    while any(queues.values()):
        for c in cat_order:
            if queues[c]:
                ordered.append(queues[c].pop(0))
    return ordered


@public_bp.route("/")
def home():
    query = request.args.get("q", "").strip()
    active_category = request.args.get("category", "").strip()

    all_active = Product.query.filter_by(active=True).all()
    categories = sorted(set(i.category for i in all_active if i.category))

    items_q = Product.query.filter_by(active=True)
    if query:
        like = f"%{query}%"
        items_q = items_q.filter(db.or_(Product.name.ilike(like), Product.description.ilike(like)))
    if active_category:
        items_q = items_q.filter(Product.category == active_category)
    items = items_q.all()

    if query:
        # Searching: most popular matches first, then A–Z.
        weights = _popularity_weights(items)
        items.sort(key=lambda p: (-weights[p.id], p.name.lower()))
    elif active_category:
        # One category: shuffle (popular favoured) so the same items aren't always on top.
        rng = random.Random(f"{session.setdefault('shuffle_seed', secrets.token_hex(6))}:"
                            f"{int(time.time() // SHUFFLE_WINDOW_SECONDS)}")
        items = _weighted_shuffle(items, _popularity_weights(items), rng)
    else:
        items = _discovery_order(items)

    return render_template(
        "public/home.html",
        items=items,
        categories=categories,
        query=query,
        active_category=active_category,
        category_filtering=True,
    )


def _looks_like_bot(user_agent):
    ua = (user_agent or "").lower()
    return not ua or any(t in ua for t in ("bot", "crawl", "spider", "slurp", "preview", "monitor", "uptime"))


@public_bp.route("/product/<int:item_id>")
def product_detail(item_id):
    item = Product.query.get_or_404(item_id)
    if not item.active:
        abort(404)

    # Count each visitor once per product (per session); ignore staff, bots and uptime pings.
    viewed = session.get("viewed_products", [])
    is_staff = current_user.is_authenticated and current_user.is_worker
    if item.id not in viewed and not is_staff and not _looks_like_bot(request.user_agent.string):
        Product.query.filter_by(id=item.id).update(
            {Product.view_count: func.coalesce(Product.view_count, 0) + 1}
        )
        db.session.commit()
        viewed.append(item.id)
        session["viewed_products"] = viewed[-200:]

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
