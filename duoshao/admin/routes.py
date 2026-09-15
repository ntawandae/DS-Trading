import os
import uuid
from datetime import datetime, date, timedelta
from calendar import monthrange
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, current_app
from flask_login import login_required, current_user
from sqlalchemy import func
from werkzeug.security import generate_password_hash
from ..models import (
    db, SourcingRequest, Product, Customer,
    Sale, SaleItem, Receipt, Expense, Employee, PayrollRun,
    Quotation, QuotationItem, FloatTransaction, User,
    SaleStatusEvent, Notification, FULFILLMENT_STAGES
)
from ..permissions import admin_required, finance_required, staff_required
from ..services import convert_quotation_to_sale, expire_stale_online_orders
from ..notify import notify_client

admin_bp = Blueprint("admin", __name__)


@admin_bp.before_request
@login_required
def restrict_to_staff():
    """Nobody with role='client' gets into /admin, ever."""
    if not current_user.is_authenticated or current_user.role not in ("admin", "finance_hr", "worker"):
        abort(403)


def catalog_price_map():
    """Small JSON-able map of product name -> price, for JS autofill on forms."""
    items = Product.query.filter_by(active=True).all()
    return {i.name: (i.unit_price_usd or 0) for i in items}


# ---------- Dashboard ----------
@admin_bp.route("/")
@login_required
def dashboard():
    expire_stale_online_orders()  # release any abandoned PayPal/Card reservations first
    total_sales = db.session.query(func.coalesce(func.sum(Sale.id * 0), 0)).scalar()  # placeholder unused
    sales = Sale.query.all()
    total_revenue = sum(s.total for s in sales)
    total_received = sum(s.amount_received for s in sales)
    total_outstanding = round(total_revenue - total_received, 2)

    total_expenses = db.session.query(func.coalesce(func.sum(Expense.amount_usd), 0)).scalar()

    pending_payroll = db.session.query(func.coalesce(func.sum(PayrollRun.amount_usd), 0)) \
        .filter(PayrollRun.status == "pending").scalar()

    low_stock = Product.query.filter(Product.quantity <= Product.reorder_level, Product.active == True).all()
    new_requests = SourcingRequest.query.filter_by(status="new").order_by(SourcingRequest.created_at.desc()).all()

    stock_value = db.session.query(
        func.coalesce(func.sum(Product.quantity * Product.unit_cost_usd), 0)
    ).scalar()

    recent_sales = Sale.query.order_by(Sale.created_at.desc()).limit(6).all()

    float_balance = compute_float_balance()

    return render_template(
        "admin/dashboard.html",
        total_revenue=total_revenue,
        total_received=total_received,
        total_outstanding=total_outstanding,
        total_expenses=total_expenses,
        pending_payroll=pending_payroll,
        low_stock=low_stock,
        new_requests=new_requests,
        stock_value=stock_value,
        recent_sales=recent_sales,
        float_balance=float_balance,
    )


def compute_float_balance():
    manual = db.session.query(func.coalesce(func.sum(FloatTransaction.amount_usd), 0)).scalar()
    cash_in = db.session.query(func.coalesce(func.sum(Receipt.amount), 0)).filter(Receipt.method == "cash").scalar()
    cash_out_expenses = db.session.query(func.coalesce(func.sum(Expense.amount_usd), 0)).filter(Expense.method == "cash").scalar()
    return round(manual + cash_in - cash_out_expenses, 2)


# ---------- Sourcing requests (from public site) ----------
@admin_bp.route("/requests")
@login_required
def requests_list():
    status_filter = request.args.get("status", "all")
    q = SourcingRequest.query
    if status_filter != "all":
        q = q.filter_by(status=status_filter)
    reqs = q.order_by(SourcingRequest.created_at.desc()).all()
    return render_template("admin/requests.html", reqs=reqs, status_filter=status_filter)


@admin_bp.route("/requests/<int:req_id>/status", methods=["POST"])
@login_required
def update_request_status(req_id):
    req = SourcingRequest.query.get_or_404(req_id)
    req.status = request.form.get("status", req.status)
    db.session.commit()
    flash("Request updated.", "success")
    return redirect(url_for("admin.requests_list"))


# ---------- Products (single source of truth — public catalog + stock, together) ----------
ALLOWED_IMAGE_EXT = {"png", "jpg", "jpeg", "webp"}


def _all_categories():
    rows = (db.session.query(Product.category)
            .filter(Product.category.isnot(None), Product.category != "")
            .distinct().order_by(Product.category).all())
    return [r[0] for r in rows]


