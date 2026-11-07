"""Synthetic labelled invoice generator (digital PDF, scanned PDF/PNG/JPG/TIFF, XLSX, CSV, corrupted files).

Ground truth = exactly what is printed on the document (the extraction target).
Faults are injected on purpose so validation recall/precision can be measured.
"""
import json
import os
import random
from typing import Dict, List, Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

SUPPLIERS = ["Northwind Traders Ltd", "Bluepeak Logistics Inc", "Orion Office Supplies", "Kestrel Components GmbH",
             "Harbor Light Foods", "Zenith Cloud Services LLC", "Maple & Oak Furniture Co", "Delta Print Works Ltd"]
CUSTOMERS = ["Globex Corporation", "Initech Holdings", "Umbrella Retail Ltd", "Stark Manufacturing", "Wayne Enterprises",
             "Hooli Systems", "Acme Industrial Group", "Vandelay Imports"]
ITEMS = ["Steel bracket", "Printer toner cartridge", "Cloud hosting (monthly)", "Safety gloves", "Packing tape roll",
         "Consulting hours", "LED panel light", "Office chair", "Network cable 10m", "Shipping and handling",
         "Pallet wrap", "Maintenance contract"]
SYM = {"USD": "$", "EUR": "€", "GBP": "£"}
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
def _load_font(size: int):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/arial.ttf",
              "C:/Windows/Fonts/segoeui.ttf", "/Library/Fonts/Arial.ttf",
              "/System/Library/Fonts/Supplemental/Arial.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def make_invoice(rng: random.Random, idx: int, fault: Optional[str] = None, currency: Optional[str] = None) -> Dict:
    cur = currency or rng.choice(["USD", "USD", "EUR", "GBP"])
    n_items = rng.randint(2, 5)
    items = []
    for d in rng.sample(ITEMS, n_items):
        q, u = rng.randint(1, 12), round(rng.uniform(3, 250), 2)
        items.append({"description": d, "quantity": float(q), "unit_price": u, "amount": round(q * u, 2)})
    sub = round(sum(i["amount"] for i in items), 2)
    rate = rng.choice([0.05, 0.10, 0.15, 0.20])
    tax = round(sub * rate, 2)
    total = round(sub + tax, 2)
    date_iso = f"{rng.randint(2023, 2025)}-{rng.randint(1, 12):02d}-{rng.randint(13, 28):02d}"
    inv = {"invoice_number": f"INV-{rng.randint(2024, 2025)}-{idx:04d}", "invoice_date": date_iso,
           "supplier_name": rng.choice(SUPPLIERS), "customer_name": rng.choice(CUSTOMERS),
           "subtotal": sub, "tax": tax, "total": total, "currency": cur, "line_items": items,
           "tax_rate": rate, "expected_issues": []}
    if fault == "total_mismatch":
        inv["total"] = round(total + rng.choice([5.0, 12.5, 100.0, -20.0]), 2)
        inv["expected_issues"] = ["TOTAL_MISMATCH"]
    elif fault == "subtotal_mismatch":
        inv["subtotal"] = round(sub + rng.choice([10.0, 37.0]), 2)
        inv["total"] = round(inv["subtotal"] + tax, 2)  # internally consistent, but lines disagree
        inv["expected_issues"] = ["SUBTOTAL_MISMATCH"]
    elif fault == "line_mismatch":
        it = inv["line_items"][0]
        it["amount"] = round(it["amount"] + 9.0, 2)
        inv["expected_issues"] = ["LINE_ITEM_MISMATCH"]
        inv["subtotal"] = round(sum(i["amount"] for i in items), 2)
        inv["tax"] = round(inv["subtotal"] * rate, 2)
        inv["total"] = round(inv["subtotal"] + inv["tax"], 2)
    elif fault == "missing_number":
        inv["invoice_number"] = None
        inv["expected_issues"] = ["MISSING_FIELD"]
    elif fault == "missing_date":
        inv["invoice_date"] = None
        inv["expected_issues"] = ["MISSING_FIELD"]
    return inv


def _fmt_date(iso: str, style: int) -> str:
    y, m, d = iso.split("-")
    return [iso, f"{int(d)}/{int(m)}/{y}" if int(d) > 12 else iso, f"{MONTHS[int(m) - 1]} {int(d)}, {y}",
            f"{int(d)} {MONTHS[int(m) - 1][:3]} {y}"][style % 4]


def _money(v: float, cur: str, sym: bool) -> str:
    return f"{SYM[cur]}{v:,.2f}" if sym else f"{v:,.2f}"


