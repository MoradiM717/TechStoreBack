#!/usr/bin/env python
"""
Create (or update) an admin user.

There is deliberately no public signup route for admin_users, so this is how the
first account gets made:

    python -m scripts.create_admin --email you@example.com --role admin

Omit --password and it prompts without echoing. Re-running with an existing email
updates that user's password/role/name instead of failing.

    docker compose exec backend python -m scripts.create_admin --email you@example.com
"""
import argparse
import getpass
import sys

from sqlalchemy.exc import SQLAlchemyError

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.admin_user import AdminUser

ROLES = ("admin", "editor", "viewer")


def main() -> int:
    parser = argparse.ArgumentParser(description="Create or update a TechStore admin user.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", help="Prompted for if omitted")
    parser.add_argument("--name", default=None, help="Full name shown in the admin panel")
    parser.add_argument("--role", default="admin", choices=ROLES)
    args = parser.parse_args()

    password = args.password or getpass.getpass("Password: ")
    if len(password) < 8:
        print("Password must be at least 8 characters.", file=sys.stderr)
        return 1
    if not args.password:
        if password != getpass.getpass("Confirm password: "):
            print("Passwords didn't match.", file=sys.stderr)
            return 1

    email = args.email.strip().lower()
    db = SessionLocal()
    try:
        user = db.query(AdminUser).filter(AdminUser.email == email).first()
        action = "Updated"
        if user is None:
            user = AdminUser(email=email)
            db.add(user)
            action = "Created"

        user.hashed_password = hash_password(password)
        user.role = args.role
        user.is_active = True
        if args.name:
            user.full_name = args.name

        db.commit()
        print(f"{action} admin user {email} (role: {args.role})")
        return 0
    except SQLAlchemyError as exc:
        db.rollback()
        print(f"Failed: {exc}", file=sys.stderr)
        print(
            "Is the database running and migrated? `docker compose up -d postgres` "
            "then `alembic upgrade head`.",
            file=sys.stderr,
        )
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
