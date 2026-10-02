"""Campaign money, spend and results (Todo-Pilot §10).

campaigns.budget used to be free text ("Rp 5.000.000", "1,5 juta", and — the
old form's own placeholder — "RM 5,000"). It is now an integer amount in major
units plus a currency; the original text is kept in budget_legacy when it could
not be read. Spend (actual_cost) and simple results (transactions and revenue
in the campaign period, optionally against a baseline period) are entered by
hand for now.

Pure functions (no DB):
  parse_legacy_budget, results_error, campaign_metrics
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Optional, Tuple

_CURRENCY = {"rp": "IDR", "rp.": "IDR", "idr": "IDR", "rm": "MYR", "myr": "MYR"}
_MULTIPLIER = {"miliar": 1_000_000_000, "juta": 1_000_000, "jt": 1_000_000,
               "ribu": 1_000, "rb": 1_000, "k": 1_000}
_PREFIX = re.compile(r"^(rp\.?|idr|rm|myr)\s*")
_AMOUNT = re.compile(r"^(\d[\d.,]*)\s*(miliar|juta|jt|ribu|rb|k)?$")


def _number(text: str, has_multiplier: bool) -> Optional[Decimal]:
    """'5.000.000' / '2,500,000' / '1,250.50' / '1,5' → Decimal (major units)."""
    seps = [c for c in text if c in ".,"]
    if not seps:
        return Decimal(text)
    if "." in text and "," in text:
        # The later separator is the decimal one when followed by 1–2 digits.
        last = max(text.rfind("."), text.rfind(","))
        tail = text[last + 1:]
        other = "," if text[last] == "." else "."
        whole = text[:last].replace(other, "")
        return Decimal(f"{whole}.{tail}") if 1 <= len(tail) <= 2 else Decimal(text.replace(".", "").replace(",", ""))
    sep = seps[0]
    groups = text.split(sep)
    if len(groups) == 2 and 1 <= len(groups[1]) <= 2:
        return Decimal(f"{groups[0]}.{groups[1]}")              # 1,5 juta / 1.5jt / 12.50
    if all(len(g) == 3 for g in groups[1:]):
        return Decimal("".join(groups))                          # thousands groups
    if has_multiplier and len(groups) == 2:
        return Decimal(f"{groups[0]}.{groups[1]}")
    raise InvalidOperation


def parse_legacy_budget(raw: Optional[str]) -> Optional[Tuple[int, str]]:
    """Old free-text budget → (integer amount, ISO currency), or None if unreadable.
    No currency marker means IDR; any other currency is left unread."""
    if raw is None or not raw.strip():
        return None
    s = raw.strip().lower()
    currency = "IDR"
    m = _PREFIX.match(s)
    if m:
        currency = _CURRENCY[m.group(1)]
        s = s[m.end():]
    s = re.sub(r"[,.]-$", "", s).strip()          # "5.000.000,-"
    s = re.sub(r"(\d)\s+(?=\d)", r"\1", s)         # "5 000 000"
    m = _AMOUNT.match(s)
    if not m:
        return None
    number, suffix = m.group(1), m.group(2)
    try:
        value = _number(number, has_multiplier=suffix is not None)
    except (InvalidOperation, ValueError):
        return None
    value *= _MULTIPLIER.get(suffix, 1)
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)), currency


def results_error(status: str, has_results: bool) -> Optional[str]:
    """Transactions/revenue belong to a campaign that ran; spend can be logged any time."""
    if has_results and status not in ("active", "completed"):
        return "Results can be recorded once the campaign is active"
    return None


def _pct(part, whole, ndigits=1) -> Optional[float]:
    if part is None or not whole:
        return None
    return round(part * 100 / whole, ndigits)


def _uplift(now, before) -> Optional[float]:
    if now is None or not before:
        return None
    return round((now - before) * 100 / before, 1)


def campaign_metrics(budget, actual_cost, result_transactions, result_revenue,
                     baseline_transactions, baseline_revenue) -> dict:
    """Derived figures; each is None when its inputs are missing or would divide by zero."""
    return {
        "budget_used_pct": _pct(actual_cost, budget),
        "over_budget": bool(budget and actual_cost is not None and actual_cost > budget),
        "cost_per_transaction": (round(actual_cost / result_transactions)
                                 if actual_cost is not None and result_transactions else None),
        "revenue_per_rupiah": (round(result_revenue / actual_cost, 2)
                               if result_revenue is not None and actual_cost else None),
        "revenue_uplift_pct": _uplift(result_revenue, baseline_revenue),
        "transaction_uplift_pct": _uplift(result_transactions, baseline_transactions),
    }
