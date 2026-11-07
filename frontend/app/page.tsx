"use client";
import { useCallback, useEffect, useState } from "react";
import { call } from "@/lib/api";
import type { Doc } from "@/lib/api";
import UploadPanel from "@/components/UploadPanel";
import QueuePanel from "@/components/QueuePanel";
import ReviewPanel from "@/components/ReviewPanel";
import SearchPanel from "@/components/SearchPanel";

type View = "queue" | "review" | "search";
const ACTIVE = ["queued", "processing", "retrying"];

export default function Home() {
  const [view, setView] = useState<View>("queue");
  const [docs, setDocs] = useState<Doc[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [openId, setOpenId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await call<{ items: Doc[]; counts: Record<string, number> }>("/documents?limit=100");
      setDocs(r.items); setCounts(r.counts); setError(null);
    } catch (e) { setError((e as Error).message); }
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {  // poll while anything is still running
    if (!docs.some((d) => ACTIVE.includes(d.status))) return;
    const t = setInterval(load, 1500);
    return () => clearInterval(t);
  }, [docs, load]);

  const open = (id: string) => { setOpenId(id); setView("review"); };

  return (
    <>
      <header className="top">
        <h1>Invoice desk</h1>
        <nav aria-label="Sections">
          {([["queue", "Documents"], ["review", "Review"], ["search", "Search"]] as [View, string][]).map(([v, label]) => (
            <button key={v} aria-current={view === v ? "page" : undefined} onClick={() => setView(v)}>{label}</button>
          ))}
        </nav>
      </header>
      <main>
        {error && <p className="err" role="alert">Cannot reach the API: {error}</p>}
        {view === "queue" && (<>
          <UploadPanel onUploaded={load} />
          <QueuePanel docs={docs} counts={counts} onOpen={open} onChange={load} />
        </>)}
        {view === "review" && <ReviewPanel docId={openId} docs={docs} onPick={setOpenId} onChanged={load} />}
        {view === "search" && <SearchPanel onOpen={open} />}
      </main>
    </>
  );
}
