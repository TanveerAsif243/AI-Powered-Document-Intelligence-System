import hashlib
import re
from datetime import date, datetime
from typing import Optional, Set

CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "₹": "INR", "৳": "BDT"}
CURRENCY_CODES = {"USD", "EUR", "GBP", "JPY", "INR", "BDT", "CAD", "AUD", "CHF", "CNY", "SGD", "AED",
                  "SAR", "MYR", "PKR", "NZD", "SEK", "NOK", "DKK", "HKD", "KRW", "THB", "ZAR", "BRL", "MXN"}
CORP_SUFFIXES = r"\b(inc|incorporated|llc|ltd|limited|corp|corporation|co|company|gmbh|plc|pte|pvt|sa|bv|ag)\b\.?"

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_amount(v) -> Optional[float]:
    """Parse '1,234.56', '1.234,56', '$12', '(45.00)', 12 -> float. Returns None when not numeric."""
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return None if v != v else float(v)
    s = str(v).strip()
    if not s:
        return None
    neg = (s.startswith("(") and s.endswith(")")) or s.startswith("-") or s.endswith("-")
    s = re.sub(r"[^\d.,]", "", s)
    if not re.search(r"\d", s):
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        parts = s.split(",")
        if len(parts) > 2 or len(parts[-1]) == 3:
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        x = float(s)
    except ValueError:
        return None
    return -x if neg else x


def _valid(y, m, d) -> Optional[str]:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def date_candidates(v) -> Set[str]:
    """All plausible ISO dates for a date string (both readings when dd/mm is ambiguous)."""
    if v is None:
        return set()
    s = str(v).strip()
    out: Set[str] = set()
    m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        r = _valid(int(m[1]), int(m[2]), int(m[3]))
        return {r} if r else set()
    m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})\b", s)
    if m:
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        y = y + 2000 if y < 100 else y
        for d_, mo in ((a, b), (b, a)):
            r = _valid(y, mo, d_)
            if r:
                out.add(r)
        return out
    m = re.match(r"^(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})", s)
    if m and m[2][:3].lower() in MONTHS:
        r = _valid(int(m[3]), MONTHS[m[2][:3].lower()], int(m[1]))
        return {r} if r else set()
    m = re.match(r"^([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", s)
    if m and m[1][:3].lower() in MONTHS:
        r = _valid(int(m[3]), MONTHS[m[1][:3].lower()], int(m[2]))
        return {r} if r else set()
    return out


def parse_date(v, dayfirst: bool = True) -> Optional[str]:
    """Return ISO date. Ambiguous dd/mm vs mm/dd resolves by value (>12) else `dayfirst`."""
    if isinstance(v, (datetime, date)):
        return v.date().isoformat() if isinstance(v, datetime) else v.isoformat()
    if v is None:
        return None
    s = str(v).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}", s):  # excel datetime string
        s = s[:10]
    cands = date_candidates(s)
    if not cands:
        return None
    if len(cands) == 1:
        return next(iter(cands))
    m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})", s)
    a, b, y = int(m[1]), int(m[2]), int(m[3])
    y = y + 2000 if y < 100 else y
    return _valid(y, b, a) if dayfirst else _valid(y, a, b)


def normalize_currency(v) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    if s.upper() in CURRENCY_CODES:
        return s.upper()
    for sym, code in CURRENCY_SYMBOLS.items():
        if sym in s:
            return code
    if re.fullmatch(r"[A-Za-z]{3}", s):
        return s.upper()  # unknown ISO-looking code: kept, validation decides
    return None


def norm_name(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = re.sub(CORP_SUFFIXES, " ", s.lower())
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s or None


def norm_invoice_no(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = re.sub(r"[^a-z0-9]", "", str(s).lower())
    return s or None