def _resolve_category(form):
    """Reads the category dropdown + optional new-category field.
    Returns (category_string, error_message). error_message is None on success."""
    selected = form.get("category", "").strip()
    new_cat = form.get("new_category", "").strip()
    existing = _all_categories()
    existing_lower = {c.lower(): c for c in existing}

    if selected == "__new__":
        if not new_cat:
            return None, "Please type a name for the new category, or pick an existing one."
        if new_cat.lower() in existing_lower:
            return None, (f'The category "{existing_lower[new_cat.lower()]}" already exists — '
                           f"please select it from the dropdown instead of adding it again.")
        return new_cat, None

    if not selected:
        return None, "Please choose a category, or add a new one."
    return selected, None


def _generate_sku(item):
    """Auto-SKU when the admin leaves the field blank: a category prefix plus the
    product's own id, which is unique by definition — e.g. "KIT-0042"."""
    prefix = "".join(ch for ch in (item.category or "GEN").upper() if ch.isalnum())[:3] or "GEN"
    return f"{prefix}-{item.id:04d}"


def _save_catalog_image(file_storage, item_id):
    ext = file_storage.filename.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_IMAGE_EXT:
        return None
    filename = f"item-{item_id}.{ext}"
    folder = current_app.config["UPLOAD_FOLDER"]
    os.makedirs(folder, exist_ok=True)
    file_storage.save(os.path.join(folder, filename))
    return filename


def _save_gallery_image(file_storage, item_id):
    """Additional photos for the same product — unique filenames since there can be several."""
    ext = file_storage.filename.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_IMAGE_EXT:
        return None
    filename = f"item-{item_id}-{uuid.uuid4().hex[:8]}.{ext}"
    folder = current_app.config["UPLOAD_FOLDER"]
    os.makedirs(folder, exist_ok=True)
    file_storage.save(os.path.join(folder, filename))
    return filename


@admin_bp.route("/products", methods=["GET", "POST"])
@login_required
def products():
    if request.method == "POST":
        category, cat_error = _resolve_category(request.form)
        if cat_error:
            flash(cat_error, "error")
            return redirect(url_for("admin.products"))

        item = Product(
            name=request.form.get("name", "").strip(),
            sku=request.form.get("sku", "").strip() or None,
            category=category,
            description=request.form.get("description", "").strip(),
            quantity=int(request.form.get("quantity") or 0),
            reorder_level=int(request.form.get("reorder_level") or 5),
            unit_cost_usd=float(request.form.get("unit_cost_usd") or 0),
            unit_price_usd=float(request.form.get("unit_price_usd") or 0),
            variant_label=request.form.get("variant_label", "").strip() or None,
            variant_options=request.form.get("variant_options", "").strip() or None,
            location=request.form.get("location", "").strip() or None,
        )
        db.session.add(item)
        db.session.flush()

        if not item.sku:
            item.sku = _generate_sku(item)

        image_file = request.files.get("image")
        if image_file and image_file.filename:
            filename = _save_catalog_image(image_file, item.id)
            if filename:
                item.image_filename = filename

        gallery_files = [f for f in request.files.getlist("gallery_images") if f and f.filename]
        saved_gallery = [fn for fn in (_save_gallery_image(f, item.id) for f in gallery_files) if fn]
        if saved_gallery:
            item.gallery_images = ", ".join(saved_gallery)

        db.session.commit()
        flash(f"{item.name} added (SKU {item.sku}) — showing publicly as \"{item.availability.replace('_',' ').title()}\" based on its stock quantity.", "success")
        return redirect(url_for("admin.products", edit_id=item.id))

    items = Product.query.order_by(Product.category, Product.name).all()
    categories = _all_categories()

    q = request.args.get("q", "").strip()
    edit_id = request.args.get("edit_id", type=int)
    edit_candidates_q = Product.query
    if q:
        like = f"%{q}%"
        edit_candidates_q = edit_candidates_q.filter(db.or_(Product.name.ilike(like), Product.sku.ilike(like)))
    edit_candidates = edit_candidates_q.order_by(Product.name).all()
    edit_item = Product.query.get(edit_id) if edit_id else None

    return render_template("admin/products.html", items=items, categories=categories,
                            q=q, edit_candidates=edit_candidates, edit_item=edit_item)


@admin_bp.route("/products/<int:item_id>/toggle", methods=["POST"])
@login_required
def products_toggle(item_id):
    item = Product.query.get_or_404(item_id)
    item.active = not item.active
    db.session.commit()
    return redirect(url_for("admin.products"))


@admin_bp.route("/products/<int:item_id>/adjust", methods=["POST"])
@login_required
def products_adjust(item_id):
    item = Product.query.get_or_404(item_id)
    delta = int(request.form.get("delta", 0))
    item.quantity = max(0, item.quantity + delta)
    db.session.commit()
    return redirect(url_for("admin.products"))


