"""Revision comparison between two versions of the same document family."""
from __future__ import annotations

import difflib


def _index(items, key="id"):
    out = {}
    for it in items:
        out.setdefault(it[key], it)
    return out


def _plain(d: dict) -> dict:
    return {k: v for k, v in d.items() if k != "prov"}


def _port_key(p): return f"{p['component']}.{p['name']}"


def compare_inventories(a: dict, b: dict) -> dict:
    result = {}
    for kind, keyfn in (("components", lambda x: x["id"]), ("interfaces", lambda x: x["id"]),
                        ("signals", lambda x: x["id"]), ("ports", _port_key)):
        ia = {keyfn(x): x for x in a[kind]}
        ib = {keyfn(x): x for x in b[kind]}
        added = sorted(set(ib) - set(ia))
        removed = sorted(set(ia) - set(ib))
        changed = []
        for k in sorted(set(ia) & set(ib)):
            pa, pb = _plain(ia[k]), _plain(ib[k])
            diffs = {f: (pa[f], pb[f]) for f in pa if pa[f] != pb.get(f)}
            if diffs:
                changed.append(dict(id=k, changes={f: dict(old=o, new=n) for f, (o, n) in diffs.items()},
                                    prov=ib[k]["prov"]))
        result[kind] = dict(added=[dict(id=k, prov=ib[k]["prov"]) for k in added],
                            removed=[dict(id=k, prov=ia[k]["prov"]) for k in removed], changed=changed)
    return result


def compare_sections(sa: list, sb: list) -> list[dict]:
    ta = {f"{s.sec_id} {s.title}": "\n".join(l for _, l in s.lines) for s in sa}
    tb = {f"{s.sec_id} {s.title}": "\n".join(l for _, l in s.lines) for s in sb}
    out = []
    for k in sorted(set(ta) | set(tb)):
        if k not in ta:
            out.append(dict(section=k, status="added", diff=[]))
        elif k not in tb:
            out.append(dict(section=k, status="removed", diff=[]))
        elif ta[k] != tb[k]:
            diff = [d for d in difflib.unified_diff(ta[k].splitlines(), tb[k].splitlines(), lineterm="", n=0)
                    if not d.startswith(("---", "+++", "@@"))]
            out.append(dict(section=k, status="changed", diff=diff[:12]))
    return out


def summarize(inv_diff: dict) -> dict:
    return {k: dict(added=len(v["added"]), removed=len(v["removed"]), changed=len(v["changed"])) for k, v in inv_diff.items()}
