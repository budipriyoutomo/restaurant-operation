#!/usr/bin/env python3
"""Set (rotate) a user's password from the command line.

Break-glass path for when no admin can sign in (in the app: Settings → Security
for your own password, Users → Reset password for someone else).

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

from app.core.tenancy import bypass_tenant
from app.database import SessionLocal
from app.models.user import User
from app.services.auth_service import set_password


def main() -> int:
    parser = argparse.ArgumentParser(description="Set a user's password")
    parser.add_argument("email")
    parser.add_argument("--password", help="new password (generated if omitted)")
    args = parser.parse_args()

    new_password = args.password or secrets.token_urlsafe(18)

    db = SessionLocal()
    try:
        with bypass_tenant(db):                 # emails are unique across companies
            user = db.query(User).filter(User.email == args.email).first()
        if not user:
            print(f"No user with email {args.email!r}", file=sys.stderr)
            return 1
        role = user.role                        # read before commit expires it
        with bypass_tenant(db):
            set_password(db, user, new_password)       # also signs out every session
            db.commit()
        print(f"Password updated for {args.email} (role: {role})")
        if not args.password:
            print(f"New password: {new_password}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