@admin_bp.route("/products/<int:item_id>/edit", methods=["GET", "POST"])
@login_required
def products_edit(item_id):
    item = Product.query.get_or_404(item_id)

    if request.method == "POST":
        category, cat_error = _resolve_category(request.form)
        if cat_error:
            flash(cat_error, "error")
            return redirect(url_for("admin.products", edit_id=item.id))

        item.name = request.form.get("name", "").strip() or item.name
        item.sku = request.form.get("sku", "").strip() or item.sku or _generate_sku(item)
        item.category = category
        item.description = request.form.get("description", "").strip()
        item.quantity = int(request.form.get("quantity") or 0)
        item.reorder_level = int(request.form.get("reorder_level") or 5)
        item.unit_cost_usd = float(request.form.get("unit_cost_usd") or 0)
        item.unit_price_usd = float(request.form.get("unit_price_usd") or 0)
        item.variant_label = request.form.get("variant_label", "").strip() or None
        item.variant_options = request.form.get("variant_options", "").strip() or None
        item.location = request.form.get("location", "").strip() or None

        image_file = request.files.get("image")
        if image_file and image_file.filename:
            filename = _save_catalog_image(image_file, item.id)
            if filename:
                item.image_filename = filename

        # Remove any gallery images the admin checked for removal, then append newly uploaded ones.
        gallery = item.gallery_filenames
        remove_list = request.form.getlist("remove_images")
        if remove_list:
            folder = current_app.config["UPLOAD_FOLDER"]
            for fn in remove_list:
                try:
                    os.remove(os.path.join(folder, fn))
                except OSError:
                    pass
            gallery = [fn for fn in gallery if fn not in remove_list]

        new_gallery_files = [f for f in request.files.getlist("gallery_images") if f and f.filename]
        gallery.extend(fn for fn in (_save_gallery_image(f, item.id) for f in new_gallery_files) if fn)
        item.gallery_images = ", ".join(gallery) if gallery else None

        db.session.commit()
        flash(f"{item.name} updated.", "success")
        return redirect(url_for("admin.products", edit_id=item.id))

    return render_template("admin/product_edit.html", item=item, categories=_all_categories())


# ---------- Customers ----------
@admin_bp.route("/customers", methods=["GET", "POST"])
@login_required
def customers():
    if request.method == "POST":
        cust = Customer(
            name=request.form.get("name", "").strip(),
            company=request.form.get("company", "").strip(),
            phone=request.form.get("phone", "").strip(),
            email=request.form.get("email", "").strip(),
            country=request.form.get("country", "").strip(),
        )
        db.session.add(cust)
        db.session.commit()
        flash("Customer added.", "success")
        return redirect(url_for("admin.customers"))
    custs = Customer.query.order_by(Customer.name).all()
    return render_template("admin/customers.html", customers=custs)


# ---------- Sales ----------
@admin_bp.route("/sales")
@login_required
def sales_list():
    sales = Sale.query.order_by(Sale.created_at.desc()).all()
    return render_template("admin/sales.html", sales=sales)


@admin_bp.route("/sales/new", methods=["GET", "POST"])
@login_required
def sales_new():
    customers = Customer.query.order_by(Customer.name).all()
    if request.method == "POST":
        customer_id = request.form.get("customer_id") or None
        sale = Sale(
            customer_id=int(customer_id) if customer_id else None,
            customer_name_freetext=request.form.get("customer_name_freetext", "").strip(),
            date=datetime.strptime(request.form.get("date"), "%Y-%m-%d").date() if request.form.get("date") else date.today(),
            notes=request.form.get("notes", "").strip(),
        )
        db.session.add(sale)
        db.session.flush()  # get sale.id before commit

        descriptions = request.form.getlist("item_description")
        qtys = request.form.getlist("item_quantity")
        prices = request.form.getlist("item_price")
        for desc, qty, price in zip(descriptions, qtys, prices):
            if desc.strip():
                db.session.add(SaleItem(
                    sale_id=sale.id,
                    description=desc.strip(),
                    quantity=float(qty or 1),
                    unit_price_usd=float(price or 0),
                ))
        db.session.commit()
        flash("Sale recorded.", "success")
        return redirect(url_for("admin.sale_detail", sale_id=sale.id))
    catalog_items = Product.query.filter_by(active=True).order_by(Product.name).all()
    return render_template("admin/sale_new.html", customers=customers, catalog_items=catalog_items)


@admin_bp.route("/sales/<int:sale_id>")
@login_required
def sale_detail(sale_id):
    sale = Sale.query.get_or_404(sale_id)
    return render_template("admin/sale_detail.html", sale=sale, fulfillment_stages=FULFILLMENT_STAGES)


