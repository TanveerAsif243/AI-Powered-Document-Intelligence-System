export const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const KEY = process.env.NEXT_PUBLIC_API_KEY || "";

export type Doc = {
  id: string; filename: string; kind: string | null; status: string; progress: number; stage: string | null;
  error: string | null; pages: number; duplicate_of: string | null; warnings: string[]; processing_ms: number | null;
  attempts: number; created_at: string;
};
export type Issue = { code: string; severity: "error" | "warning"; field: string | null; message: string };
export type LineItem = { description: string | null; quantity: number | null; unit_price: number | null; amount: number | null };
export type Invoice = {
  id: string; document_id: string; invoice_number: string | null; invoice_date: string | null; supplier_name: string | null;
  customer_name: string | null; subtotal: number | null; tax: number | null; total: number | null; currency: string | null;
  confidence: Record<string, number>; line_items: LineItem[]; issues: Issue[]; needs_review: boolean;
  reviewed: boolean; manually_corrected: boolean; page_start: number | null; page_end: number | null; source: string | null;
};
export type SearchHit = {
  document_id: string; filename: string; status: string; score: number; snippet: string;
  invoices: { id: string; invoice_number: string | null; supplier_name: string | null; total: number | null; currency: string | null; needs_review: boolean }[];
};

export async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API}/api${path}`, { ...init, headers: { "X-API-Key": KEY, ...(init.headers || {}) } });
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch {}
    throw new Error(msg);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

export const json = (method: string, body: unknown): RequestInit => ({
  method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});
