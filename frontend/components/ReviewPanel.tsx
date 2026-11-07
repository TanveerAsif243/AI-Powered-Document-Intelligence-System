"use client";
import { Fragment, useCallback, useEffect, useState } from "react";
import { call, json } from "@/lib/api";
import type { Doc, Invoice, LineItem } from "@/lib/api";

type Result = { document: Doc; invoices: Invoice[]; document_issues: { code: string; message: string }[] };
type PageText = { page: number; method: string; ocr_confidence: number | null; text: string };
const FIELDS: [keyof Invoice, string, string][] = [
  ["invoice_number", "Invoice number", "text"], ["invoice_date", "Invoice date", "date"], ["supplier_name", "Supplier", "text"],
  ["customer_name", "Customer", "text"], ["currency", "Currency", "text"],
];
const AMOUNTS: [keyof Invoice, string][] = [["subtotal", "Subtotal"], ["tax", "Tax"], ["total", "Total"]];
const n = (v: string): number | null => (v.trim() === "" || isNaN(Number(v)) ? null : Number(v));
const close = (a: number, b: number) => Math.abs(a - b) <= Math.max(0.02, 0.001 * Math.max(Math.abs(a), Math.abs(b)));

export default function ReviewPanel({ docId, docs, onPick, onChanged }: { docId: string | null; docs: Doc[]; onPick: (id: string) => void; onChanged: () => void }) {
  const [res, setRes] = useState<Result | null>(null);
  const [pages, setPages] = useState<PageText[]>([]);
  const [idx, setIdx] = useState(0);
  const [draft, setDraft] = useState<Invoice | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    if (!docId) return;
    try {
      const r = await call<Result>(`/documents/${docId}/result`);
      setRes(r); setIdx(0); setDraft(r.invoices[0] ? structuredClone(r.invoices[0]) : null); setMsg(null);
      setPages(await call<PageText[]>(`/documents/${docId}/text`));
    } catch (e) { setRes(null); setDraft(null); setMsg((e as Error).message); }
  }, [docId]);
  useEffect(() => { load(); }, [load]);

  const pick = (i: number) => { setIdx(i); setDraft(res ? structuredClone(res.invoices[i]) : null); setMsg(null); };
  const set = (k: keyof Invoice, v: unknown) => setDraft((d) => (d ? { ...d, [k]: v } : d));
  const setItem = (i: number, k: keyof LineItem, v: string | number | null) =>
    setDraft((d) => d && { ...d, line_items: d.line_items.map((li, j) => (j === i ? { ...li, [k]: v } : li)) });

  async function save(approve: boolean) {
    if (!draft) return;
    setSaving(true);
    try {
      const { invoice_number, invoice_date, supplier_name, customer_name, subtotal, tax, total, currency, line_items } = draft;
      const out = await call<Invoice>(`/invoices/${draft.id}`, json("PATCH", { invoice_number, invoice_date, supplier_name, customer_name, subtotal, tax, total, currency, line_items, approve }));
      setRes((r) => r && { ...r, invoices: r.invoices.map((x, i) => (i === idx ? out : x)) });
      setDraft(structuredClone(out)); setMsg(approve ? "Approved." : "Saved and re-checked."); onChanged();
    } catch (e) { setMsg((e as Error).message); }
    setSaving(false);
  }

  if (!docId) return (
    <section className="panel"><h2>Review</h2>
      <p className="muted">Pick a document to inspect what was extracted.</p>
      <div className="row">{docs.filter((d) => d.status === "needs_review").slice(0, 8).map((d) => <button key={d.id} className="quiet" onClick={() => onPick(d.id)}>{d.filename}</button>)}</div>
    </section>);

  const sumOk = draft && draft.subtotal != null && draft.tax != null && draft.total != null ? close(draft.subtotal + draft.tax, draft.total) : null;
  const itemSum = draft && draft.line_items.length && draft.line_items.every((l) => l.amount != null) ? draft.line_items.reduce((s, l) => s + (l.amount || 0), 0) : null;

  return (
    <div className="split">
      <section className="panel" aria-labelledby="src">
        <h2 id="src">{res?.document.filename || "Source text"}</h2>
        {res?.document.warnings?.map((w, i) => <div className="issue" key={i}>{w}</div>)}
        {res?.document.error && <div className="issue error">{res.document.error}</div>}
        {pages.length === 0 ? <p className="muted">No extracted text.</p> : pages.slice(0, 40).map((p) => (
          <div key={p.page} style={{ marginBottom: 12 }}>
            <small className="muted">Page {p.page} · {p.method === "ocr" ? `scanned, OCR confidence ${Math.round((p.ocr_confidence || 0) * 100)}%` : p.method}</small>
            <pre className="src">{p.text || "(no text found)"}</pre>
          </div>))}
      </section>

      <section className="panel" aria-labelledby="ext">
        <h2 id="ext">Extracted invoice</h2>
        {res && res.invoices.length > 1 && (
          <div className="row" style={{ marginBottom: 12 }} role="tablist">
            {res.invoices.map((inv, i) => (
              <button key={inv.id} role="tab" aria-selected={i === idx} className={i === idx ? "" : "quiet"} onClick={() => pick(i)}>
                {inv.invoice_number || `Invoice ${i + 1}`}{inv.needs_review ? " ⚠" : ""}</button>))}
          </div>)}
        {res?.document_issues.map((i) => <div className="issue error" key={i.code}>{i.message}</div>)}
        {msg && <p role="status" className={msg.includes("Saved") || msg === "Approved." ? "" : "err"}>{msg}</p>}
        {!draft ? <p className="muted">{res ? "No invoices were extracted from this document." : "Loading…"}</p> : (<>
          <div className="fields">
            {FIELDS.map(([k, label, type]) => (
              <label key={k}>{label}{draft.confidence?.[k as string] != null && draft.confidence[k as string] < 0.6 && <span className="low"> · low confidence, check this</span>}
                <input type={type} value={(draft[k] as string) ?? ""} placeholder="not found" onChange={(e) => set(k, e.target.value || null)} />
              </label>))}
          </div>
          <div className={`equation ${sumOk === false ? "bad" : ""}`} aria-live="polite">
            {AMOUNTS.map(([k, label], i) => (
              <Fragment key={k}>
                {i > 0 && <div className="op" aria-hidden>{i === 1 ? "+" : "="}</div>}
                <div className={i === 2 ? "res" : ""}>
                  <small>{label}</small>
                  <input style={{ width: "100%", fontSize: 18 }} className={sumOk === false ? "mismatch" : ""} inputMode="decimal"
                    value={draft[k] ?? ""} placeholder="—" onChange={(e) => set(k, n(e.target.value))} aria-label={label} />
                </div>
              </Fragment>))}
          </div>
          {sumOk === false && <p className="err" style={{ marginTop: -6 }}>Subtotal + tax = {((draft.subtotal || 0) + (draft.tax || 0)).toFixed(2)}, which does not match the total.</p>}
          {itemSum != null && draft.subtotal != null && !close(itemSum, draft.subtotal) && <p className="err">Line items add up to {itemSum.toFixed(2)}, but the subtotal is {draft.subtotal.toFixed(2)}.</p>}

          <h2 style={{ marginTop: 16 }}>Line items</h2>
          {draft.line_items.length === 0 ? <p className="muted">No line items found.</p> : (
            <table><thead><tr><th>Description</th><th className="num">Qty</th><th className="num">Unit price</th><th className="num">Amount</th></tr></thead>
              <tbody>{draft.line_items.map((li, i) => (
                <tr key={i}>
                  <td><input value={li.description ?? ""} onChange={(e) => setItem(i, "description", e.target.value || null)} aria-label="Description" /></td>
                  {(["quantity", "unit_price", "amount"] as const).map((k) => (
                    <td key={k}><input className="num" style={{ textAlign: "right" }} value={li[k] ?? ""} onChange={(e) => setItem(i, k, n(e.target.value))} aria-label={k} /></td>))}
                </tr>))}</tbody></table>)}

          <h2 style={{ marginTop: 16 }}>Checks</h2>
          {draft.issues.length === 0 ? <p className="muted">All checks passed.</p> :
            draft.issues.map((i, k) => <div key={k} className={`issue ${i.severity}`}>{i.message}</div>)}
          <div className="row" style={{ marginTop: 14 }}>
            <button disabled={saving} onClick={() => save(false)}>Save changes</button>
            <button disabled={saving} className="quiet" onClick={() => save(true)}>Approve as is</button>
            {draft.manually_corrected && <span className="muted">Corrected by a reviewer</span>}
          </div>
        </>)}
      </section>
    </div>
  );
}