@admin_bp.route("/sales/<int:sale_id>/fulfillment", methods=["POST"])
@login_required
def update_fulfillment(sale_id):
    sale = Sale.query.get_or_404(sale_id)
    new_status = request.form.get("status")
    note = request.form.get("note", "").strip()
    carrier = request.form.get("carrier_name", "").strip()
    tracking = request.form.get("tracking_number", "").strip()

    sale.fulfillment_status = new_status
    if carrier:
        sale.carrier_name = carrier
    if tracking:
        sale.tracking_number = tracking

    event_note = note
    if new_status == "shipped" and (carrier or tracking):
        details = []
        if carrier:
            details.append(f"Carrier: {carrier}")
        if tracking:
            details.append(f"Tracking #: {tracking}")
        event_note = (note + " — " if note else "") + " · ".join(details)

    db.session.add(SaleStatusEvent(sale_id=sale.id, status=new_status, note=event_note))

    # Notify the client (in-app + email), if this order belongs to a client account
    if sale.customer and sale.customer.user_account:
        stage_label = dict(FULFILLMENT_STAGES).get(new_status, new_status.replace("_", " ").title())
        extra = f"\n\n{note}" if note else ""
        tracking_line = f"\n\nTracking: {sale.tracking_number}" + (f" ({sale.carrier_name})" if sale.carrier_name else "") if sale.tracking_number else ""
        notify_client(
            sale.customer.user_account,
            f"Your order #{sale.id} is now: {stage_label}",
            link=url_for("account.order_detail", sale_id=sale.id),
            email_subject=f"Duoshao Trading — Order #{sale.id} update: {stage_label}",
            email_body=(
                f"Hi {sale.customer.user_account.full_name},\n\n"
                f"Your order #{sale.id} status has changed to: {stage_label}{tracking_line}{extra}\n\n"
                f"View the full order: {url_for('account.order_detail', sale_id=sale.id, _external=True)}\n\n"
                f"Duoshao Trading Co., Ltd."
            ),
        )

    db.session.commit()
    flash("Fulfillment status updated.", "success")
    return redirect(url_for("admin.sale_detail", sale_id=sale.id))


@admin_bp.route("/sales/<int:sale_id>/receipt", methods=["POST"])
@login_required
def add_receipt(sale_id):
    sale = Sale.query.get_or_404(sale_id)
    method = request.form.get("method", "bank_transfer")
    balance_due = sale.balance_due

    if balance_due <= 0:
        flash("This sale is already fully paid — nothing further is owed.", "error")
        return redirect(url_for("admin.sale_detail", sale_id=sale.id))

    change_due = 0.0

    if method == "cash":
        # For cash, the entered amount is what the customer HANDED OVER (tendered).
        # We only ever record the balance due as the receipt amount; anything above
        # that is change owed back to the customer, not extra revenue.
        tendered = float(request.form.get("amount") or 0)
        if tendered <= 0:
            flash("Enter the cash amount received.", "error")
            return redirect(url_for("admin.sale_detail", sale_id=sale.id))
        receipt_amount = min(tendered, balance_due)
        change_due = round(tendered - receipt_amount, 2)
    else:
        # Electronic / non-cash methods can't "give change" — reject anything over balance due
        # rather than silently banking an overpayment.
        amount = float(request.form.get("amount") or 0)
        if amount <= 0:
            flash("Enter a payment amount.", "error")
            return redirect(url_for("admin.sale_detail", sale_id=sale.id))
        if amount > balance_due + 0.005:
            flash(
                f"That's more than the balance due (${balance_due:.2f}). "
                f"{method.replace('_',' ').title()} payments can't be recorded above the amount owed — "
                f"please correct the amount, or use Cash if change needs to be given back.",
                "error",
            )
            return redirect(url_for("admin.sale_detail", sale_id=sale.id))
        receipt_amount = amount

    count = Receipt.query.count() + 1
    receipt = Receipt(
        receipt_number=f"DS-RCT-{datetime.utcnow().year}-{count:04d}",
        sale_id=sale.id,
        amount=receipt_amount,
        method=method,
        issued_date=date.today(),
    )
    db.session.add(receipt)
    db.session.flush()  # ensure this receipt is written before the aggregate query below

    # Query the total directly rather than via sale.amount_received — that property reads the
    # sale.receipts relationship, which may have been cached (and gone stale) earlier in this
    # same request (e.g. by the sale.balance_due access above), so it can undercount here.
    total_received = db.session.query(func.coalesce(func.sum(Receipt.amount), 0)) \
        .filter(Receipt.sale_id == sale.id).scalar()
    sale.status = "paid" if total_received >= sale.total else "partially_paid"

    if sale.customer and sale.customer.user_account:
        remaining = round(sale.total - total_received, 2)
        notify_client(
            sale.customer.user_account,
            f"Payment of ${receipt_amount:.2f} received for order #{sale.id}",
            link=url_for("account.order_detail", sale_id=sale.id),
            email_subject=f"Duoshao Trading — Payment received for order #{sale.id}",
            email_body=(
                f"Hi {sale.customer.user_account.full_name},\n\n"
                f"We've received your payment of ${receipt_amount:.2f} for order #{sale.id}.\n"
                f"Remaining balance: ${remaining:.2f}\n\n"
                f"View your order: {url_for('account.order_detail', sale_id=sale.id, _external=True)}\n\n"
                f"Duoshao Trading Co., Ltd."
            ),
        )

    db.session.commit()

    if change_due > 0:
        flash(f"Receipt {receipt.receipt_number} issued for ${receipt_amount:.2f}. Give the customer ${change_due:.2f} change.", "success")
    else:
        flash(f"Receipt {receipt.receipt_number} issued.", "success")
    return redirect(url_for("admin.sale_detail", sale_id=sale.id))


