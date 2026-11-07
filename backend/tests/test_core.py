"""Unit tests: type detection, parsing helpers, PDF/OCR/spreadsheet pipelines, extraction, validation."""
import shutil
import zipfile

import pytest

from app.core import extract, pdf as pdfmod
from app.core.detect import detect_kind
from app.core.errors import CorruptedFileError, UnsupportedFileError
from app.core.extract import extract_invoice
from app.core.pipeline import process_file
from app.core.reconcile import reconcile
from app.core.segment import find_invoice_number, segment_pages
from app.core.models import PageResult
from app.core.spreadsheet import parse_spreadsheet
from app.core.utils import date_candidates, norm_name, parse_amount, parse_date
from app.core.validation import needs_review, validate_batch, validate_invoice
from evaluation import synth

needs_tesseract = pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")


# ---------- helpers ----------
@pytest.mark.parametrize("raw,exp", [("1,234.56", 1234.56), ("1.234,56", 1234.56), ("$12", 12.0), ("(45.00)", -45.0),
                                     ("12,50", 12.5), ("1,234", 1234.0), ("abc", None), ("", None), (None, None), (7, 7.0)])
def test_parse_amount(raw, exp):
    assert parse_amount(raw) == exp


def test_parse_date_formats():
    assert parse_date("2024-03-05") == "2024-03-05"
    assert parse_date("March 5, 2024") == "2024-03-05"
    assert parse_date("5 Mar 2024") == "2024-03-05"
    assert parse_date("25/12/2024") == "2024-12-25"
    assert parse_date("12/25/2024") == "2024-12-25"
    assert parse_date("2024-13-45") is None
    assert len(date_candidates("03/04/2024")) == 2       # ambiguous: both readings are kept for grounding


def test_norm_name_ignores_suffix_and_case():
    assert norm_name("ACME Supplies, Ltd.") == norm_name("acme supplies")


# ---------- detection / corrupted ----------
def test_detect_by_content_not_extension(tmp_path, scanned_png):
    renamed = tmp_path / "fake.pdf"
    renamed.write_bytes(open(scanned_png[0], "rb").read())
    kind, warns = detect_kind(str(renamed), "fake.pdf")
    assert kind == "image" and warns


def test_empty_and_garbage_files(tmp_path):
    e = tmp_path / "e.pdf"; e.write_bytes(b"")
    with pytest.raises(CorruptedFileError):
        detect_kind(str(e), "e.pdf")
    g = tmp_path / "g.pdf"; g.write_bytes(b"\x00\x01garbage" * 50)
    with pytest.raises(CorruptedFileError):
        detect_kind(str(g), "g.pdf")
    u = tmp_path / "x.bin"; u.write_bytes(b"\x00\x01\x02" * 50)
    with pytest.raises(UnsupportedFileError):
        detect_kind(str(u), "x.bin")


def test_truncated_pdf_fails_cleanly(digital_pdf, tmp_path):
    p = tmp_path / "t.pdf"; p.write_bytes(open(digital_pdf[0], "rb").read()[:200])
    with pytest.raises(CorruptedFileError):
        process_file(str(p), "t.pdf", use_llm=False)


# ---------- PDF ----------
def test_digital_pdf_uses_text_layer_not_ocr(digital_pdf):
    path, inv = digital_pdf
    pages = pdfmod.extract_pdf(path)
    assert pages[0].method == "text" and inv["invoice_number"] in pages[0].text


def test_multi_page_pdf_segments_into_invoices(tmp_path):
    import random
    rng = random.Random(3)
    invs = [synth.make_invoice(rng, i) for i in range(1, 6)]
    p = tmp_path / "multi.pdf"
    synth.write_digital_pdf(str(p), invs, [0, 1, 2, 0, 1])
    res = process_file(str(p), "multi.pdf", use_llm=False)
    assert [i["invoice_number"] for i in res.invoices] == [i["invoice_number"] for i in invs]
    assert [i["total"] for i in res.invoices] == [i["total"] for i in invs]


def test_continuation_page_is_merged():
    pages = [PageResult(1, "ACME\nInvoice No: INV-9001\nTotal: 10.00", "text"), PageResult(2, "continued items\nNotes only", "text"),
             PageResult(3, "Invoice No: INV-9002\nTotal: 20.00", "text")]
    segs = segment_pages(pages)
    assert [(s.start, s.end) for s in segs] == [(1, 2), (3, 3)]


def test_invoice_date_label_is_not_an_invoice_number():
    assert find_invoice_number("Invoice Date: 2024-01-05\nInvoice No: ABC-123") == "ABC-123"


# ---------- OCR ----------
@needs_tesseract
def test_ocr_image_extracts_invoice(scanned_png):
    path, inv = scanned_png
    res = process_file(path, "scan.png", use_llm=False)
    got = res.invoices[0]
    assert got["invoice_number"] == inv["invoice_number"]
    assert got["total"] == inv["total"] and got["invoice_date"] == inv["invoice_date"]
    assert res.pages[0].method == "ocr" and res.pages[0].confidence > 0.7


