"""Create the initial Superset admin account once, without interactive prompts."""
import os

from superset.app import create_app

username = os.environ["SUPERSET_ADMIN_USERNAME"]
password = os.environ["SUPERSET_ADMIN_PASSWORD"]
email = os.environ.get("SUPERSET_ADMIN_EMAIL", "atlasadmin@local.test")

app = create_app()
with app.app_context():
    security_manager = app.appbuilder.sm
    existing = security_manager.find_user(username=username)
    if existing:
        print(f"Superset admin '{username}' already exists; leaving its password unchanged.")
    else:
        admin_role = security_manager.find_role("Admin")
        if admin_role is None:
            raise RuntimeError("Superset Admin role is missing. Check the 'superset init' step.")
        created = security_manager.add_user(
            username,
            "AtlasFlow",
            "Admin",
            email,
            admin_role,
            password,
        )
        if not created:
            raise RuntimeError("Could not create the initial Superset administrator.")
        print(f"Created Superset admin '{username}'. Store its password in the local .env file.")