@admin_bp.route("/receipts")
@login_required
def receipts_list():
    receipts = Receipt.query.order_by(Receipt.created_at.desc()).all()
    return render_template("admin/receipts.html", receipts=receipts)


@admin_bp.route("/receipts/<int:receipt_id>")
@login_required
def receipt_detail(receipt_id):
    receipt = Receipt.query.get_or_404(receipt_id)
    return render_template("admin/receipt_detail.html", receipt=receipt)


@admin_bp.route("/sales/<int:sale_id>/receipts/combined")
@login_required
def receipt_combined(sale_id):
    sale = Sale.query.get_or_404(sale_id)
    return render_template("admin/receipt_combined.html", sale=sale)


# ---------- Expenses ----------
@admin_bp.route("/expenses", methods=["GET", "POST"])
@login_required
@staff_required
def expenses():
    if request.method == "POST":
        exp = Expense(
            date=datetime.strptime(request.form.get("date"), "%Y-%m-%d").date() if request.form.get("date") else date.today(),
            category=request.form.get("category", "").strip(),
            description=request.form.get("description", "").strip(),
            paid_to=request.form.get("paid_to", "").strip(),
            amount_usd=float(request.form.get("amount_usd") or 0),
            method=request.form.get("method", "bank_transfer"),
        )
        db.session.add(exp)
        db.session.commit()
        flash("Expense logged.", "success")
        return redirect(url_for("admin.expenses"))
    exp_list = Expense.query.order_by(Expense.date.desc()).all()
    total = sum(e.amount_usd for e in exp_list)
    return render_template("admin/expenses.html", expenses=exp_list, total=total)


# ---------- Payroll ----------
@admin_bp.route("/payroll", methods=["GET"])
@login_required
@finance_required
def payroll():
    employees = Employee.query.order_by(Employee.name).all()
    runs = PayrollRun.query.order_by(PayrollRun.created_at.desc()).limit(30).all()
    return render_template("admin/payroll.html", employees=employees, runs=runs)


@admin_bp.route("/payroll/employee", methods=["POST"])
@login_required
@finance_required
def add_employee():
    emp = Employee(
        employee_code=request.form.get("employee_code", "").strip() or None,
        name=request.form.get("name", "").strip(),
        role=request.form.get("role", "").strip(),
        department=request.form.get("department", "").strip(),
        id_number=request.form.get("id_number", "").strip(),
        phone=request.form.get("phone", "").strip(),
        date_joined=datetime.strptime(request.form.get("date_joined"), "%Y-%m-%d").date() if request.form.get("date_joined") else None,
        bank_name=request.form.get("bank_name", "").strip(),
        bank_account_number=request.form.get("bank_account_number", "").strip(),
        monthly_salary_usd=float(request.form.get("monthly_salary_usd") or 0),
    )
    db.session.add(emp)
    db.session.commit()
    flash("Employee added.", "success")
    return redirect(url_for("admin.payroll"))