def render_lines(inv: Dict, layout: int) -> List[str]:
    cur, items = inv["currency"], inv["line_items"]
    dstyle = layout + 1
    date = _fmt_date(inv["invoice_date"], dstyle) if inv["invoice_date"] else None
    L: List[str] = []
    if layout == 0:
        L += [inv["supplier_name"], "INVOICE"]
        if inv["invoice_number"]:
            L.append(f"Invoice No: {inv['invoice_number']}")
        if date:
            L.append(f"Invoice Date: {date}")
        L += [f"Bill To: {inv['customer_name']}", "", "Description Qty Unit Price Amount"]
        for i in items:
            L.append(f"{i['description']} {i['quantity']:g} {i['unit_price']:.2f} {i['amount']:.2f}")
        L += ["", f"Subtotal: {_money(inv['subtotal'], cur, True)}", f"Tax ({inv['tax_rate']:.0%}): {_money(inv['tax'], cur, True)}",
              f"Total: {_money(inv['total'], cur, True)}", f"Currency: {cur}"]
    elif layout == 1:
        L += ["TAX INVOICE", f"Supplier: {inv['supplier_name']}", f"Customer: {inv['customer_name']}"]
        if inv["invoice_number"]:
            L.append(f"Inv #: {inv['invoice_number']}")
        if date:
            L.append(f"Date: {date}")
        L += ["", "Item Qty Rate Line Total"]
        for i in items:
            L.append(f"{i['description']} {i['quantity']:g} {i['unit_price']:.2f} {i['amount']:.2f}")
        L += ["", f"Sub Total: {inv['subtotal']:,.2f}", f"VAT: {inv['tax']:,.2f}", f"Grand Total: {cur} {inv['total']:,.2f}"]
    else:
        L += [inv["supplier_name"], "Receipt"]
        if inv["invoice_number"]:
            L.append(f"Receipt No: {inv['invoice_number']}")
        if date:
            L.append(f"Date: {date}")
        L += [f"Sold To: {inv['customer_name']}", ""]
        for i in items:
            L.append(f"{i['description']} {i['quantity']:g} {i['unit_price']:.2f} {i['amount']:.2f}")
        L += ["", f"Net Amount: {inv['subtotal']:,.2f}", f"GST: {inv['tax']:,.2f}", f"Amount Due: {_money(inv['total'], cur, True)}",
              f"Currency: {cur}"]
    return L


def write_digital_pdf(path: str, invoices: List[Dict], layouts: List[int]) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(path, pagesize=A4)
    for inv, lay in zip(invoices, layouts):
        y = 800
        for line in render_lines(inv, lay):
            c.setFont("Helvetica-Bold" if line in ("INVOICE", "TAX INVOICE", "Receipt") else "Helvetica", 11)
            c.drawString(50, y, line.replace("€", "EUR ").replace("£", "GBP ") if False else line)
            y -= 18
        c.showPage()
    c.save()


def draw_page(lines: List[str], quality: str = "clean", seed: int = 0) -> Image.Image:
    rng = random.Random(seed)
    img = Image.new("L", (1240, 1754), 255)
    dr = ImageDraw.Draw(img)
    font =      _load_font(30)
    y = 120
    for line in lines:
        dr.text((100, y), line, font=font, fill=15)
        y += 48
    if quality == "clean":
        return img.rotate(rng.uniform(-0.6, 0.6), fillcolor=255, resample=Image.BICUBIC)
    if quality == "noisy":      # typical office scan
        img = img.rotate(rng.uniform(-2.5, 2.5), fillcolor=255, resample=Image.BICUBIC)
        img = img.filter(ImageFilter.GaussianBlur(0.9))
        a = np.array(img).astype(float) + np.random.RandomState(seed).normal(0, 14, (1754, 1240))
        return Image.fromarray(np.clip(a, 0, 255).astype("uint8"))
    # poor: low resolution, strong blur, heavy noise, skew
    img = img.rotate(rng.uniform(-4, 4), fillcolor=255, resample=Image.BICUBIC)
    img = img.resize((620, 877), Image.BILINEAR).filter(ImageFilter.GaussianBlur(1.1)).resize((1240, 1754))
    a = np.array(img).astype(float) * 0.85 + np.random.RandomState(seed).normal(0, 30, (1754, 1240))
    return Image.fromarray(np.clip(a, 0, 255).astype("uint8"))


def write_ledger(path: str, invoices: List[Dict], fmt: str, with_items: bool, extra_sheet: bool = False) -> None:
    import pandas as pd
    rows = []
    for inv in invoices:
        base = {"Invoice Number": inv["invoice_number"] or "", "Invoice Date": inv["invoice_date"] or "",
                "Supplier": inv["supplier_name"], "Customer": inv["customer_name"], "Subtotal": inv["subtotal"],
                "Tax": inv["tax"], "Total": inv["total"], "Currency": inv["currency"]}
        if with_items:
            for k, it in enumerate(inv["line_items"]):
                r = dict(base) if k == 0 else {"Invoice Number": inv["invoice_number"] or ""}
                r.update({"Description": it["description"], "Qty": it["quantity"], "Unit Price": it["unit_price"], "Line Amount": it["amount"]})
                rows.append(r)
        else:
            rows.append(base)
    df = pd.DataFrame(rows)
    if fmt == "csv":
        df.to_csv(path, index=False)
    else:
        with pd.ExcelWriter(path, engine="openpyxl") as w:
            df.to_excel(w, sheet_name="Invoices", index=False)
            if extra_sheet:
                pd.DataFrame({"Notes": ["Prepared by finance", "Do not distribute"]}).to_excel(w, sheet_name="Notes", index=False)


