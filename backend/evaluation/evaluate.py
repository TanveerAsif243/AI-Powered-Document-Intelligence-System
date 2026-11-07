"""Run the pipeline over a labelled dataset and report MEASURED metrics.

Usage:  python -m evaluation.evaluate [dataset_dir] [--regen] [--llm]
Outputs: evaluation/results/metrics.json and evaluation/results/report.md
"""
import json
import os
import statistics
import sys
import time
from collections import defaultdict
from difflib import SequenceMatcher

from app.core.pipeline import process_file
from app.core.utils import norm_invoice_no, norm_name
from app.core.validation import needs_review, validate_batch
from evaluation.synth import build_dataset

AMT = ("subtotal", "tax", "total")


def _field_ok(f, truth, got):
    if truth is None:
        return got is None            # must NOT invent a value
    if got is None:
        return False
    if f in AMT:
        return abs(truth - got) <= 0.01
    if f == "invoice_number":
        return norm_invoice_no(truth) == norm_invoice_no(got)
    if f in ("supplier_name", "customer_name"):
        a, b = norm_name(truth) or "", norm_name(got) or ""
        return a == b or SequenceMatcher(None, a, b).ratio() >= 0.9
    return truth == got


def _match(truths, got):
    """Pair extracted invoices with ground truth by invoice number, else by order."""
    used, pairs = set(), []
    for t in truths:
        k = norm_invoice_no(t["invoice_number"])
        j = next((i for i, g in enumerate(got) if i not in used and k and norm_invoice_no(g.get("invoice_number")) == k), None)
        pairs.append((t, j))
        if j is not None:
            used.add(j)
    free = [i for i in range(len(got)) if i not in used]
    return [(t, (free.pop(0) if j is None and free else j)) for t, j in pairs]


