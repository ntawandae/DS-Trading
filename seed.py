"""
Run once to create the first admin login and some demo data:
    python seed.py
"""
from duoshao import create_app
from duoshao.models import db, User, Product, Customer, Employee

app = create_app()

with app.app_context():
    # --- Admin user (management) ---
    if not User.query.filter_by(username="admin").first():
        admin = User(username="admin", full_name="Emmanuel", role="admin")
        admin.set_password("changeme123")
        db.session.add(admin)
        print("Created admin user -> username: admin / password: changeme123 (CHANGE THIS)")

    # --- Sample worker (staff) user ---
    if not User.query.filter_by(username="worker").first():
        worker = User(username="worker", full_name="Tatenda Ncube", role="worker")
        worker.set_password("changeme123")
        db.session.add(worker)
        print("Created worker user -> username: worker / password: changeme123 (CHANGE THIS)")

    # --- Sample products (single record = public catalog listing + stock) ---
    if Product.query.count() == 0:
        demo_products = [
            # name, category, description, quantity, unit_cost, unit_price
            ("Smart Toilet", "Smart Home", "Sensor-flush smart toilet with bidet function.", 0, 150, 220),
            ("Smart Bin", "Smart Home", "Motion-sensor automatic waste bin.", 0, 20, 35),
            ("Solar Home System 20W", "Solar & Energy", "All-in-one solar kit with lighting and USB charging.", 24, 32, 60),
            ("Camping Tent 4-person", "Events & Outdoor", "Waterproof family/event tent.", 8, 28, 45),
            ("PA Speaker Set", "Events & Outdoor", "Portable PA system for events.", 0, 120, 180),
            ("Laptop (Entry-level)", "Technology", "Budget laptops for office/school use, bulk pricing available.", 0, 0, 0),
            ("Hair Extensions Bundle", "Beauty", "Wholesale hair extension bundles, multiple textures.", 0, 0, 0),
        ]
        skus = {"Solar Home System 20W": "SLR-020", "Camping Tent 4-person": "TNT-004"}
        for name, cat, desc, qty, cost, price in demo_products:
            db.session.add(Product(
                name=name, sku=skus.get(name), category=cat, description=desc,
                quantity=qty, reorder_level=10, unit_cost_usd=cost, unit_price_usd=price,
            ))
        print("Added sample products — two with real stock quantities (shown as 'In Stock'), "
              "the rest at quantity 0 (shown as 'Available on Request').")

    # --- Sample customer + linked client login ---
    if Customer.query.count() == 0:
        cust = Customer(name="Tendai Moyo", company="Moyo Wholesale", phone="+263 77 xxx xxxx",
                                 email="tendai@example.com", country="Zimbabwe")
        db.session.add(cust)
        db.session.flush()
        client_user = User(username="tendai@example.com", full_name="Tendai Moyo", role="client", customer_id=cust.id)
        client_user.set_password("changeme123")
        db.session.add(client_user)
        print("Added sample customer + client login -> username: tendai@example.com / password: changeme123")

    # --- Sample employee ---
    if Employee.query.count() == 0:
        db.session.add(Employee(name="Emmanuel", role="Founder & Legal Representative", monthly_salary_usd=0))
        print("Added founder as employee record (edit salary as needed).")

    db.session.commit()
    print("Seed complete.")