@admin_bp.route("/payroll/run", methods=["POST"])
@login_required
@finance_required
def run_payroll():
    employee_id = int(request.form.get("employee_id"))
    period = request.form.get("period", "").strip()

    basic = float(request.form.get("basic_salary_usd") or 0)
    housing = float(request.form.get("housing_allowance_usd") or 0)
    transport = float(request.form.get("transport_allowance_usd") or 0)
    other_allow = float(request.form.get("other_allowance_usd") or 0)
    tax = float(request.form.get("tax_deduction_usd") or 0)
    pension = float(request.form.get("pension_deduction_usd") or 0)
    other_ded = float(request.form.get("other_deduction_usd") or 0)

    gross = basic + housing + transport + other_allow
    net = round(gross - tax - pension - other_ded, 2)

    run = PayrollRun(
        employee_id=employee_id, period=period,
        period_start=datetime.strptime(request.form.get("period_start"), "%Y-%m-%d").date() if request.form.get("period_start") else None,
        period_end=datetime.strptime(request.form.get("period_end"), "%Y-%m-%d").date() if request.form.get("period_end") else None,
        basic_salary_usd=basic, housing_allowance_usd=housing, transport_allowance_usd=transport, other_allowance_usd=other_allow,
        tax_deduction_usd=tax, pension_deduction_usd=pension, other_deduction_usd=other_ded,
        amount_usd=net, payment_method=request.form.get("payment_method", "bank_transfer"),
        status="pending",
    )
    db.session.add(run)
    db.session.commit()
    flash("Payroll run created.", "success")
    return redirect(url_for("admin.payroll"))


@admin_bp.route("/payroll/<int:run_id>/mark_paid", methods=["POST"])
@login_required
@finance_required
def mark_payroll_paid(run_id):
    run = PayrollRun.query.get_or_404(run_id)
    run.status = "paid"
    run.paid_date = date.today()
    run.payment_reference = request.form.get("payment_reference", "").strip() or run.payment_reference
    db.session.commit()
    flash("Payroll marked as paid.", "success")
    return redirect(url_for("admin.payroll"))


@admin_bp.route("/payroll/<int:run_id>/payslip")
@login_required
@finance_required
def payslip(run_id):
    run = PayrollRun.query.get_or_404(run_id)
    return render_template("admin/payslip.html", run=run)


# ---------- Quotations ----------
@admin_bp.route("/quotations")
@login_required
def quotations_list():
    quotes = Quotation.query.order_by(Quotation.created_at.desc()).all()
    return render_template("admin/quotations.html", quotes=quotes)


@admin_bp.route("/quotations/new", methods=["GET", "POST"])
@login_required
def quotation_new():
    customers = Customer.query.order_by(Customer.name).all()
    catalog_items = Product.query.filter_by(active=True).order_by(Product.name).all()

    if request.method == "POST":
        customer_id = request.form.get("customer_id") or None
        sourcing_request_id = request.form.get("sourcing_request_id") or None
        count = Quotation.query.count() + 1
        quote = Quotation(
            quote_number=f"DS-QT-{datetime.utcnow().year}-{count:04d}",
            customer_id=int(customer_id) if customer_id else None,
            customer_name_freetext=request.form.get("customer_name_freetext", "").strip(),
            sourcing_request_id=int(sourcing_request_id) if sourcing_request_id else None,
            date=datetime.strptime(request.form.get("date"), "%Y-%m-%d").date() if request.form.get("date") else date.today(),
            valid_until=datetime.strptime(request.form.get("valid_until"), "%Y-%m-%d").date() if request.form.get("valid_until") else date.today() + timedelta(days=14),
            notes=request.form.get("notes", "").strip(),
        )
        db.session.add(quote)
        db.session.flush()

        descriptions = request.form.getlist("item_description")
        qtys = request.form.getlist("item_quantity")
        prices = request.form.getlist("item_price")
        subtotal = 0.0
        new_products_added = []
        for desc, qty, price in zip(descriptions, qtys, prices):
            if desc.strip():
                name = desc.strip()
                q = float(qty or 1)
                p = float(price or 0)
                subtotal += q * p
                db.session.add(QuotationItem(
                    quotation_id=quote.id,
                    description=name,
                    quantity=q,
                    unit_price_usd=p,
                ))

                # Not something we already stock or list — add it at 0 quantity so it's
                # searchable and ready ("Available on Request") if another client asks
                # for the same thing later, instead of re-typing it from scratch each time.
                existing = Product.query.filter(Product.name.ilike(name)).first()
                if not existing and name not in new_products_added:
                    db.session.add(Product(
                        name=name,
                        category="General",
                        quantity=0,
                        unit_price_usd=p,
                    ))
                    new_products_added.append(name)

        quote.service_fee_usd = round(subtotal * Quotation.SERVICE_FEE_RATE, 2)
        quote.shipping_estimate_usd = float(request.form.get("shipping_estimate_usd") or 0)

        # Creating a quotation from a sourcing request automatically progresses that request.
        if sourcing_request_id:
            src_req = SourcingRequest.query.get(int(sourcing_request_id))
            if src_req and src_req.status == "new":
                src_req.status = "in_progress"

        db.session.commit()
        msg = f"Quotation {quote.quote_number} created."
        if new_products_added:
            msg += f" Also added to Products (at 0 stock, ready for future requests): {', '.join(new_products_added)}."
        flash(msg, "success")
        return redirect(url_for("admin.quotation_detail", quote_id=quote.id))

    # --- Prefill from a sourcing request, if we arrived here via "Create Quotation" on one ---
    prefill = None
    from_request_id = request.args.get("from_request")
    if from_request_id:
        src_req = SourcingRequest.query.get(int(from_request_id))
        if src_req:
            # Try to match an existing Customer so the dropdown pre-selects instead of duplicating.
            matched_customer = src_req.customer
            if not matched_customer:
                matched_customer = Customer.query.filter(
                    db.or_(Customer.phone == src_req.contact, Customer.email == src_req.contact)
                ).first()
            prefill = {
                "request": src_req,
                "matched_customer_id": matched_customer.id if matched_customer else None,
                "customer_name_freetext": "" if matched_customer else src_req.customer_name,
            }

    return render_template("admin/quotation_new.html", customers=customers, catalog_items=catalog_items, prefill=prefill)


