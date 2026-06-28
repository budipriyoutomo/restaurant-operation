#!/usr/bin/env python3
"""Seed script — loads the same sample data that was previously hardcoded in lib/store.ts.

Run from the backend/ directory after applying migrations:
    python -m scripts.seed

All sequence tables are updated so future creates continue from the right numbers.
"""

import sys
import os

# Ensure the backend/ directory is on the path when running as a module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from app.database import SessionLocal
from app.models.issue import Issue, IssueNumberSequence
from app.models.task import Task, TaskNumberSequence
from app.models.approval import ApprovalRequest, ApprovalNumberSequence
from app.models.outlet import Outlet
from app.models.category import Category
from app.models.pic import PIC
from app.models.user import User
from app.services.auth_service import hash_password
from datetime import date
import uuid


def run():
    db = SessionLocal()
    try:
        # ------------------------------------------------------------------
        # Default users for testing
        # ------------------------------------------------------------------
        default_users = [
            {"id": uuid.UUID("00000000-0000-0000-0000-000000000001"), "email": "admin@restaurant.com",   "name": "Admin",         "role": "admin"},
            {"id": uuid.UUID("00000000-0000-0000-0000-000000000002"), "email": "manager@restaurant.com", "name": "Restaurant Manager", "role": "manager"},
            {"id": uuid.UUID("00000000-0000-0000-0000-000000000003"), "email": "staff@restaurant.com",   "name": "Staff User",    "role": "staff"},
        ]
        passwords = {
            "admin@restaurant.com":   "admin123",
            "manager@restaurant.com": "manager123",
            "staff@restaurant.com":   "staff123",
        }
        created = 0
        for u in default_users:
            if not db.query(User).filter(User.email == u["email"]).first():
                db.add(User(id=u["id"], email=u["email"], name=u["name"],
                            password_hash=hash_password(passwords[u["email"]]), role=u["role"]))
                created += 1
        if created:
            db.commit()
            print(f"  Users:      {created} inserted")
        else:
            print("  Users:      already present — skipped")

        # ------------------------------------------------------------------
        # Outlets — seeded independently; safe to run alongside existing issues
        # ------------------------------------------------------------------
        if db.query(Outlet).count() == 0:
            outlets = [
                Outlet(id=uuid.UUID("44444444-0000-0000-0000-000000000001"), name="KL Central",   code="KLC", status="operational"),
                Outlet(id=uuid.UUID("44444444-0000-0000-0000-000000000002"), name="KLCC",          code="KCC", status="operational"),
                Outlet(id=uuid.UUID("44444444-0000-0000-0000-000000000003"), name="Subang",        code="SUB", status="warning"),
                Outlet(id=uuid.UUID("44444444-0000-0000-0000-000000000004"), name="Bangsar",       code="BGS", status="operational"),
                Outlet(id=uuid.UUID("44444444-0000-0000-0000-000000000005"), name="Petaling Jaya", code="PJY", status="operational"),
                Outlet(id=uuid.UUID("44444444-0000-0000-0000-000000000006"), name="Damansara",     code="DMS", status="critical"),
            ]
            db.add_all(outlets)
            db.commit()
            print(f"  Outlets:    {len(outlets)} inserted")
        else:
            print("  Outlets:    already present — skipped")

        # ------------------------------------------------------------------
        # Categories — mirrors hardcoded master-data-page.tsx categoryMasterData
        # ------------------------------------------------------------------
        cat_ids = {
            "operations":      uuid.UUID("55555555-0000-0000-0000-000000000001"),
            "procurement":     uuid.UUID("55555555-0000-0000-0000-000000000002"),
            "service_quality": uuid.UUID("55555555-0000-0000-0000-000000000003"),
            "training":        uuid.UUID("55555555-0000-0000-0000-000000000004"),
            "compliance":      uuid.UUID("55555555-0000-0000-0000-000000000005"),
            "marketing":       uuid.UUID("55555555-0000-0000-0000-000000000006"),
            "equipment":       uuid.UUID("55555555-0000-0000-0000-000000000007"),
            "plumbing":        uuid.UUID("55555555-0000-0000-0000-000000000008"),
            "electrical":      uuid.UUID("55555555-0000-0000-0000-000000000009"),
            "hvac":            uuid.UUID("55555555-0000-0000-0000-000000000010"),
        }
        if db.query(Category).count() == 0:
            categories = [
                Category(id=cat_ids["operations"],      name="Operations",     description="Operational and scheduling issues",     type="operations"),
                Category(id=cat_ids["procurement"],     name="Procurement",    description="Supply chain and inventory management", type="operations"),
                Category(id=cat_ids["service_quality"], name="Service Quality",description="Customer service and quality issues",   type="operations"),
                Category(id=cat_ids["training"],        name="Training",       description="Staff training and development",        type="operations"),
                Category(id=cat_ids["compliance"],      name="Compliance",     description="Health and safety compliance",          type="operations"),
                Category(id=cat_ids["marketing"],       name="Marketing",      description="Marketing and promotional activities",  type="operations"),
                Category(id=cat_ids["equipment"],       name="Equipment",      description="Kitchen equipment maintenance",         type="maintenance"),
                Category(id=cat_ids["plumbing"],        name="Plumbing",       description="Water and plumbing systems",            type="maintenance"),
                Category(id=cat_ids["electrical"],      name="Electrical",     description="Electrical systems and lighting",       type="maintenance"),
                Category(id=cat_ids["hvac"],            name="HVAC",           description="Heating, ventilation and cooling",      type="maintenance"),
            ]
            db.add_all(categories)
            db.commit()
            print(f"  Categories: {len(categories)} inserted")
        else:
            print("  Categories: already present — skipped")

        # ------------------------------------------------------------------
        # PICs — query categories from DB so this works even if cats were
        # seeded in a prior run
        # ------------------------------------------------------------------
        if db.query(PIC).count() == 0:
            def cat_objs(*keys):
                ids = [cat_ids[k] for k in keys]
                return db.query(Category).filter(Category.id.in_(ids)).all()

            pic_objs = [
                PIC(id=uuid.UUID("66666666-0000-0000-0000-000000000001"),
                    name="Ahmad Razif", email="ahmad.razif@restaurant.com",
                    phone="+60 12-3456 7890", department="Engineering",
                    categories=cat_objs("equipment", "electrical", "hvac")),
                PIC(id=uuid.UUID("66666666-0000-0000-0000-000000000002"),
                    name="Raj Kumar", email="raj.kumar@restaurant.com",
                    phone="+60 11-2345 6789", department="Engineering",
                    categories=cat_objs("equipment", "electrical", "plumbing")),
                PIC(id=uuid.UUID("66666666-0000-0000-0000-000000000003"),
                    name="Lee Chong Wei", email="lee.cw@restaurant.com",
                    phone="+60 13-4567 8901", department="Operations",
                    categories=cat_objs("compliance", "operations")),
                PIC(id=uuid.UUID("66666666-0000-0000-0000-000000000004"),
                    name="Sarah Johnson", email="sarah.johnson@restaurant.com",
                    phone="+60 14-5678 9012", department="Operations",
                    categories=cat_objs("training", "service_quality")),
                PIC(id=uuid.UUID("66666666-0000-0000-0000-000000000005"),
                    name="Priya Sharma", email="priya.sharma@restaurant.com",
                    phone="+60 16-7890 1234", department="Supply Chain",
                    categories=cat_objs("procurement")),
            ]
            db.add_all(pic_objs)
            db.commit()
            print(f"  PICs:       {len(pic_objs)} inserted")
        else:
            print("  PICs:       already present — skipped")

        # ------------------------------------------------------------------
        # Issues / Tasks / Approvals
        # ------------------------------------------------------------------
        if db.query(Issue).count() > 0:
            print("  Issues:     already present — skipped")
            return

        # Fixed UUIDs so foreign keys are consistent
        iss_ids = {
            "iss-1": uuid.UUID("11111111-0000-0000-0000-000000000001"),
            "iss-2": uuid.UUID("11111111-0000-0000-0000-000000000002"),
            "iss-3": uuid.UUID("11111111-0000-0000-0000-000000000003"),
            "iss-4": uuid.UUID("11111111-0000-0000-0000-000000000004"),
            "iss-5": uuid.UUID("11111111-0000-0000-0000-000000000005"),
            "iss-6": uuid.UUID("11111111-0000-0000-0000-000000000006"),
        }
        task_ids = {
            "task-1": uuid.UUID("22222222-0000-0000-0000-000000000001"),
            "task-2": uuid.UUID("22222222-0000-0000-0000-000000000002"),
            "task-3": uuid.UUID("22222222-0000-0000-0000-000000000003"),
            "task-4": uuid.UUID("22222222-0000-0000-0000-000000000004"),
            "task-5": uuid.UUID("22222222-0000-0000-0000-000000000005"),
            "task-6": uuid.UUID("22222222-0000-0000-0000-000000000006"),
        }
        apr_ids = {
            "apr-1": uuid.UUID("33333333-0000-0000-0000-000000000001"),
            "apr-2": uuid.UUID("33333333-0000-0000-0000-000000000002"),
            "apr-3": uuid.UUID("33333333-0000-0000-0000-000000000003"),
            "apr-4": uuid.UUID("33333333-0000-0000-0000-000000000004"),
        }

        # ------------------------------------------------------------------
        # Issues
        # ------------------------------------------------------------------
        issues = [
            Issue(id=iss_ids["iss-1"], number="ISS-2026-00145", title="Kitchen AC System Breakdown",
                  description="Main air conditioning unit in kitchen area not functioning",
                  outlet="KL Central", category="Maintenance", priority="critical", status="in-progress",
                  assignee="Ahmad Razif", due_date=date(2026, 6, 4)),
            Issue(id=iss_ids["iss-2"], number="ISS-2026-00142", title="POS System Network Issue",
                  description="POS terminals losing network connectivity intermittently",
                  outlet="KLCC", category="IT Support", priority="critical", status="assigned",
                  assignee="Raj Kumar", due_date=date(2026, 6, 4)),
            Issue(id=iss_ids["iss-3"], number="ISS-2026-00138", title="Food Safety Compliance Finding",
                  description="Cold storage temperature deviation during last audit",
                  outlet="Subang", category="Compliance", priority="high", status="open",
                  assignee="Unassigned", due_date=date(2026, 6, 5)),
            Issue(id=iss_ids["iss-4"], number="ISS-2026-00135", title="Staff Training Program - New Menu",
                  description="Training required for 5 new menu items launching next week",
                  outlet="Bangsar", category="Training", priority="high", status="assigned",
                  assignee="Sarah Johnson", due_date=date(2026, 6, 7)),
            Issue(id=iss_ids["iss-5"], number="ISS-2026-00132", title="Procurement - Kitchen Equipment",
                  description="Replace aging food prep station and order new utensils",
                  outlet="KL Central", category="Procurement", priority="medium", status="waiting",
                  assignee="Priya Sharma", due_date=date(2026, 6, 15)),
            Issue(id=iss_ids["iss-6"], number="ISS-2026-00128", title="Marketing Campaign Launch",
                  description="Approve and launch mid-year promotional campaign",
                  outlet="All Outlets", category="Marketing", priority="medium", status="open",
                  assignee="Unassigned", due_date=date(2026, 6, 10)),
        ]
        db.add_all(issues)
        db.flush()

        # ------------------------------------------------------------------
        # Tasks
        # ------------------------------------------------------------------
        tasks = [
            Task(id=task_ids["task-1"], issue_id=iss_ids["iss-1"], issue_number="ISS-2026-00145",
                 number="TSK-2026-00301", title="Replace AC compressor unit",
                 description="Kitchen AC system breakdown repair",
                 status="in-progress", priority="critical", assignee="Ahmad Razif",
                 due_date=date(2026, 6, 4), outlet="KL Central"),
            Task(id=task_ids["task-2"], issue_id=iss_ids["iss-2"], issue_number="ISS-2026-00142",
                 number="TSK-2026-00302", title="Troubleshoot POS network connectivity",
                 description="Intermittent POS terminal network issues",
                 status="assigned", priority="critical", assignee="Raj Kumar",
                 due_date=date(2026, 6, 4), outlet="KLCC"),
            Task(id=task_ids["task-3"], issue_id=iss_ids["iss-3"], issue_number="ISS-2026-00138",
                 number="TSK-2026-00303", title="Audit cold storage temperature logs",
                 description="Review temperature deviation findings",
                 status="open", priority="high", assignee="Lee Chong Wei",
                 due_date=date(2026, 6, 5), outlet="Subang"),
            Task(id=task_ids["task-4"], issue_id=iss_ids["iss-4"], issue_number="ISS-2026-00135",
                 number="TSK-2026-00304", title="Prepare training materials",
                 description="Create training guides for new menu items",
                 status="assigned", priority="high", assignee="Sarah Johnson",
                 due_date=date(2026, 6, 6), outlet="Bangsar"),
            Task(id=task_ids["task-5"], issue_id=iss_ids["iss-4"], issue_number="ISS-2026-00135",
                 number="TSK-2026-00305", title="Conduct staff training session",
                 description="Train staff on new menu items",
                 status="waiting", priority="high", assignee="Sarah Johnson",
                 due_date=date(2026, 6, 7), outlet="Bangsar"),
            Task(id=task_ids["task-6"], issue_id=iss_ids["iss-5"], issue_number="ISS-2026-00132",
                 number="TSK-2026-00306", title="Get management approval for purchase",
                 description="Obtain approval for equipment purchase",
                 status="waiting", priority="medium", assignee="Priya Sharma",
                 due_date=date(2026, 6, 15), outlet="KL Central"),
        ]
        db.add_all(tasks)

        # ------------------------------------------------------------------
        # Approval Requests
        # ------------------------------------------------------------------
        approvals = [
            ApprovalRequest(id=apr_ids["apr-1"], issue_id=iss_ids["iss-5"], issue_number="ISS-2026-00132",
                            number="APR-2026-00089", title="Purchase kitchen equipment and utensils",
                            type="procurement", description="Replace aging food prep station and order new utensils",
                            requester="Priya Sharma", outlet="KL Central",
                            requested_date=date(2026, 6, 1), amount="RM 45,000", status="pending"),
            ApprovalRequest(id=apr_ids["apr-2"], issue_id=iss_ids["iss-6"], issue_number="ISS-2026-00128",
                            number="APR-2026-00086", title="Mid-year promotional campaign launch",
                            type="marketing", description="Approve marketing campaign for mid-year promotions across all outlets",
                            requester="Marketing Team", outlet="All Outlets",
                            requested_date=date(2026, 5, 28), amount=None, status="pending"),
            ApprovalRequest(id=apr_ids["apr-3"], issue_id=iss_ids["iss-2"], issue_number="ISS-2026-00142",
                            number="APR-2026-00082", title="Purchase new POS system hardware",
                            type="asset-purchase", description="Upgrade POS terminals for better performance and compliance",
                            requester="IT Department", outlet="KLCC",
                            requested_date=date(2026, 5, 25), amount="RM 28,500", status="pending"),
            ApprovalRequest(id=apr_ids["apr-4"], issue_id=iss_ids["iss-4"], issue_number="ISS-2026-00135",
                            number="APR-2026-00078", title="Staff training program for new menu",
                            type="training", description="Training program for staff on 5 new menu items launching next week",
                            requester="Sarah Johnson", outlet="Bangsar",
                            requested_date=date(2026, 6, 1), amount=None, status="pending"),
        ]
        db.add_all(approvals)

        # ------------------------------------------------------------------
        # Sync sequences so new creates continue from the right numbers
        # ------------------------------------------------------------------
        db.add(IssueNumberSequence(year=2026, last_seq=145))
        db.add(TaskNumberSequence(year=2026, last_seq=306))
        db.add(ApprovalNumberSequence(year=2026, last_seq=89))

        db.commit()
        print(f"  Issues:     {len(issues)} inserted")
        print(f"  Tasks:      {len(tasks)} inserted")
        print(f"  Approvals:  {len(approvals)} inserted")

    except Exception as e:
        db.rollback()
        print(f"Seed failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run()
