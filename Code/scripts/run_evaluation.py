"""
Evaluation harness for the HLD Assistant.

Runs the ground-truth question set, the entity-extraction check and the seeded-issue check against a FRESH
temporary database, then writes the results to Evaluation_Results/.

Usage:  python scripts/run_evaluation.py            (uses Model_Prompts_Config/config.json)
        HLD_LLM_PROVIDER=ollama python scripts/run_evaluation.py   (evaluate with a local LLM)
"""
from __future__ import annotations

import csv
import json
import re
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hldassist import config as C  # noqa: E402
from hldassist.service import Service  # noqa: E402

GT = C.EVAL_DIR / "ground_truth"


def prf(pred: set, gold: set):
    tp = len(pred & gold)
    p = tp / len(pred) if pred else 0.0
    r = tp / len(gold) if gold else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return round(p, 3), round(r, 3), round(f, 3)


def main() -> None:
    cfg = C.load_config()
    tmp = tempfile.mkdtemp(prefix="hld_eval_")
    svc = Service(cfg, data_dir=Path(tmp))
    qa_set = json.loads((GT / "ground_truth_qa.json").read_text(encoding="utf-8"))
    rows, lat = [], []
    hit = {1: 0, 3: 0, 5: 0}
    rr = []
    n_pos = n_neg = ref_ok = false_ref = ans_pass = cite_ok = 0
    grounded = []
    tools_used = 0
    for q in qa_set:
        st = svc._project_state(q["project"])
        t0 = time.time()
        res = svc.ask(q["project"], q["question"], "eval")
        lat.append((time.time() - t0) * 1000)
        row = dict(id=q["id"], type=q["type"], project=q["project"], question=q["question"], refused=res["refused"],
                   mode=res["tool"] or ("rag" if not res["refused"] else "refusal"), confidence=res["confidence"],
                   latency_ms=res["latency_ms"])
        if q["expect_refusal"]:
            n_neg += 1
            ok = res["refused"]
            ref_ok += int(ok)
            row.update(retrieval_rank="", cited_gold="", fact_recall="", result="PASS" if ok else "FAIL",
                       note="correctly refused" if ok else "answered an out-of-scope question")
        else:
            n_pos += 1
            gold = {tuple(g) for g in q["gold_sections"]}
            # retrieval-only ranking (no tools): where does the first gold section appear?
            hits = st["index"].search(q["question"], 10)["hits"]
            rank = None
            for r, h in enumerate(hits, 1):
                c = st["index"].chunks[h["idx"]]
                if (c["doc_id"], c["sec_id"]) in gold:
                    rank = r
                    break
            for k in hit:
                hit[k] += int(rank is not None and rank <= k)
            rr.append(1 / rank if rank else 0.0)
            tags = set(re.findall(r"\[(S\d+)\]", res["answer"]))
            cited = {(e["doc_id"], e["sec_id"]) for e in res["citations"] if e["tag"] in tags}
            c_ok = bool(cited & gold)
            cite_ok += int(c_ok)
            low = res["answer"].lower()
            facts = q["expected_facts"]
            fr = sum(1 for f in facts if f.lower() in low) / len(facts) if facts else 1.0
            ok = (not res["refused"]) and c_ok and fr >= 0.999
            ans_pass += int(ok)
            false_ref += int(res["refused"])
            if res["tool"]:
                tools_used += 1
            if res["groundedness"] is not None:
                grounded.append(res["groundedness"])
            note = []
            if res["refused"]: note.append("refused (false refusal)")
            if not c_ok: note.append("gold section not cited")
            if fr < 0.999: note.append(f"fact recall {fr:.2f}")
            row.update(retrieval_rank=rank or "miss", cited_gold=c_ok, fact_recall=round(fr, 3),
                       result="PASS" if ok else "FAIL", note="; ".join(note) or "ok")
        row["answer"] = res["answer"].replace("\n", " | ")[:400]
        rows.append(row)

    # ---- extraction quality ------------------------------------------------
    gold_ent = json.loads((GT / "entities_gold.json").read_text(encoding="utf-8"))
    ext = {}
    tot = {"p": [], "r": [], "f": []}
    for project, g in gold_ent.items():
        inv = svc._project_state(project)["inv"]
        pred = dict(components={c["id"] for c in inv["components"]}, interfaces={i["id"] for i in inv["interfaces"]},
                    signals={s["id"] for s in inv["signals"]}, ports={f"{p['component']}.{p['name']}" for p in inv["ports"]})
        for kind in ("components", "ports", "interfaces", "signals"):
            p, r, f = prf(pred[kind], set(g[kind]))
            ext[f"{project}/{kind}"] = dict(precision=p, recall=r, f1=f, gold=len(g[kind]), predicted=len(pred[kind]))
            tot["p"].append(p); tot["r"].append(r); tot["f"].append(f)

    # ---- issue detection ---------------------------------------------------
    seeded = json.loads((GT / "issues_seeded.json").read_text(encoding="utf-8"))
    found_all, found_hm = set(), set()
    for project in gold_ent:
        for f in svc.findings(project):
            found_all.add((project, f["fingerprint"]))
            if f["severity"] in ("high", "medium"):
                found_hm.add((project, f["fingerprint"]))
    seeded_set = {(s["project"], f"{s['rule']}:{s['key']}") for s in seeded}
    issue = dict(seeded=len(seeded_set), detected=len(seeded_set & found_all),
                 recall=round(len(seeded_set & found_all) / len(seeded_set), 3),
                 high_medium_findings=len(found_hm),
                 precision_high_medium=round(len(seeded_set & found_hm) / len(found_hm), 3) if found_hm else 0.0,
                 low_severity_informational=len(found_all) - len(found_hm),
                 missed=sorted(f"{p}:{k}" for p, k in seeded_set - found_all),
                 unexpected_high_medium=sorted(f"{p}:{k}" for p, k in found_hm - seeded_set))

    lat_sorted = sorted(lat)
    summary = dict(
        generated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        configuration=svc.system_info(),
        counts=dict(questions=len(qa_set), answerable=n_pos, out_of_scope=n_neg),
        retrieval=dict(hit_at_1=round(hit[1] / n_pos, 3), hit_at_3=round(hit[3] / n_pos, 3), hit_at_5=round(hit[5] / n_pos, 3),
                       mrr=round(statistics.mean(rr), 3), note="hybrid retrieval only, tools disabled"),
        answers=dict(answer_pass_rate=round(ans_pass / n_pos, 3), citation_accuracy=round(cite_ok / n_pos, 3),
                     false_refusal_rate=round(false_ref / n_pos, 3), mean_groundedness=round(statistics.mean(grounded), 3),
                     answered_by_structured_tool=tools_used),
        refusal=dict(correct_refusals=ref_ok, out_of_scope=n_neg, refusal_accuracy=round(ref_ok / n_neg, 3)),
        extraction=dict(per_entity=ext, macro_precision=round(statistics.mean(tot["p"]), 3),
                        macro_recall=round(statistics.mean(tot["r"]), 3), macro_f1=round(statistics.mean(tot["f"]), 3)),
        issues=issue,
        latency_ms=dict(mean=round(statistics.mean(lat), 1), p95=round(lat_sorted[int(0.95 * (len(lat_sorted) - 1))], 1),
                        max=round(max(lat), 1)),
    )
    C.EVAL_DIR.mkdir(exist_ok=True)
    (C.EVAL_DIR / "evaluation_results.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    with open(C.EVAL_DIR / "qa_results.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    fails = [r for r in rows if r["result"] == "FAIL"]
    md = ["# Evaluation report", "", f"Generated: {summary['generated_at']}  ",
          f"Configuration: {summary['configuration']['embedding']} + {summary['configuration']['vector_store']} + LLM: {summary['configuration']['llm']}", "",
          f"* Questions: {len(qa_set)} ({n_pos} answerable, {n_neg} out-of-scope)",
          f"* Retrieval hit@1/3/5: {summary['retrieval']['hit_at_1']} / {summary['retrieval']['hit_at_3']} / {summary['retrieval']['hit_at_5']}, MRR {summary['retrieval']['mrr']}",
          f"* Answer pass rate: {summary['answers']['answer_pass_rate']} | citation accuracy: {summary['answers']['citation_accuracy']} | false refusals: {summary['answers']['false_refusal_rate']}",
          f"* Out-of-scope refusal accuracy: {summary['refusal']['refusal_accuracy']}",
          f"* Extraction macro P/R/F1: {summary['extraction']['macro_precision']} / {summary['extraction']['macro_recall']} / {summary['extraction']['macro_f1']}",
          f"* Seeded issue recall: {issue['recall']} ({issue['detected']}/{issue['seeded']}); precision on high/medium findings: {issue['precision_high_medium']}",
          f"* Latency mean / p95: {summary['latency_ms']['mean']} ms / {summary['latency_ms']['p95']} ms", "",
          "## Failed or weak cases", ""]
    md += [f"* {r['id']} - {r['question']} -> {r['note']}" for r in fails] or ["* none"]
    (C.EVAL_DIR / "evaluation_report.md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "extraction"}, indent=2))
    print("FAILED CASES:", [(r["id"], r["note"]) for r in fails])


if __name__ == "__main__":
    main()
