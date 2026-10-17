#!/usr/bin/env python3
"""Seed built-in daily ops templates for one company (Todo-Ops-Checklist Fase 4).

Not run by migrations. The same sets are one click in onboarding; this is for
an operator setting up a pilot company from the shell.

Run from the backend/ directory:
    python -m scripts.seed_ops_templates <company_slug> [cafe|resto|cloud_kitchen] [--outlet "Outlet name"]

Default set: resto. With --outlet the templates are also scheduled at that
outlet. Idempotent: existing template names and schedules are skipped.

Exit codes: 0 = ok, 1 = failure (unknown company, set or outlet).
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from app.core.tenancy import bypass_tenant, tenant
from app.database import SessionLocal
from app.models.company import Company
from app.models.outlet import Outlet
from app.services.ops_preset_service import apply_preset
from app.services.ops_template_presets import PRESETS


def seed(db, slug: str, preset: str, outlet_name=None) -> dict:
    with bypass_tenant(db):
        company = db.query(Company).filter(Company.slug == slug).first()
    if company is None:
        raise SystemExit(f"Unknown company {slug!r}")
    with tenant(db, company.id):
        outlet = None
        if outlet_name:
            outlet = db.query(Outlet).filter(Outlet.name == outlet_name, Outlet.deleted_at.is_(None)).first()
            if outlet is None:
                raise SystemExit(f"Unknown outlet {outlet_name!r} in {slug}")
        return apply_preset(db, preset, outlet, actor="seed_ops_templates")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("company_slug")
    parser.add_argument("preset", nargs="?", default="resto", choices=sorted(PRESETS))
    parser.add_argument("--outlet")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        result = seed(db, args.company_slug, args.preset, args.outlet)
        print(f"Ops templates ({args.preset}): {len(result['created'])} created, "
              f"{len(result['skipped'])} already there, {result['schedules']} schedule(s)")
        for name in result["created"]:
            print(f"  + {name}")
        return 0
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