def build_dataset(out_dir: str, seed: int = 7, scale: float = 1.0) -> List[Dict]:
    """Create files + ground_truth.json. Returns the manifest."""
    rng = random.Random(seed)
    os.makedirs(out_dir, exist_ok=True)
    manifest: List[Dict] = []
    counter = [0]

    def inv(fault=None):
        counter[0] += 1
        return make_invoice(rng, counter[0], fault)

    def add(name, kind, invoices, expect_fail=False, quality=None):
        manifest.append({"file": name, "kind": kind, "invoices": invoices, "expect_fail": expect_fail, "quality": quality})

    faults = [None] * 6 + ["total_mismatch", "subtotal_mismatch", "line_mismatch", "missing_number", "missing_date"]
    n = lambda x: max(1, int(round(x * scale)))

    # digital PDFs (single + multi-invoice)
    for i in range(n(8)):
        f = rng.choice(faults)
        v, lay = inv(f), i % 3
        p = f"digital_{i:02d}.pdf"
        write_digital_pdf(os.path.join(out_dir, p), [v], [lay])
        add(p, "digital_pdf", [v])
    for i in range(n(3)):
        vs = [inv(rng.choice(faults)) for _ in range(4)]
        p = f"digital_multi_{i:02d}.pdf"
        write_digital_pdf(os.path.join(out_dir, p), vs, [0, 1, 2, 0])
        add(p, "digital_pdf_multi", vs)
    # scanned (clean / noisy / poor) in several containers
    for i, (q, ext) in enumerate([("clean", "png"), ("clean", "pdf"), ("clean", "jpg"), ("noisy", "png"), ("noisy", "tiff"),
                                  ("noisy", "pdf"), ("noisy", "jpg"), ("poor", "png"), ("poor", "jpg"), ("poor", "pdf")][:max(3, n(10))]):
        v = inv(rng.choice(faults))
        img = draw_page(render_lines(v, i % 3), q, seed=i)
        p = f"scan_{q}_{i:02d}.{ext}"
        fp = os.path.join(out_dir, p)
        if ext == "jpg":
            img.convert("RGB").save(fp, quality=70 if q != "clean" else 90)
        elif ext == "pdf":
            img.convert("RGB").save(fp, "PDF", resolution=150)
        elif ext == "tiff":
            img.save(fp, compression="tiff_lzw")
        else:
            img.save(fp)
        add(p, f"scan_{ext}", [v], quality=q)
    # spreadsheets
    for i, (fmt, items) in enumerate([("xlsx", False), ("xlsx", True), ("csv", False), ("csv", True)][:max(2, n(4))]):
        ledger_faults = faults if items else [f for f in faults if f not in ("line_mismatch", "subtotal_mismatch")]
        vs = [inv(rng.choice(ledger_faults)) for _ in range(6)]
        p = f"ledger_{i:02d}.{fmt}"
        write_ledger(os.path.join(out_dir, p), vs, fmt, items, extra_sheet=(fmt == "xlsx"))
        add(p, f"ledger_{fmt}", vs)
    # duplicate: same invoice issued twice in two different files (second must be flagged)
    d = inv()
    write_digital_pdf(os.path.join(out_dir, "dup_a.pdf"), [d], [0])
    write_digital_pdf(os.path.join(out_dir, "dup_b.pdf"), [d], [1])
    add("dup_a.pdf", "digital_pdf", [d])
    dd = json.loads(json.dumps(d)); dd["expected_issues"] = ["DUPLICATE_INVOICE"]
    add("dup_b.pdf", "digital_pdf", [dd])
    # corrupted / hostile inputs (must fail cleanly, never crash)
    open(os.path.join(out_dir, "corrupt_truncated.pdf"), "wb").write(open(os.path.join(out_dir, "digital_00.pdf"), "rb").read()[:300])
    open(os.path.join(out_dir, "corrupt_garbage.pdf"), "wb").write(os.urandom(2048))
    open(os.path.join(out_dir, "corrupt_empty.png"), "wb").write(b"")
    open(os.path.join(out_dir, "corrupt_bad.xlsx"), "wb").write(b"PK\x03\x04" + os.urandom(500))
    for p, k in [("corrupt_truncated.pdf", "pdf"), ("corrupt_garbage.pdf", "pdf"), ("corrupt_empty.png", "image"), ("corrupt_bad.xlsx", "xlsx")]:
        add(p, "corrupted", [], expect_fail=True)
    with open(os.path.join(out_dir, "ground_truth.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    return manifest


if __name__ == "__main__":
    import sys
    m = build_dataset(sys.argv[1] if len(sys.argv) > 1 else "evaluation/dataset")
    print(f"{len(m)} files, {sum(len(x['invoices']) for x in m)} labelled invoices")
