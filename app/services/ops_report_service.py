"""Inspection export — ops checklist & temperature log as PDF (Todo-Ops-Checklist Fase 4).

For a health office (Dinkes) visit, a halal audit or an internal audit: one
outlet, up to 31 business days. Per day: the manager's sign-off (and whether
the data still matches it), every checklist run, failed items, and every
temperature reading with its limits and corrective action. Times are outlet
wall-clock.

  report_data  plain dict (tested on its own)
  render_pdf   fpdf2, core fonts → text through ops_report_rules.pdf_text
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from fpdf import FPDF, FontFace
from sqlalchemy.orm import Session

from app.core.tenancy import current_tenant
from app.models.asset import Asset
from app.models.company import Company
from app.models.ops_checklist import OpsDailySignoff
from app.models.outlet import Outlet
from app.services import ops_checklist_rules as R
from app.services import ops_signoff_rules as S
from app.services import ops_signoff_service as signoffs
from app.services.ops_report_rules import pdf_text as T

MAX_DAYS = 31
_DAYS_ID = ("Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu")
_STATUS = {"pending": "BELUM DIISI", "in_progress": "SEDANG DIISI", "missed": "TERLEWAT"}
_SOURCE = {"checklist": "Checklist", "manual": "Manual", "sensor": "Sensor"}
_HEAD = FontFace(emphasis="BOLD", fill_color=(235, 235, 235))


def _local(dt: Optional[datetime], tz: str, fmt: str) -> str:
    return dt.astimezone(R.zone(tz)).strftime(fmt) if dt else ""


def _num(v) -> str:
    return f"{float(v):.1f}" if v is not None else ""


def _limits(lo, hi) -> str:
    if lo is not None and hi is not None:
        return f"{_num(lo)} s/d {_num(hi)}"
    if lo is not None:
        return f">= {_num(lo)}"
    if hi is not None:
        return f"<= {_num(hi)}"
    return "-"


def _status(r) -> str:
    if r.status == "submitted":
        return "TERLAMBAT" if r.is_late else "TEPAT WAKTU"
    return _STATUS.get(r.status, r.status.upper())


def day_label(d: date) -> str:
    return f"{_DAYS_ID[d.weekday()]}, {d.strftime('%d-%m-%Y')}"


def report_data(db: Session, outlet: Outlet, date_from: date, date_to: date,
                now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    tz = signoffs.tz_of(outlet)
    company = db.get(Company, current_tenant(db)) if current_tenant(db) else None
    signed = {s.business_date: s for s in db.query(OpsDailySignoff).filter(
        OpsDailySignoff.outlet_id == outlet.id, OpsDailySignoff.business_date >= date_from,
        OpsDailySignoff.business_date <= date_to)}
    days: List[dict] = []
    d = date_from
    while d <= date_to:
        runs, logs = signoffs.day_runs(db, outlet, d), signoffs.day_logs(db, outlet, d)
        rd, ld = [signoffs.run_dict(r) for r in runs], [signoffs.log_dict(x) for x in logs]
        asset_names = dict(db.query(Asset.id, Asset.name).filter(Asset.id.in_({x.asset_id for x in logs}))) if logs else {}
        s = signed.get(d)
        days.append({
            "date": d.isoformat(), "label": day_label(d), "summary": S.day_summary(rd, ld),
            "signoff": signoffs.signoff_to_response(s).model_dump() if s else None,
            "signedAtLocal": _local(s.signed_at, tz, "%d-%m-%Y %H:%M") if s else None,
            "isIntact": (s.digest == S.day_digest(rd, ld)) if s else None,
            "runs": [{
                "number": r.number, "name": r.template_name, "due": _local(r.due_at, tz, "%H:%M"),
                "status": _status(r), "submitted": _local(r.submitted_at, tz, "%d-%m %H:%M"),
                "by": r.submitted_by_name or "", "score": _num(r.score),
                "failed": [{"title": i.title, "value": f"{_num(i.value)} {i.unit or ''}".strip() if i.value is not None else "",
                            "notes": i.notes or "", "correctiveAction": i.corrective_action or ""}
                           for i in r.items if i.result == "fail"],
            } for r in runs],
            "logs": [{
                "time": _local(x.recorded_at, tz, "%H:%M"), "asset": asset_names.get(x.asset_id, ""),
                "value": _num(x.value), "limits": _limits(x.min_value, x.max_value), "inRange": bool(x.in_range),
                "correctiveAction": x.corrective_action or "", "source": _SOURCE.get(x.source, x.source),
            } for x in logs],
        })
        d += timedelta(days=1)
    totals = {k: sum(day["summary"][k] for day in days) for k in days[0]["summary"]} if days else {}
    return {
        "company": company.name if company else "", "outlet": outlet.name, "timezone": tz,
        "from": date_from.isoformat(), "to": date_to.isoformat(),
        "generatedAt": _local(now, tz, "%d-%m-%Y %H:%M"),
        "totals": totals, "signedDays": sum(1 for day in days if day["signoff"]), "days": days,
    }


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

class _Doc(FPDF):
    footer_text = ""

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", size=7)
        self.set_text_color(110)
        self.cell(0, 6, T(self.footer_text), align="L")
        self.cell(0, 6, f"Halaman {self.page_no()}/{{nb}}", align="R")
        self.set_text_color(0)


def _heading(pdf: FPDF, text: str, size: float = 11) -> None:
    pdf.set_font("Helvetica", "B", size)
    pdf.cell(0, 7, T(text), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=8)


def _line(pdf: FPDF, text: str, bold: bool = False, color=None) -> None:
    pdf.set_font("Helvetica", "B" if bold else "", 8)
    if color:
        pdf.set_text_color(*color)
    pdf.multi_cell(0, 4.5, T(text), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0)
    pdf.set_font("Helvetica", size=8)


def _table(pdf: FPDF, header: List[str], rows: List[List[str]], widths) -> None:
    pdf.set_font("Helvetica", size=7.5)
    with pdf.table(col_widths=widths, line_height=4.5, text_align="LEFT", first_row_as_headings=True,
                   headings_style=_HEAD) as table:
        h = table.row()
        for c in header:
            h.cell(T(c))
        for r in rows:
            row = table.row()
            for c in r:
                row.cell(T(c))
    pdf.ln(2)


def render_pdf(data: dict, compress: bool = True) -> bytes:
    pdf = _Doc(orientation="L", unit="mm", format="A4")
    pdf.compress = compress
    pdf.set_auto_page_break(True, margin=15)
    pdf.set_margins(12, 12, 12)
    pdf.footer_text = f"{data['company']} - {data['outlet']} - dibuat {data['generatedAt']} ({data['timezone']})"
    pdf.add_page()

    _heading(pdf, "Log Checklist Operasional & Suhu", 15)
    _line(pdf, f"{data['company']}  |  Outlet: {data['outlet']}  |  Periode: "
               f"{date.fromisoformat(data['from']).strftime('%d-%m-%Y')} s/d "
               f"{date.fromisoformat(data['to']).strftime('%d-%m-%Y')}  |  Zona waktu: {data['timezone']}")
    t = data["totals"]
    if t:
        _line(pdf, f"Checklist: {t['runs']}  |  Dikirim: {t['submitted']} (terlambat {t['late']})  |  "
                   f"Terlewat: {t['missed']}  |  Item gagal: {t['failedItems']}  |  "
                   f"Pencatatan suhu: {t['temperatureReadings']} (di luar batas {t['excursions']})  |  "
                   f"Hari ditandatangani: {data['signedDays']}/{len(data['days'])}", bold=True)
    pdf.ln(2)

    for day in data["days"]:
        if pdf.get_y() > pdf.h - 45:
            pdf.add_page()
        _heading(pdf, day["label"])
        s = day["signoff"]
        if s:
            note = f" - \"{s['note']}\"" if s["note"] else ""
            _line(pdf, f"Ditandatangani {s['signedByName']}, {day['signedAtLocal']}{note}  (kode {s['digest'][:12]})")
            if day["isIntact"] is False:
                _line(pdf, "PERINGATAN: data hari ini berubah setelah ditandatangani.", bold=True, color=(180, 0, 0))
        else:
            _line(pdf, "Belum ditandatangani manager.", color=(150, 90, 0))

        if day["runs"]:
            _table(pdf, ["No.", "Checklist", "Tenggat", "Status", "Dikirim", "Oleh", "Skor"],
                   [[r["number"], r["name"], r["due"], r["status"], r["submitted"], r["by"], r["score"]]
                    for r in day["runs"]], widths=(28, 70, 18, 30, 26, 60, 16))
            failed = [(r["number"], f) for r in day["runs"] for f in r["failed"]]
            if failed:
                _line(pdf, "Item gagal:", bold=True)
                for number, f in failed:
                    parts = [f["title"], f["value"], f["notes"], f"Koreksi: {f['correctiveAction']}" if f["correctiveAction"] else ""]
                    _line(pdf, f"- {number}: " + " - ".join(p for p in parts if p))
        else:
            _line(pdf, "Tidak ada checklist.")

        if day["logs"]:
            _table(pdf, ["Jam", "Aset", "Suhu (°C)", "Batas (°C)", "Status", "Tindakan koreksi", "Sumber"],
                   [[x["time"], x["asset"], x["value"], x["limits"], "OK" if x["inRange"] else "DI LUAR BATAS",
                     x["correctiveAction"], x["source"]] for x in day["logs"]],
                   widths=(14, 55, 20, 30, 28, 103, 20))
        pdf.ln(3)

    return bytes(pdf.output())