@needs_tesseract
def test_scanned_pdf_falls_back_to_ocr(tmp_path, invoice):
    p = tmp_path / "scan.pdf"
    synth.draw_page(synth.render_lines(invoice, 1), "noisy", seed=2).convert("RGB").save(str(p), "PDF", resolution=150)
    res = process_file(str(p), "scan.pdf", use_llm=False)
    assert res.pages[0].method == "ocr" and res.invoices[0]["total"] == invoice["total"]


@needs_tesseract
def test_poor_quality_scan_is_flagged_not_silently_trusted(tmp_path, invoice):
    p = tmp_path / "poor.png"
    synth.draw_page(synth.render_lines(invoice, 0), "poor", seed=5).save(p)
    res = process_file(str(p), "poor.png", use_llm=False)
    assert any("low OCR confidence" in w for w in res.warnings)
    iss = validate_batch(res.invoices)[0] if res.invoices else []
    assert (not res.invoices) or needs_review(iss)


def test_blank_image_has_no_invoice(tmp_path):
    from PIL import Image
    p = tmp_path / "blank.png"; Image.new("RGB", (800, 600), "white").save(p)
    if shutil.which("tesseract") is None:
        pytest.skip("tesseract not installed")
    res = process_file(str(p), "blank.png", use_llm=False)
    assert res.invoices == [] and res.doc_issues[0].code == "NO_INVOICE_FOUND"


# ---------- spreadsheets ----------
def test_xlsx_multiple_sheets_with_line_items(tmp_path):
    import random
    invs = [synth.make_invoice(random.Random(9), i) for i in range(1, 4)]
    p = tmp_path / "l.xlsx"
    synth.write_ledger(str(p), invs, "xlsx", with_items=True, extra_sheet=True)
    sheets = parse_spreadsheet(str(p), "xlsx")
    assert len(sheets) == 2 and sheets[1].fallback_text       # 'Notes' sheet is not mistaken for invoices
    recs = sheets[0].records
    assert len(recs) == 3 and recs[0]["total"] == invs[0]["total"]
    assert len(recs[0]["line_items"]) == len(invs[0]["line_items"])


def test_csv_semicolon_and_latin1(tmp_path):
    p = tmp_path / "x.csv"
    p.write_bytes("Invoice No;Date;Supplier;Total;Currency\nA-100;2024-02-01;Café Müller;110.50;EUR\n".encode("cp1252"))
    recs = parse_spreadsheet(str(p), "csv")[0].records
    assert recs[0]["supplier_name"] == "Café Müller" and recs[0]["total"] == 110.5