@admin_bp.route("/quotations/<int:quote_id>")
@login_required
def quotation_detail(quote_id):
    quote = Quotation.query.get_or_404(quote_id)
    return render_template("admin/quotation_detail.html", quote=quote)


@admin_bp.route("/quotations/<int:quote_id>/status", methods=["POST"])
@login_required
def quotation_status(quote_id):
    quote = Quotation.query.get_or_404(quote_id)
    old_status = quote.status
    new_status = request.form.get("status", quote.status)
    quote.status = new_status

    if new_status == "sent" and old_status != "sent" and quote.customer and quote.customer.user_account:
        notify_client(
            quote.customer.user_account,
            f"You have a new quotation: {quote.quote_number}",
            link=url_for("account.quotation_detail", quote_id=quote.id),
            email_subject=f"Duoshao Trading — Quotation {quote.quote_number} ready",
            email_body=(
                f"Hi {quote.customer.user_account.full_name},\n\n"
                f"A quotation is ready for you to review: {quote.quote_number}, total ${quote.total:.2f} "
                f"(valid until {quote.valid_until.strftime('%d %B %Y') if quote.valid_until else 'further notice'}).\n\n"
                f"View and respond: {url_for('account.quotation_detail', quote_id=quote.id, _external=True)}\n\n"
                f"Duoshao Trading Co., Ltd."
            ),
        )

    db.session.commit()
    return redirect(url_for("admin.quotation_detail", quote_id=quote.id))


@admin_bp.route("/quotations/<int:quote_id>/convert", methods=["POST"])
@login_required
def quotation_convert(quote_id):
    quote = Quotation.query.get_or_404(quote_id)
    sale = convert_quotation_to_sale(quote, source="admin")
    quote.status = "accepted"
    db.session.commit()
    flash(f"Quotation {quote.quote_number} converted to Sale #{sale.id}.", "success")
    return redirect(url_for("admin.sale_detail", sale_id=sale.id))


# ---------- Reports ----------
def _period_bounds(period, offset):
    today = date.today()
    if period == "day":
        d = today + timedelta(days=offset)
        return d, d, d.strftime("%d %b %Y")
    if period == "week":
        start = today - timedelta(days=today.weekday()) + timedelta(weeks=offset)
        end = start + timedelta(days=6)
        return start, end, f"{start.strftime('%d %b')} – {end.strftime('%d %b %Y')}"
    if period == "month":
        month_index = today.month - 1 + offset
        year = today.year + month_index // 12
        month = month_index % 12 + 1
        start = date(year, month, 1)
        end = date(year, month, monthrange(year, month)[1])
        return start, end, start.strftime("%B %Y")
    if period == "quarter":
        q = (today.month - 1) // 3
        q_index = q + offset
        year = today.year + q_index // 4
        q = q_index % 4
        start_month = q * 3 + 1
        start = date(year, start_month, 1)
        end_month = start_month + 2
        end = date(year, end_month, monthrange(year, end_month)[1])
        return start, end, f"Q{q+1} {year}"
    if period == "year":
        year = today.year + offset
        return date(year, 1, 1), date(year, 12, 31), str(year)
    # default: this month
    return _period_bounds("month", 0)


