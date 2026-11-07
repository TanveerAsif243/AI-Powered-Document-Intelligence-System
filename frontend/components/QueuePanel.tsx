"use client";
import { call } from "@/lib/api";
import type { Doc } from "@/lib/api";

const LABEL: Record<string, string> = { queued: "Queued", processing: "Processing", retrying: "Retrying", completed: "Done", needs_review: "Needs review", failed: "Failed" };

export default function QueuePanel({ docs, counts, onOpen, onChange }: { docs: Doc[]; counts: Record<string, number>; onOpen: (id: string) => void; onChange: () => void }) {
  async function reprocess(id: string) {
    try { await call(`/documents/${id}/reprocess`, { method: "POST" }); } catch (e) { alert((e as Error).message); }
    onChange();
  }
  async function retryAll() { await call("/documents/reprocess-failed", { method: "POST" }); onChange(); }
  const active = (counts.queued || 0) + (counts.processing || 0) + (counts.retrying || 0);

  return (
    <section className="panel" aria-labelledby="q">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 id="q">Documents</h2>
        {!!counts.failed && <button className="quiet" onClick={retryAll}>Retry all {counts.failed} failed</button>}
      </div>
      <div className="counts">
        <span><b>{active}</b>in progress</span><span><b>{counts.needs_review || 0}</b>need review</span>
        <span><b>{counts.completed || 0}</b>done</span><span><b>{counts.failed || 0}</b>failed</span>
      </div>
      {docs.length === 0 ? <p className="muted">Nothing here yet. Add a document above to get started.</p> : (
        <table>
          <thead><tr><th>File</th><th>Status</th><th>Progress</th><th className="num">Pages</th><th className="num">Time</th><th /></tr></thead>
          <tbody>
            {docs.map((d) => (
              <tr key={d.id} className="click" onClick={() => onOpen(d.id)}>
                <td>{d.filename}{d.duplicate_of && <span className="muted"> · same file uploaded before</span>}
                  {d.error && <div className="err" style={{ fontSize: 13 }}>{d.error}</div>}</td>
                <td><span className={`badge ${d.status}`}>{LABEL[d.status] || d.status}</span></td>
                <td>{["queued", "processing", "retrying"].includes(d.status)
                  ? <><div className="bar" role="progressbar" aria-valuenow={d.progress} aria-valuemin={0} aria-valuemax={100}><i style={{ width: `${d.progress}%` }} /></div><small className="muted">{d.stage}</small></>
                  : null}</td>
                <td className="num">{d.pages || ""}</td>
                <td className="num">{d.processing_ms != null ? `${(d.processing_ms / 1000).toFixed(1)}s` : ""}</td>
                <td onClick={(e) => e.stopPropagation()}>{d.status === "failed" && <button className="quiet" onClick={() => reprocess(d.id)}>Reprocess</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
