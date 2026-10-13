"""Deterministic consistency and completeness checks over the extracted architecture inventory.

Each finding has a stable fingerprint (rule + key) so reviewer decisions survive re-runs.
Severity: high | medium | low (low = informational).
"""
from __future__ import annotations

from .graph import ArchGraph

LEVELS = {"QM": 0, "ASIL A": 1, "ASIL B": 2, "ASIL C": 3, "ASIL D": 4}


def _f(rule, key, severity, title, detail, evidence):
    return dict(rule=rule, key=key, fingerprint=f"{rule}:{key}", severity=severity, title=title,
                detail=detail, evidence=evidence)


def run_checks(inv: dict, suspicious: list[dict]) -> list[dict]:
    g = ArchGraph(inv)
    out: list[dict] = []
    defined_comps = set(g.components)

    # R1 consumer without any producer
    for sid, cons in sorted(g.requires.items()):
        if not g.provides.get(sid):
            for cc, cp in cons:
                out.append(_f("ORPHAN_CONSUMER", sid, "high", f"No producer for {sid}",
                              f"{cc} requires {sid} through port {cp['name']} but no component provides it.",
                              [cp["prov"]]))

    # R2 port references an interface that is not defined
    seen = set()
    for p in inv["ports"]:
        if p["interface"] not in g.interfaces and p["interface"] not in seen:
            seen.add(p["interface"])
            users = sorted({q["component"] for q in inv["ports"] if q["interface"] == p["interface"]})
            out.append(_f("UNDEFINED_INTERFACE", p["interface"], "high", f"Interface {p['interface']} is not defined",
                          f"Port {p['name']} of {p['component']} uses {p['interface']}, which has no definition in the "
                          f"approved documents. Used by: {', '.join(users)}.", [p["prov"]]))

    # R3 dependency on a component that is not defined
    seen = set()
    for c in inv["components"]:
        for d in c["depends_on"]:
            if d not in defined_comps and d not in seen:
                seen.add(d)
                out.append(_f("UNDEFINED_COMPONENT", d, "medium", f"Component {d} is referenced but not defined",
                              f"{c['id']} depends on {d}, but no section defines it.", [c["prov"]]))

    # R4 consumer timeout shorter than producer period
    for sid, cons in sorted(g.requires.items()):
        for cc, cp in cons:
            for pc, pp in g.provides.get(sid, []):
                if cp["timing_kind"] == "timeout" and pp["timing_kind"] == "period" and cp["timing_ms"] < pp["timing_ms"]:
                    out.append(_f("TIMING_MISMATCH", sid, "medium", f"Timeout shorter than update period for {sid}",
                                  f"{cc} expects {sid} within {cp['timing_ms']} ms, but {pc} publishes it only every "
                                  f"{pp['timing_ms']} ms.", [cp["prov"], pp["prov"]]))

    # R5 same signal defined with different type or unit
    by_sig: dict[str, list[dict]] = {}
    for s in inv["signals"]:
        by_sig.setdefault(s["id"], []).append(s)
    for sid, defs in sorted(by_sig.items()):
        if len({(d["dtype"], d["unit"]) for d in defs}) > 1:
            out.append(_f("TYPE_CONFLICT", sid, "high", f"Conflicting definitions of {sid}",
                          "; ".join(f"{d['prov']['doc_id']} v{d['prov']['version']}: {d['dtype']}, {d['unit']}, {d['range']}"
                                    for d in defs), [d["prov"] for d in defs]))

    # R6 component defined more than once with different type or safety level
    by_comp: dict[str, list[dict]] = {}
    for c in inv["components"]:
        by_comp.setdefault(c["id"], []).append(c)
    for cid, defs in sorted(by_comp.items()):
        if len(defs) > 1 and len({(d["type"], d["level"]) for d in defs}) > 1:
            out.append(_f("DUPLICATE_COMPONENT", cid, "medium", f"{cid} is defined differently in several documents",
                          "; ".join(f"{d['prov']['doc_id']}: {d['type']}, {d['level']}" for d in defs),
                          [d["prov"] for d in defs]))

    # R7 provided signal nobody consumes (informational)
    for sid, provs in sorted(g.provides.items()):
        if not g.requires.get(sid):
            pc, pp = provs[0]
            out.append(_f("UNCONNECTED_PROVIDER", f"{pc}.{pp['name']}", "low", f"Nobody consumes {sid}",
                          f"{pc} provides {sid} through {pp['name']}, but no component in the project requires it.",
                          [pp["prov"]]))

    # R8 component without a verification case
    for cid, c in sorted(g.components.items()):
        if not c["tests"]:
            out.append(_f("MISSING_VERIFICATION", cid, "medium", f"{cid} has no verification case",
                          "The component section lists no test case, so test coverage is incomplete.", [c["prov"]]))

    # R9 interface data element missing in the signal catalogue
    for iid, i in sorted(g.interfaces.items()):
        for se in i["data_elements"]:
            if se not in g.signals:
                out.append(_f("SIGNAL_NOT_IN_CATALOGUE", f"{iid}:{se}", "medium", f"{se} is missing in the signal catalogue",
                              f"Interface {iid} carries {se}, which has no entry in any signal catalogue.", [i["prov"]]))

    # R10 safety level gap: consumer has a higher level than its provider
    seen = set()
    for e in g.edges:
        if e["kind"] != "signal":
            continue
        a, b = g.components.get(e["source"]), g.components.get(e["target"])
        if a and b and LEVELS.get(b["level"], 0) > LEVELS.get(a["level"], 0):
            key = f"{a['id']}>{b['id']}"
            if key not in seen:
                seen.add(key)
                out.append(_f("SAFETY_LEVEL_GAP", key, "medium", f"{b['id']} ({b['level']}) relies on {a['id']} ({a['level']})",
                              f"{b['id']} consumes {e['signal']} from {a['id']} whose safety level is lower. "
                              "Freedom from interference or ASIL decomposition must be justified.", [a["prov"], b["prov"]]))

    # R11 suspicious (prompt-injection-like) content
    seen = set()
    for s in suspicious:
        if s["doc_id"] not in seen:
            seen.add(s["doc_id"])
            out.append(_f("SUSPICIOUS_CONTENT", s["doc_id"], "medium", f"Instruction-like text found in {s['doc_id']}",
                          "A passage that looks like an instruction to an AI assistant was found. It is excluded from "
                          "answers and must be reviewed by an engineer.", [s]))
    return out
