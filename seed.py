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

   
    db.session.commit()
    print("Seed complete.")
