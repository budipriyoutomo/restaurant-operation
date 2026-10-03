#!/usr/bin/env python3
"""Create a platform admin — the SaaS operator account (Todo-Pilot §11).

Platform admins manage companies from the Platform page; they belong to no
company and cannot read any company's data. Run once to bootstrap:

    python -m scripts.create_platform_admin ops@yourdomain.com "Ops Team" [--password ...]
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import app.main  # noqa: F401 — registers every model
from app.database import SessionLocal
from app.services.platform_service import create_platform_admin


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a platform admin")
    parser.add_argument("email")
    parser.add_argument("name")
    parser.add_argument("--password", help="password (generated if omitted)")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        user, password = create_platform_admin(db, args.email, args.name, args.password)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        db.close()
    print(f"Platform admin created: {user.email}")
    if not args.password:
        print(f"Password: {password}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
