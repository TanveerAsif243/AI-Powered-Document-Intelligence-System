"""XLSX / XLS / CSV parsing. Ledger-style sheets are parsed deterministically (no LLM needed)."""
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

from .errors import CorruptedFileError, UnsupportedFileError
from .utils import normalize_currency, parse_amount, parse_date

ALIASES = {
    "invoice_number": ["invoicenumber", "invoiceno", "invoice", "invno", "invoiceid", "billno", "documentno", "invnumber", "invoicenum"],
    "invoice_date": ["invoicedate", "date", "invdate", "issuedate", "billdate", "dateofissue"],
    "supplier_name": ["supplier", "vendor", "seller", "suppliername", "vendorname", "from"],
    "customer_name": ["customer", "client", "buyer", "billto", "customername", "soldto", "clientname"],
    "subtotal": ["subtotal", "netamount", "net", "amountbeforetax", "taxableamount"],
    "tax": ["tax", "vat", "gst", "taxamount", "salestax", "vatamount"],
    "total": ["total", "grandtotal", "totalamount", "amountdue", "invoicetotal", "gross", "grossamount"],
    "currency": ["currency", "curr", "ccy", "currencycode"],
    "description": ["description", "item", "itemdescription", "product", "details", "itemname"],
    "quantity": ["qty", "quantity", "units"],
    "unit_price": ["unitprice", "price", "rate", "unitcost", "priceperunit"],
    "line_amount": ["lineamount", "linetotal", "itemtotal", "extendedprice"],
    "amount": ["amount"],  # ambiguous: resolved below
}
LOOKUP = {a: f for f, al in ALIASES.items() for a in al}


def _nh(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


@dataclass
class SheetParse:
    name: str
    text: str
    records: List[dict] = field(default_factory=list)
    fallback_text: bool = False   # no recognisable ledger -> let the text extractor handle it
    warnings: List[str] = field(default_factory=list)
    n_rows: int = 0


def _read(path: str, kind: str) -> Dict[str, pd.DataFrame]:
    try:
        if kind == "csv":
            last = None
            for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
                try:
                    df = pd.read_csv(path, sep=None, engine="python", dtype=str, header=None,
                                     keep_default_na=False, encoding=enc, on_bad_lines="skip")
                    return {"csv": df}
                except UnicodeDecodeError as e:
                    last = e
            raise CorruptedFileError(f"Cannot decode CSV: {last}")
        engine = "openpyxl" if kind == "xlsx" else "xlrd"
        return pd.read_excel(path, sheet_name=None, header=None, dtype=str, engine=engine, keep_default_na=False)
    except CorruptedFileError:
        raise
    except ImportError as e:
        raise UnsupportedFileError(f"Missing spreadsheet engine: {e}")
    except Exception as e:
        raise CorruptedFileError(f"Unreadable spreadsheet: {e}")


def _text(df: pd.DataFrame, name: str, cap: int = 300_000) -> str:
    out, size = [f"Sheet: {name}"], 0
    for row in df.itertuples(index=False):
        line = " | ".join(str(c).strip() for c in row if str(c).strip())
        if line:
            out.append(line)
            size += len(line)
            if size > cap:
                out.append("[truncated]")
                break
    return "\n".join(out)


def _find_header(df: pd.DataFrame) -> Optional[tuple]:
    for r in range(min(25, len(df))):
        mapping = {}
        for c, v in enumerate(df.iloc[r].tolist()):
            f = LOOKUP.get(_nh(v)) if str(v).strip() else None
            if f and f not in mapping.values():
                mapping[c] = f
        fields = set(mapping.values())
        if len(fields) >= 3 and ({"invoice_number", "total"} & fields):
            if "amount" in fields:  # resolve ambiguous "Amount"
                col = next(c for c, f in mapping.items() if f == "amount")
                mapping[col] = "line_amount" if "description" in fields else "total"
            return r, mapping
    return None


def _records(df: pd.DataFrame, name: str, header_row: int, mapping: Dict[int, str]) -> List[dict]:
    if "invoice_number" not in mapping.values():
        return []
    cells = lambda row, f: next((str(row[c]).strip() for c, ff in mapping.items() if ff == f and str(row[c]).strip()), "")
    groups: Dict[str, dict] = {}
    order: List[str] = []
    last_key = None
    for idx in range(header_row + 1, len(df)):
        row = df.iloc[idx].tolist()
        if not any(str(x).strip() for x in row):
            continue
        no = cells(row, "invoice_number")
        has_item = bool(cells(row, "description") or cells(row, "line_amount"))
        if not no:
            header_cells = sum(bool(cells(row, f)) for f in ("supplier_name", "invoice_date", "total", "customer_name"))
            if header_cells >= 2:
                no = f"__row{idx}"          # a new invoice whose number is missing: keep it, validation flags it
            elif has_item and last_key:     # continuation row (line item) of the previous invoice
                no = last_key
            else:
                continue                    # footer / noise
        key = no
        last_key = key
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"rows": [], "idx": idx}
            order.append(key)
        g["rows"].append(row)
    out = []
    for key in order:
        rows = groups[key]["rows"]
        first = lambda f: next((v for r in rows if (v := cells(r, f))), "")
        raw_total = first("total")
        inv = {
            "invoice_number": None if key.startswith("__row") else key,
            "invoice_date": parse_date(first("invoice_date")) if first("invoice_date") else None,
            "supplier_name": first("supplier_name") or None,
            "customer_name": first("customer_name") or None,
            "subtotal": parse_amount(first("subtotal")),
            "tax": parse_amount(first("tax")),
            "total": parse_amount(raw_total),
            "currency": normalize_currency(first("currency")) or normalize_currency(raw_total),
            "line_items": [],
        }
        if first("invoice_date") and inv["invoice_date"] is None:
            inv["invoice_date_raw"] = first("invoice_date")  # keep so validation can flag it
        if "description" in mapping.values() or "line_amount" in mapping.values():
            for r in rows:
                d, q, u, a = cells(r, "description"), cells(r, "quantity"), cells(r, "unit_price"), cells(r, "line_amount")
                if d or a:
                    inv["line_items"].append({"description": d or None, "quantity": parse_amount(q),
                                              "unit_price": parse_amount(u), "amount": parse_amount(a)})
        inv["confidence"] = {k: 1.0 for k in ("invoice_number", "invoice_date", "supplier_name", "customer_name",
                                              "subtotal", "tax", "total", "currency") if inv.get(k) is not None}
        inv["notes"] = []
        inv["method"] = "spreadsheet"
        inv["source_ref"] = f"{name}!row{groups[key]['idx'] + 1}"
        out.append(inv)
    return out


def parse_spreadsheet(path: str, kind: str) -> List[SheetParse]:
    sheets = _read(path, kind)
    results: List[SheetParse] = []
    for name, df in sheets.items():
        df = df.dropna(how="all")
        if df.empty or not any(str(x).strip() for x in df.to_numpy().ravel()):
            continue
        sp = SheetParse(name=str(name), text=_text(df, str(name)), n_rows=len(df))
        hdr = _find_header(df)
        if hdr:
            sp.records = _records(df, str(name), *hdr)
        if not sp.records:
            sp.fallback_text = True
        results.append(sp)
    if not results:
        raise CorruptedFileError("Spreadsheet contains no data")
    return results
