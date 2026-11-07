"""API tests (FastAPI TestClient). Background jobs run inline, so results are available right after upload."""
import io
import random

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
from fastapi.testclient import TestClient

from app.main import app
from evaluation import synth

H = {"X-API-Key": "test-key"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def pdf_bytes(tmp_path, inv, layout=0, name="a.pdf"):
    p = tmp_path / name
    synth.write_digital_pdf(str(p), [inv], [layout])
    return p.read_bytes()


def upload(client, files):
    return client.post("/api/documents", files=[("files", f) for f in files], headers=H)


def test_auth_required(client):
    assert client.get("/api/documents").status_code == 401
    assert client.get("/api/documents", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/health").status_code == 200


def test_upload_process_and_result(client, tmp_path):
    inv = synth.make_invoice(random.Random(11), 101)
    r = upload(client, [("a.pdf", pdf_bytes(tmp_path, inv), "application/pdf")])
    assert r.status_code == 202
    doc_id = r.json()["documents"][0]["id"]
    st = client.get(f"/api/documents/{doc_id}/status", headers=H).json()
    assert st["status"] == "completed" and st["progress"] == 100
    res = client.get(f"/api/documents/{doc_id}/result", headers=H).json()
    got = res["invoices"][0]
    assert got["invoice_number"] == inv["invoice_number"] and got["total"] == inv["total"]
    assert len(got["line_items"]) == len(inv["line_items"])
    detail = client.get(f"/api/documents/{doc_id}", headers=H).json()
    assert [e["status"] for e in detail["events"]][-1] == "completed"      # processing history recorded


def test_multiple_files_and_rejections(client, tmp_path):
    rng = random.Random(5)
    files = [("one.pdf", pdf_bytes(tmp_path, synth.make_invoice(rng, 201)), "application/pdf"),
             ("evil.exe", b"MZ\x90\x00", "application/octet-stream"),
             ("empty.csv", b"", "text/csv")]
    r = upload(client, files).json()
    assert len(r["documents"]) == 1 and {x["filename"] for x in r["rejected"]} == {"evil.exe", "empty.csv"}


def test_path_traversal_filename_is_neutralised(client, tmp_path):
    inv = synth.make_invoice(random.Random(12), 301)
    r = upload(client, [("../../etc/passwd.pdf", pdf_bytes(tmp_path, inv), "application/pdf")]).json()
    assert r["documents"][0]["filename"] == "passwd.pdf"


def test_corrupted_file_fails_then_reprocess(client, tmp_path):
    r = upload(client, [("bad.pdf", b"%PDF-1.4 truncated garbage", "application/pdf")]).json()
    d = r["documents"][0]["id"]
    st = client.get(f"/api/documents/{d}/status", headers=H).json()
    assert st["status"] == "failed" and "Corrupted" in st["error"]
    assert client.get(f"/api/documents/{d}/result", headers=H).status_code == 200   # terminal state: result is readable (empty)
    again = client.post(f"/api/documents/{d}/reprocess", headers=H)
    assert again.status_code == 202
    assert client.get(f"/api/documents/{d}/status", headers=H).json()["attempts"] >= 1
    assert client.post("/api/documents/reprocess-failed", headers=H).status_code == 202
    ok = upload(client, [("fine.pdf", pdf_bytes(tmp_path, synth.make_invoice(random.Random(13), 401)), "application/pdf")]).json()["documents"][0]["id"]
    assert client.post(f"/api/documents/{ok}/reprocess", headers=H).status_code == 409       # healthy docs need force=true


def test_validation_flags_and_manual_correction(client, tmp_path):
    inv = synth.make_invoice(random.Random(21), 501, fault="total_mismatch")
    d = upload(client, [("bad_total.pdf", pdf_bytes(tmp_path, inv), "application/pdf")]).json()["documents"][0]["id"]
    res = client.get(f"/api/documents/{d}/result", headers=H).json()
    i = res["invoices"][0]
    assert res["document"]["status"] == "needs_review" and any(x["code"] == "TOTAL_MISMATCH" for x in i["issues"])
    fixed = client.patch(f"/api/invoices/{i['id']}", json={"total": round(inv["subtotal"] + inv["tax"], 2)}, headers=H)
    assert fixed.status_code == 200
    assert not fixed.json()["needs_review"] and fixed.json()["manually_corrected"]
    assert client.get(f"/api/documents/{d}", headers=H).json()["status"] == "completed"
    corr = client.get(f"/api/invoices/{i['id']}/corrections", headers=H).json()
    assert corr[0]["field"] == "total" and corr[0]["old"] is not None                # audit trail
    assert client.patch(f"/api/invoices/{i['id']}", json={"invoice_date": "31/31/2024"}, headers=H).status_code == 422


def test_duplicate_invoice_across_files(client, tmp_path):
    inv = synth.make_invoice(random.Random(31), 601)
    upload(client, [("first.pdf", pdf_bytes(tmp_path, inv, 0), "application/pdf")])
    d2 = upload(client, [("second.pdf", pdf_bytes(tmp_path, inv, 1, "b.pdf"), "application/pdf")]).json()["documents"][0]["id"]
    i = client.get(f"/api/documents/{d2}/result", headers=H).json()["invoices"][0]
    assert any(x["code"] == "DUPLICATE_INVOICE" for x in i["issues"]) and i["needs_review"]


def test_identical_file_is_marked_duplicate(client, tmp_path):
    inv = synth.make_invoice(random.Random(41), 701)
    b = pdf_bytes(tmp_path, inv)
    a = upload(client, [("x.pdf", b, "application/pdf")]).json()["documents"][0]
    c = upload(client, [("y.pdf", b, "application/pdf")]).json()["documents"][0]
    assert c["duplicate_of"] == a["id"]


def test_keyword_and_semantic_search_with_filters(client, tmp_path):
    inv = synth.make_invoice(random.Random(51), 801)
    inv["supplier_name"] = "Zebrafish Hydraulics Ltd"
    d = upload(client, [("z.pdf", pdf_bytes(tmp_path, inv), "application/pdf")]).json()["documents"][0]["id"]
    kw = client.get("/api/search", params={"q": "Zebrafish"}, headers=H).json()["results"]
    assert any(r["document_id"] == d for r in kw)
    flt = client.get("/api/search", params={"supplier": "zebrafish", "currency": inv["currency"], "min_total": 1}, headers=H).json()["results"]
    assert [r["document_id"] for r in flt] == [d]
    none = client.get("/api/search", params={"supplier": "zebrafish", "max_total": 0.5}, headers=H).json()["results"]
    assert none == []
    sem = client.get("/api/search", params={"q": "hydraulics invoice", "mode": "semantic"}, headers=H).json()["results"]
    assert d in [r["document_id"] for r in sem[:3]]
    assert client.get("/api/search", params={"mode": "semantic"}, headers=H).status_code == 422


def test_spreadsheet_upload_and_reconcile_with_pdf(client, tmp_path):
    rng = random.Random(61)
    inv = synth.make_invoice(rng, 901)
    pdf_id = upload(client, [("p.pdf", pdf_bytes(tmp_path, inv), "application/pdf")]).json()["documents"][0]["id"]
    edited = dict(inv, total=round(inv["total"] + 1, 2))
    xl = tmp_path / "l.xlsx"
    synth.write_ledger(str(xl), [edited], "xlsx", with_items=False)
    x_id = upload(client, [("l.xlsx", xl.read_bytes(), "application/vnd.ms-excel")]).json()["documents"][0]["id"]
    rec = client.get("/api/reconcile", params={"left": pdf_id, "right": x_id}, headers=H).json()
    assert rec["summary"] == {"mismatch": 1} and rec["rows"][0]["differences"][0]["field"] == "total"


def test_unknown_ids_404(client):
    assert client.get("/api/documents/nope/status", headers=H).status_code == 404
    assert client.patch("/api/invoices/nope", json={"total": 1}, headers=H).status_code == 404
