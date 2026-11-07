"use client";
import { useRef, useState } from "react";
import { API } from "@/lib/api";

const ACCEPT = ".pdf,.jpg,.jpeg,.png,.tif,.tiff,.xlsx,.xls,.csv";

export default function UploadPanel({ onUploaded }: { onUploaded: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [rejected, setRejected] = useState<{ filename: string; reason: string }[]>([]);

  async function send(files: FileList | File[]) {
    const list = Array.from(files);
    if (!list.length) return;
    setBusy(true); setMsg(null); setRejected([]);
    const fd = new FormData();
    list.forEach((f) => fd.append("files", f));
    try {
      const res = await fetch(`${API}/api/documents`, { method: "POST", body: fd, headers: { "X-API-Key": process.env.NEXT_PUBLIC_API_KEY || "" } });
      const j = await res.json();
      if (!res.ok) {
        const d = j.detail;
        setRejected(d?.rejected || []);
        setMsg(typeof d === "string" ? d : d?.message || "Upload failed");
      } else {
        setRejected(j.rejected || []);
        setMsg(`${j.documents.length} file(s) queued for processing.`);
      }
    } catch (e) { setMsg((e as Error).message); }
    setBusy(false); onUploaded();
  }

  return (
    <section className="panel" aria-labelledby="up">
      <h2 id="up">Add documents</h2>
      <div className={`drop ${over ? "over" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); send(e.dataTransfer.files); }}>
        <p style={{ margin: "0 0 10px" }}>Drop PDFs, scans, spreadsheets or CSVs here</p>
        <button disabled={busy} onClick={() => input.current?.click()}>{busy ? "Uploading…" : "Choose files"}</button>
        <input ref={input} type="file" multiple accept={ACCEPT} hidden onChange={(e) => e.target.files && send(e.target.files)} />
      </div>
      {msg && <p role="status">{msg}</p>}
      {rejected.map((r) => <p key={r.filename} className="err">{r.filename}: {r.reason}</p>)}
    </section>
  );
}