@admin_bp.route("/reports")
@login_required
@finance_required
def reports():
    period = request.args.get("period", "month")
    offset = int(request.args.get("offset", 0))
    start, end, label = _period_bounds(period, offset)

    revenue = db.session.query(func.coalesce(func.sum(Receipt.amount), 0)) \
        .filter(Receipt.issued_date >= start, Receipt.issued_date <= end).scalar()
    expenses = db.session.query(func.coalesce(func.sum(Expense.amount_usd), 0)) \
        .filter(Expense.date >= start, Expense.date <= end).scalar()
    payroll_paid = db.session.query(func.coalesce(func.sum(PayrollRun.amount_usd), 0)) \
        .filter(PayrollRun.status == "paid", PayrollRun.paid_date >= start, PayrollRun.paid_date <= end).scalar()
    profit = round(revenue - expenses - payroll_paid, 2)

    sales_in_range = Sale.query.filter(Sale.date >= start, Sale.date <= end).count()
    quotes_in_range = Quotation.query.filter(Quotation.date >= start, Quotation.date <= end).count()

    expense_breakdown = db.session.query(Expense.category, func.coalesce(func.sum(Expense.amount_usd), 0)) \
        .filter(Expense.date >= start, Expense.date <= end).group_by(Expense.category).all()

    float_balance = compute_float_balance()

    return render_template(
        "admin/reports.html",
        period=period, offset=offset, label=label, start=start, end=end,
        revenue=revenue, expenses=expenses, payroll_paid=payroll_paid, profit=profit,
        sales_in_range=sales_in_range, quotes_in_range=quotes_in_range,
        expense_breakdown=expense_breakdown, float_balance=float_balance,
    )


# ---------- Float / cash ledger ----------
@admin_bp.route("/float", methods=["GET", "POST"])
@login_required
@staff_required
def float_ledger():
    if request.method == "POST":
        entry = FloatTransaction(
            date=datetime.strptime(request.form.get("date"), "%Y-%m-%d").date() if request.form.get("date") else date.today(),
            description=request.form.get("description", "").strip(),
            amount_usd=float(request.form.get("amount_usd") or 0),
        )
        db.session.add(entry)
        db.session.commit()
        flash("Float entry recorded.", "success")
        return redirect(url_for("admin.float_ledger"))

    manual_entries = FloatTransaction.query.order_by(FloatTransaction.date.desc(), FloatTransaction.id.desc()).all()
    cash_receipts = Receipt.query.filter_by(method="cash").order_by(Receipt.issued_date.desc()).all()
    cash_expenses = Expense.query.filter_by(method="cash").order_by(Expense.date.desc()).all()

    ledger = []
    for e in manual_entries:
        ledger.append({"date": e.date, "desc": e.description, "amount": e.amount_usd})
    for r in cash_receipts:
        ledger.append({"date": r.issued_date, "desc": f"Cash receipt {r.receipt_number} (Sale #{r.sale_id})", "amount": r.amount})
    for x in cash_expenses:
        ledger.append({"date": x.date, "desc": f"Cash expense — {x.description or x.category}", "amount": -x.amount_usd})
    ledger.sort(key=lambda x: x["date"], reverse=True)

    balance = compute_float_balance()
    return render_template("admin/float.html", ledger=ledger, balance=balance)


# ---------- User management (admin only) ----------
@admin_bp.route("/users")
@login_required
@admin_required
def users_list():
    users = User.query.order_by(User.role, User.full_name).all()
    return render_template("admin/users.html", users=users)


@admin_bp.route("/users/new", methods=["POST"])
@login_required
@admin_required
def users_new():
    username = request.form.get("username", "").strip()
    if User.query.filter_by(username=username).first():
        flash("That username is already taken.", "error")
        return redirect(url_for("admin.users_list"))
    user = User(
        username=username,
        full_name=request.form.get("full_name", "").strip(),
        role=request.form.get("role", "worker"),
    )
    user.set_password(request.form.get("password") or "changeme123")
    db.session.add(user)
    db.session.commit()
    flash(f"Account created for {user.full_name} ({user.role}).", "success")
    return redirect(url_for("admin.users_list"))


@admin_bp.route("/users/<int:user_id>/toggle", methods=["POST"])
@login_required
@admin_required
def users_toggle(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash("You can't deactivate your own account.", "error")
        return redirect(url_for("admin.users_list"))
    user.active = not user.active
    db.session.commit()
    flash("Account updated.", "success")
    return redirect(url_for("admin.users_list"))


@admin_bp.route("/users/<int:user_id>/reset_password", methods=["POST"])
@login_required
@admin_required
def users_reset_password(user_id):
    user = User.query.get_or_404(user_id)
    new_password = request.form.get("password", "").strip()
    if not new_password:
        flash("Enter a new password.", "error")
        return redirect(url_for("admin.users_list"))
    user.set_password(new_password)
    db.session.commit()
    flash(f"Password reset for {user.full_name}.", "success")
    return redirect(url_for("admin.users_list"))