def test_ledger_row_without_invoice_number_is_kept_and_flagged(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("Invoice Number,Invoice Date,Supplier,Total,Currency\nA-1,2024-01-02,Acme,10,USD\n,2024-01-03,Beta,20,USD\n,,,30,\n")
    recs = parse_spreadsheet(str(p), "csv")[0].records
    assert len(recs) == 2 and recs[1]["invoice_number"] is None      # footer row (total only) ignored


def test_corrupted_xlsx(tmp_path):
    p = tmp_path / "bad.xlsx"; p.write_bytes(b"PK\x03\x04" + b"\x00" * 100)
    with pytest.raises(CorruptedFileError):
        process_file(str(p), "bad.xlsx", use_llm=False)


def test_empty_spreadsheet(tmp_path):
    p = tmp_path / "e.csv"; p.write_text(",\n,\n")
    with pytest.raises(CorruptedFileError):
        parse_spreadsheet(str(p), "csv")


# ---------- AI extraction ----------
TEXT = """Orion Office Supplies
INVOICE
Invoice No: INV-1001
Invoice Date: March 5, 2024
Due Date: 2024-04-05
Bill To: Globex Corporation
Description Qty Unit Price Amount
Widget A 2 10.00 20.00
Gadget B 1 80.00 80.00
Subtotal: $100.00
Tax (10%): $10.00
Total: $110.00
Currency: USD"""


def test_rules_extraction_complete():
    r = extract_invoice(TEXT, use_llm=False)
    assert (r["invoice_number"], r["invoice_date"], r["supplier_name"], r["customer_name"]) == \
           ("INV-1001", "2024-03-05", "Orion Office Supplies", "Globex Corporation")
    assert (r["subtotal"], r["tax"], r["total"], r["currency"]) == (100.0, 10.0, 110.0, "USD")
    assert len(r["line_items"]) == 2


def test_missing_values_stay_null_not_invented():
    r = extract_invoice("Some random memo\nCall me tomorrow about lunch", use_llm=False)
    assert all(r[k] is None for k in ("invoice_number", "invoice_date", "subtotal", "tax", "total", "currency"))


def test_due_date_never_used_as_invoice_date():
    r = extract_invoice("Acme\nInvoice No: X-77\nDue Date: 2024-05-05\nTotal: 5.00", use_llm=False)
    assert r["invoice_date"] != "2024-05-05"


def test_hallucinated_llm_values_are_discarded(monkeypatch):
    fake = {"invoice_number": "INV-1001", "invoice_date": "2024-03-05", "supplier_name": "Totally Invented Corp",
            "total": 999.99, "subtotal": 100, "tax": 10, "currency": "USD", "customer_name": "Globex Corporation",
            "line_items": [{"description": "Phantom item", "quantity": 1, "unit_price": 5, "amount": 5}],
            "confidence": {"supplier_name": 0.99, "total": 0.99}}
    monkeypatch.setattr(extract, "llm_available", lambda: True)
    monkeypatch.setattr(extract, "extract_with_llm", lambda t: fake)
    r = extract_invoice(TEXT)
    assert r["total"] == 110.0 or r["total"] is None      # 999.99 is not in the text -> dropped (rules refill)
    assert r["supplier_name"] != "Totally Invented Corp"
    assert all(i["description"] != "Phantom item" for i in r["line_items"])
    assert any("discarded" in n for n in r["notes"])


def test_llm_rules_disagreement_lowers_confidence(monkeypatch):
    fake = {"invoice_number": "INV-1001", "invoice_date": "2024-03-05", "supplier_name": "Orion Office Supplies",
            "total": 100.0, "subtotal": 100, "tax": 10, "currency": "USD", "customer_name": "Globex Corporation",
            "line_items": [], "confidence": {"total": 0.95}}
    monkeypatch.setattr(extract, "llm_available", lambda: True)
    monkeypatch.setattr(extract, "extract_with_llm", lambda t: fake)
    r = extract_invoice(TEXT)
    assert r["confidence"]["total"] <= 0.5 and any("disagrees" in n for n in r["notes"])


def test_llm_json_parsing_tolerates_fences():
    from app.core.llm import parse_json_object
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('Sure! {"a": 2} hope it helps') == {"a": 2}
    assert parse_json_object("not json") is None


# ---------- validation ----------
def good():
    return {"invoice_number": "A1", "invoice_date": "2024-01-10", "supplier_name": "Acme", "customer_name": "B", "subtotal": 100.0,
            "tax": 10.0, "total": 110.0, "currency": "USD", "confidence": {},
            "line_items": [{"description": "x", "quantity": 2, "unit_price": 50, "amount": 100}]}


def codes(inv, known=None):
    return {i.code for i in validate_invoice(inv, known)}


def test_clean_invoice_passes():
    assert not needs_review(validate_invoice(good()))


def test_missing_required_fields():
    inv = good(); inv["invoice_number"] = None; inv["total"] = None
    assert "MISSING_FIELD" in codes(inv) and needs_review(validate_invoice(inv))


def test_total_mismatch():
    inv = good(); inv["total"] = 120.0
    assert "TOTAL_MISMATCH" in codes(inv)


def test_subtotal_vs_line_items_mismatch():
    inv = good(); inv["subtotal"] = 90.0; inv["total"] = 100.0
    assert "SUBTOTAL_MISMATCH" in codes(inv)


def test_line_item_math():
    inv = good(); inv["line_items"][0]["amount"] = 120
    assert "LINE_ITEM_MISMATCH" in codes(inv)


def test_negative_and_invalid_values():
    inv = good(); inv["tax"] = -5.0; inv["invoice_date"] = "not-a-date"; inv["currency"] = "ZZZ"
    c = codes(inv)
    assert {"INVALID_NUMBER", "INVALID_DATE", "INVALID_CURRENCY"} <= c


def test_rounding_tolerance_one_cent():
    inv = good(); inv["total"] = 110.01
    assert "TOTAL_MISMATCH" not in codes(inv)


def test_duplicate_detection():
    known = [{"id": "x1", "invoice_number": "a-1", "supplier_name": "ACME Ltd.", "total": 110.0, "invoice_date": "2024-01-10"}]
    assert "DUPLICATE_INVOICE" in codes(good(), known)
    inv = good(); inv["invoice_number"] = "ZZ"
    assert "POSSIBLE_DUPLICATE" in codes(inv, known)
    other = [{"id": "x2", "invoice_number": "A1", "supplier_name": "Different Co", "total": 5.0, "invoice_date": "2020-01-01"}]
    assert "DUPLICATE_INVOICE" not in codes(good(), other)


def test_low_confidence_forces_review():
    inv = good(); inv["confidence"] = {"total": 0.3}
    assert needs_review(validate_invoice(inv))


# ---------- reconciliation ----------
def test_reconcile_pdf_vs_excel():
    a = [dict(id="p1", invoice_number="INV-1", supplier_name="Acme", total=110.0, currency="USD", invoice_date="2024-01-01"),
         dict(id="p2", invoice_number="INV-2", supplier_name="Acme", total=50.0, currency="USD", invoice_date="2024-01-02")]
    b = [dict(id="e1", invoice_number="inv 1", supplier_name="ACME Ltd", total=110.0, currency="USD", invoice_date="2024-01-01"),
         dict(id="e2", invoice_number="INV-2", supplier_name="Acme", total=55.0, currency="USD", invoice_date="2024-01-02"),
         dict(id="e3", invoice_number="INV-3", supplier_name="Acme", total=1.0, currency="USD", invoice_date="2024-01-03")]
    r = reconcile(a, b)
    assert r["summary"] == {"match": 1, "mismatch": 1, "missing_in_left": 1}
    assert r["rows"][1]["differences"][0]["field"] == "total"
