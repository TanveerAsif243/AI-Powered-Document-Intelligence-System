"""Deterministic regex extractor: offline fallback + cross-check for the LLM output."""
import re
from collections import Counter
from typing import Dict, List, Optional

from .segment import INV_NO_RE
from .utils import CURRENCY_CODES, CURRENCY_SYMBOLS, parse_amount, parse_date

SP = r"[^\S\n]*"
AMT = (r"(?:\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+,\d{1,2}(?!\d)|\d+(?:\.\d{1,2})?)")
CUR = r"(?:[A-Za-z]{3}" + SP + r"|[$€£¥₹৳]" + SP + r")?"
RATE = r"(?:\(?\s*\d+(?:\.\d+)?\s*%\s*\)?)?"
DATE_PAT = (r"(\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|"
            r"\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,9}\.?,?\s+\d{4}|[A-Za-z]{3,9}\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4})")
ISO_RE = re.compile(r"\b(" + "|".join(sorted(CURRENCY_CODES)) + r")\b")


def _amount_after(label: str, text: str, skip_sub: bool = False) -> Optional[float]:
    pat = re.compile(label + SP + RATE + SP + r"[:\-]?" + SP + CUR + r"(" + AMT + r")", re.I)
    found = None
    for m in pat.finditer(text):
        if skip_sub and re.search(r"sub[\s\-]?$", text[max(0, m.start() - 5):m.start()], re.I):
            continue
        v = parse_amount(m.group(1))
        if v is not None:
            found = v          # totals sit at the bottom: keep the last match
    return found


def _clean(v: str) -> str:
    v = re.split(r"\s{2,}|\t|\s\|\s", v.strip())[0]
    return v.strip(" :-–|,;")


def _label_value(text: str, label: str, allow_next_line: bool = False, need_sep: bool = True) -> Optional[str]:
    sep = r"[:\-]" if need_sep else r"[:\-]?"
    pat = re.compile(r"(?<![A-Za-z])(?:" + label + r")" + SP + sep + SP + r"(.*)", re.I)
    lines = text.split("\n")
    for i, line in enumerate(lines):
        m = pat.search(line)
        if not m:
            continue
        v = _clean(m.group(1))
        if v:
            return v
        if allow_next_line:
            for nxt in lines[i + 1:i + 3]:
                if nxt.strip():
                    return _clean(nxt)
    return None


def extract_rules(text: str) -> Dict:
    conf: Dict[str, float] = {}
    out: Dict = {k: None for k in ("invoice_number", "invoice_date", "supplier_name", "customer_name",
                                   "subtotal", "tax", "total", "currency")}
    # invoice number
    m = INV_NO_RE.search(text)
    if m:
        out["invoice_number"] = m.group(1).rstrip(".-/_")
        conf["invoice_number"] = 0.9
    # invoice date: labelled first (never "due date"), else first date in the text
    for m in re.finditer(r"(?:invoice\s*date|issue\s*date|date\s*of\s*issue|bill\s*date|date)" + SP + r"[:\-]?" + SP + DATE_PAT, text, re.I):
        if "due" in text[max(0, m.start() - 8):m.start()].lower():
            continue
        iso = parse_date(m.group(1))
        if iso:
            out["invoice_date"], conf["invoice_date"] = iso, 0.9
            break
    if not out["invoice_date"]:
        for m in re.finditer(DATE_PAT, text):            # unlabelled date: never a due/payment/ship date
            if re.search(r"(due|payment|pay by|ship|deliver)", text[max(0, m.start() - 14):m.start()], re.I):
                continue
            iso = parse_date(m.group(1))
            if iso:
                out["invoice_date"], conf["invoice_date"] = iso, 0.5
                break
    # parties
    v = _label_value(text, r"supplier(?:\s+name)?|vendor(?:\s+name)?|seller|sold\s+by|issued\s+by|from")
    if v:
        out["supplier_name"], conf["supplier_name"] = v, 0.85
    else:
        for line in text.split("\n")[:6]:
            s = line.strip()
            if 3 <= len(s) <= 80 and re.search(r"[A-Za-z]{3}", s) and not re.search(r"invoice|receipt|bill|page|sheet|date|^\d", s, re.I):
                out["supplier_name"], conf["supplier_name"] = _clean(s), 0.7
                break
    v = _label_value(text, r"bill(?:ed)?\s*to|customer(?:\s+name)?|client(?:\s+name)?|buyer|sold\s+to",
                     allow_next_line=True, need_sep=False)
    if v and not re.fullmatch(r"(name|date|no\.?|number|address)", v, re.I):
        out["customer_name"], conf["customer_name"] = v, 0.85
    # amounts
    out["subtotal"] = _amount_after(r"(?<![A-Za-z])(?:sub[\s\-]?total|net\s+amount|amount\s+before\s+tax)", text)
    out["tax"] = _amount_after(r"(?<![A-Za-z])(?:total\s+tax|sales\s+tax|vat|gst|tax)", text)
    total = _amount_after(r"(?<![A-Za-z])(?:grand\s+total|total\s+due|amount\s+due|balance\s+due|total\s+amount|invoice\s+total|total\s+payable|amount\s+payable)", text)
    if total is None:
        total = _amount_after(r"(?<![A-Za-z])total", text, skip_sub=True)
    out["total"] = total
    for k in ("subtotal", "tax", "total"):
        if out[k] is not None:
            conf[k] = 0.9
    # currency
    m = re.search(r"currency" + SP + r"[:\-]?" + SP + r"([A-Za-z]{3})\b", text, re.I)
    if m and m.group(1).upper() in CURRENCY_CODES:
        out["currency"], conf["currency"] = m.group(1).upper(), 0.95
    else:
        codes = ISO_RE.findall(text)
        if codes:
            out["currency"], conf["currency"] = Counter(codes).most_common(1)[0][0], 0.9
        else:
            syms = [CURRENCY_SYMBOLS[c] for c in text if c in CURRENCY_SYMBOLS]
            if syms:
                out["currency"], conf["currency"] = Counter(syms).most_common(1)[0][0], 0.6
    out["line_items"] = _line_items(text)
    out["confidence"] = conf
    return out


LINE_RE = re.compile(
    r"^\s*(?:\d+[.)]\s+)?(?P<desc>[A-Za-z][^\n]*?)\s+(?P<qty>\d+(?:\.\d+)?)\s+(?:[A-Za-z]{3}\s?|[$€£¥₹৳]\s?)?"
    r"(?P<unit>\d[\d,]*\.\d{2})\s+(?:[A-Za-z]{3}\s?|[$€£¥₹৳]\s?)?(?P<amt>\d[\d,]*\.\d{2})\s*$")
NOT_ITEM = re.compile(r"total|tax|vat|gst|balance|amount due|subtotal|discount", re.I)


def _line_items(text: str) -> List[dict]:
    items = []
    for line in text.split("\n"):
        m = LINE_RE.match(line)
        if m and not NOT_ITEM.search(m["desc"]):
            items.append({"description": m["desc"].strip(), "quantity": parse_amount(m["qty"]),
                          "unit_price": parse_amount(m["unit"]), "amount": parse_amount(m["amt"])})
    return items
