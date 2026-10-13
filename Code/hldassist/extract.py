"""Rule-based architecture entity extraction from sections that follow the HLD template.

Entities: components, ports, interfaces, signals. Every entity keeps provenance (document, version, page,
section) so every fact can be cited. Extraction is deterministic: it never invents an entity that is not
literally present in the text.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

KEYS = ("Component", "Type", "Safety Level", "Description", "Design Rationale", "Runnables", "Provided Ports",
        "Required Ports", "Depends On", "Error Handling", "Verification", "Interface", "Paradigm", "Data Elements")
KEY_RE = re.compile(r"^(%s):\s*(.*)$" % "|".join(re.escape(k) for k in KEYS))
PORT_RE = re.compile(r"^-\s*(\w+)\s*\|\s*(\w+)\s*\|\s*([\w\- ]+?)\s*\|\s*(period|timeout)\s+(\d+)\s*ms\s*$", re.I)
SIG_RE = re.compile(r"^(SIG_\w+)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*$")
ID_RE = re.compile(r"\b(?:SWC|SIG|SR|TC|CS|NV|CDD)_\w+")


def prov(doc: dict, sec, page: int) -> dict:
    return dict(doc_id=doc["doc_id"], version=doc["version"], title=doc["title"], page=page,
                sec_id=sec.sec_id, sec_title=sec.title)


def _fields(sec):
    """Collapse a section into {key: [(page, text), ...]} handling wrapped continuation lines."""
    fields: dict[str, list] = {}
    cur = None
    for page, line in sec.lines:
        m = KEY_RE.match(line)
        if m:
            cur = m.group(1)
            fields.setdefault(cur, [])
            if m.group(2):
                fields[cur].append([page, m.group(2)])
        elif cur is not None:
            if line.startswith("-") and cur in ("Provided Ports", "Required Ports"):
                fields[cur].append([page, line])
            elif fields[cur] and cur not in ("Provided Ports", "Required Ports"):
                fields[cur][-1][1] += " " + line  # wrapped line of the previous value
            elif fields[cur] and cur in ("Provided Ports", "Required Ports") and not line.startswith("-"):
                fields[cur][-1][1] += " " + line
    return fields


def _csv(items) -> list[str]:
    text = " ".join(t for _, t in items)
    return [x.strip() for x in text.split(",") if x.strip() and x.strip().lower() not in ("none", "none defined")]


def extract_document(doc: dict, sections: list) -> dict:
    out = dict(components=[], interfaces=[], signals=[], ports=[])
    for sec in sections:
        f = _fields(sec)
        if "Component" in f and f["Component"]:
            cid = f["Component"][0][1].split()[0]
            page = f["Component"][0][0]
            comp = dict(
                id=cid, type=" ".join(t for _, t in f.get("Type", [])), level=" ".join(t for _, t in f.get("Safety Level", [])),
                description=" ".join(t for _, t in f.get("Description", [])),
                rationale=" ".join(t for _, t in f.get("Design Rationale", [])),
                error_handling=" ".join(t for _, t in f.get("Error Handling", [])),
                runnables=_csv(f.get("Runnables", [])), depends_on=_csv(f.get("Depends On", [])),
                tests=_csv(f.get("Verification", [])), prov=prov(doc, sec, page))
            out["components"].append(comp)
            for direction, key in (("provided", "Provided Ports"), ("required", "Required Ports")):
                for pg, line in f.get(key, []):
                    m = PORT_RE.match(line.strip())
                    if m:
                        out["ports"].append(dict(
                            component=cid, name=m.group(1), direction=direction, interface=m.group(2),
                            paradigm=m.group(3).strip(), timing_kind=m.group(4).lower(), timing_ms=int(m.group(5)),
                            prov=prov(doc, sec, pg)))
        if "Interface" in f and f["Interface"]:
            out["interfaces"].append(dict(
                id=f["Interface"][0][1].split()[0], paradigm=" ".join(t for _, t in f.get("Paradigm", [])),
                data_elements=_csv(f.get("Data Elements", [])),
                description=" ".join(t for _, t in f.get("Description", [])), prov=prov(doc, sec, f["Interface"][0][0])))
        for page, line in sec.lines:
            m = SIG_RE.match(line.strip())
            if m:
                out["signals"].append(dict(id=m.group(1), dtype=m.group(2), unit=m.group(3), range=m.group(4),
                                           period=m.group(5), prov=prov(doc, sec, page)))
    return out


def merge_project(per_doc: list[dict]) -> dict:
    """Merge extraction results of all ACTIVE documents of a project (keeps duplicates for checks)."""
    merged = dict(components=[], interfaces=[], signals=[], ports=[])
    for r in per_doc:
        for k in merged:
            merged[k].extend(r[k])
    return merged


def mentioned_ids(text: str) -> list[str]:
    return ID_RE.findall(text)
