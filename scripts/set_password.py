#!/usr/bin/env python3
"""Set (rotate) a user's password from the command line.

There is no self-service password-change endpoint, so this is the supported way
to rotate the seeded admin credentials in production.

Usage (from the backend/ directory or inside the api container):
    python -m scripts.set_password admin@restaurant.com
    python -m scripts.set_password admin@restaurant.com --password 's3cret'

With no --password, a strong one is generated and printed once.
"""

import argparse
import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from app.database import SessionLocal
from app.models.user import User
from app.services.auth_service import hash_password


def main() -> int:
    parser = argparse.ArgumentParser(description="Set a user's password")
    parser.add_argument("email")
    parser.add_argument("--password", help="new password (generated if omitted)")
    args = parser.parse_args()

    new_password = args.password or secrets.token_urlsafe(18)

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == args.email).first()
        if not user:
            print(f"No user with email {args.email!r}", file=sys.stderr)
            return 1
        user.password_hash = hash_password(new_password)
        db.commit()
        print(f"Password updated for {args.email} (role: {user.role})")
        if not args.password:
            print(f"New password: {new_password}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
