"use client";
import { useState } from "react";
import { call } from "@/lib/api";
import type { SearchHit } from "@/lib/api";

export default function SearchPanel({ onOpen }: { onOpen: (id: string) => void }) {
  const [q, setQ] = useState("");
  const [mode, setMode] = useState<"keyword" | "semantic">("keyword");
  const [f, setF] = useState({ supplier: "", status: "", currency: "", min_total: "", max_total: "", needs_review: "" });
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function run(e: React.FormEvent) {
    e.preventDefault(); setErr(null);
    const p = new URLSearchParams({ q, mode });
    Object.entries(f).forEach(([k, v]) => v && p.set(k, v));
    try { setHits((await call<{ results: SearchHit[] }>(`/search?${p}`)).results); } catch (x) { setErr((x as Error).message); setHits(null); }
  }
  const upd = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });

  return (
    <section className="panel" aria-labelledby="s">
      <h2 id="s">Search processed documents</h2>
      <form onSubmit={run}>
        <div className="row">
          <input style={{ flex: 1, minWidth: 220 }} value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search text"
            placeholder={mode === "keyword" ? "Supplier, invoice number or words in the document" : "Describe what you are looking for"} />
          <select value={mode} onChange={(e) => setMode(e.target.value as "keyword" | "semantic")} aria-label="Search type">
            <option value="keyword">Keyword</option><option value="semantic">Meaning</option></select>
          <button type="submit">Search</button>
        </div>
        <div className="row" style={{ marginTop: 10 }}>
          <input placeholder="Supplier" value={f.supplier} onChange={upd("supplier")} aria-label="Supplier" />
          <input placeholder="Currency" size={6} value={f.currency} onChange={upd("currency")} aria-label="Currency" />
          <input placeholder="Min total" size={9} value={f.min_total} onChange={upd("min_total")} aria-label="Minimum total" />
          <input placeholder="Max total" size={9} value={f.max_total} onChange={upd("max_total")} aria-label="Maximum total" />
          <select value={f.status} onChange={upd("status")} aria-label="Status"><option value="">Any status</option>
            <option value="completed">Done</option><option value="needs_review">Needs review</option><option value="failed">Failed</option></select>
          <select value={f.needs_review} onChange={upd("needs_review")} aria-label="Review flag"><option value="">Review: any</option>
            <option value="true">Flagged for review</option><option value="false">Not flagged</option></select>
        </div>
      </form>
      {err && <p className="err" role="alert">{err}</p>}
      {hits && (hits.length === 0 ? <p className="muted" style={{ marginTop: 14 }}>No documents match. Try fewer filters or the other search type.</p> :
        <table style={{ marginTop: 14 }}><thead><tr><th>Document</th><th>Invoices</th><th>Match</th></tr></thead>
          <tbody>{hits.map((h) => (
            <tr key={h.document_id} className="click" onClick={() => onOpen(h.document_id)}>
              <td>{h.filename}<div><span className={`badge ${h.status}`}>{h.status.replace("_", " ")}</span></div></td>
              <td>{h.invoices.map((i) => <div key={i.id}>{i.invoice_number || "no number"} · {i.supplier_name || "unknown supplier"} · {i.total ?? "—"} {i.currency || ""}</div>)}</td>
              <td className="muted" style={{ maxWidth: 360, fontSize: 13 }}>{h.snippet}</td>
            </tr>))}</tbody></table>)}
    </section>
  );
}