def run(dataset_dir, use_llm=False):
    gt = json.load(open(os.path.join(dataset_dir, "ground_truth.json")))
    fields = ["invoice_number", "invoice_date", "supplier_name", "customer_name", "subtotal", "tax", "total", "currency"]
    ok, tot = defaultdict(int), defaultdict(int)
    kind_ok, kind_tot = defaultdict(lambda: defaultdict(int)), defaultdict(lambda: defaultdict(int))
    times, by_kind_time = [], defaultdict(list)
    docs_ok = docs_failed = expected_fail_ok = expected_fail_total = 0
    li_tp = li_fp = li_fn = 0
    inv_exact = inv_total = 0
    all_extracted, all_truth_meta, per_file, all_good = [], [], [], []
    for entry in gt:
        path = os.path.join(dataset_dir, entry["file"])
        t0 = time.perf_counter()
        try:
            res = process_file(path, entry["file"], use_llm=use_llm)
            err = None
        except Exception as e:
            res, err = None, f"{type(e).__name__}: {e}"
        dt = time.perf_counter() - t0
        times.append(dt)
        by_kind_time[entry["kind"]].append(dt)
        if entry["expect_fail"]:
            expected_fail_total += 1
            expected_fail_ok += int(err is not None)
            per_file.append({"file": entry["file"], "status": "failed_as_expected" if err else "UNEXPECTEDLY_OK", "seconds": round(dt, 2)})
            continue
        if err or not res.invoices:
            docs_failed += 1
            per_file.append({"file": entry["file"], "status": "FAILED", "error": err or "no invoice found", "seconds": round(dt, 2)})
            for t in entry["invoices"]:
                inv_total += 1
                for f in fields:
                    tot[f] += 1; kind_tot[entry["kind"]][f] += 1
            continue
        docs_ok += 1
        per_file.append({"file": entry["file"], "status": "ok", "invoices": len(res.invoices), "seconds": round(dt, 2)})
        pairs = _match(entry["invoices"], res.invoices)
        for t, j in pairs:
            g = res.invoices[j] if j is not None else {}
            inv_total += 1
            good = True
            for f in fields:
                tot[f] += 1; kind_tot[entry["kind"]][f] += 1
                if _field_ok(f, t[f], g.get(f)):
                    ok[f] += 1; kind_ok[entry["kind"]][f] += 1
                else:
                    good = False
            inv_exact += int(good)
            tl = [round(i["amount"], 2) for i in t["line_items"]]
            gl = [round(i["amount"], 2) for i in (g.get("line_items") or []) if i.get("amount") is not None]
            for a in tl:
                if a in gl:
                    li_tp += 1; gl.remove(a)
                else:
                    li_fn += 1
            li_fp += len(gl)
            if j is not None:
                all_extracted.append(g); all_truth_meta.append(t); all_good.append(good)
    # validation detection quality (expected issues injected as faults)
    issues = validate_batch(all_extracted)
    flagged = [needs_review(i) for i in issues]
    tp = fp = fn = tn = 0
    code_hit = code_total = 0
    for t, iss, fl in zip(all_truth_meta, issues, flagged):
        faulty = bool(t["expected_issues"])
        tp += faulty and fl; fn += faulty and not fl; fp += (not faulty) and fl; tn += (not faulty) and not fl
        for c in t["expected_issues"]:
            code_total += 1; code_hit += any(i.code == c for i in iss)
    wrong = [k for k, g_ in enumerate(all_good) if not g_]
    silent = sum(1 for k in wrong if not flagged[k])
    pct = lambda a, b: round(100 * a / b, 2) if b else None
    prec, rec = pct(tp, tp + fp), pct(tp, tp + fn)
    n_docs = len([e for e in gt if not e["expect_fail"]])
    srt = sorted(times)
    m = {
        "dataset": {"files": len(gt), "labelled_invoices": inv_total, "expected_corrupt_files": expected_fail_total, "llm_used": use_llm},
        "field_accuracy_pct": {f: pct(ok[f], tot[f]) for f in fields},
        "overall_field_accuracy_pct": pct(sum(ok.values()), sum(tot.values())),
        "invoice_exact_match_pct": pct(inv_exact, inv_total),
        "line_item_amount_f1_pct": pct(2 * li_tp, 2 * li_tp + li_fp + li_fn),
        "document_success_rate_pct": pct(docs_ok, n_docs),
        "document_failure_rate_pct": pct(docs_failed, n_docs),
        "corrupt_files_rejected_cleanly_pct": pct(expected_fail_ok, expected_fail_total),
        "processing_seconds": {"mean": round(statistics.mean(times), 2), "p50": round(srt[len(srt) // 2], 2),
                               "p95": round(srt[min(len(srt) - 1, int(len(srt) * 0.95))], 2), "total": round(sum(times), 1)},
        "seconds_by_kind_mean": {k: round(statistics.mean(v), 2) for k, v in by_kind_time.items()},
        "accuracy_by_kind_pct": {k: pct(sum(kind_ok[k].values()), sum(kind_tot[k].values())) for k in kind_tot},
        "validation": {"flag_precision_pct": prec, "flag_recall_pct": rec, "true_pos": tp, "false_pos": fp,
                       "false_neg": fn, "true_neg": tn, "injected_error_code_detected_pct": pct(code_hit, code_total),
                       "wrong_extractions": len(wrong), "wrong_but_not_flagged": silent,
                       "wrong_extractions_flagged_pct": pct(len(wrong) - silent, len(wrong))},
        "per_file": per_file,
    }
    return m


def write_report(m, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    json.dump(m, open(os.path.join(out_dir, "metrics.json"), "w"), indent=1)
    L = ["# Evaluation report (measured)", "",
         f"Dataset: {m['dataset']['files']} files, {m['dataset']['labelled_invoices']} labelled invoices, "
         f"{m['dataset']['expected_corrupt_files']} deliberately corrupted files. LLM used: **{m['dataset']['llm_used']}**.", "",
         "## Extraction accuracy (field-level, exact/normalised match; a value invented for an empty field counts as wrong)", "",
         "| Field | Accuracy % |", "|---|---|"]
    L += [f"| {f} | {v} |" for f, v in m["field_accuracy_pct"].items()]
    L += ["", f"**Overall field accuracy:** {m['overall_field_accuracy_pct']}%  ", f"**Invoices with every field correct:** {m['invoice_exact_match_pct']}%  ",
          f"**Line-item amount F1:** {m['line_item_amount_f1_pct']}%", "", "## Accuracy by source", "", "| Source | Field accuracy % | Mean s/file |", "|---|---|---|"]
    L += [f"| {k} | {v} | {m['seconds_by_kind_mean'].get(k)} |" for k, v in m["accuracy_by_kind_pct"].items()]
    p = m["processing_seconds"]
    L += ["", "## Reliability and speed", "", f"- Document success rate: **{m['document_success_rate_pct']}%**",
          f"- Document failure rate (valid files that failed): **{m['document_failure_rate_pct']}%**",
          f"- Corrupted files rejected cleanly (no crash): **{m['corrupt_files_rejected_cleanly_pct']}%**",
          f"- Processing time per file: mean {p['mean']}s, p50 {p['p50']}s, p95 {p['p95']}s (total {p['total']}s, single process)", "",
          "## Validation (flagging for manual review)", ""]
    v = m["validation"]
    L += [f"- Precision: **{v['flag_precision_pct']}%**, recall: **{v['flag_recall_pct']}%** (TP {v['true_pos']}, FP {v['false_pos']}, FN {v['false_neg']}, TN {v['true_neg']})",
          f"- Injected arithmetic/missing-field errors detected with the correct code: **{v['injected_error_code_detected_pct']}%**",
          f"- Extraction mistakes caught by review flags: **{v['wrong_extractions_flagged_pct']}%** ({v['wrong_extractions']} wrong invoices, {v['wrong_but_not_flagged']} silently accepted)", "",
          "## Per file", "", "| File | Status | Seconds |", "|---|---|---|"]
    L += [f"| {r['file']} | {r['status']}{(' - ' + r['error']) if r.get('error') else ''} | {r['seconds']} |" for r in m["per_file"]]
    open(os.path.join(out_dir, "report.md"), "w").write("\n".join(L) + "\n")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    ds = args[0] if args else os.path.join(os.path.dirname(__file__), "dataset")
    if "--regen" in sys.argv or not os.path.exists(os.path.join(ds, "ground_truth.json")):
        build_dataset(ds)
    metrics = run(ds, use_llm="--llm" in sys.argv)
    write_report(metrics, os.path.join(os.path.dirname(__file__), "results"))
    print(json.dumps({k: v for k, v in metrics.items() if k != "per_file"}, indent=1))
