"""Dependency graph, producer/consumer resolution and impact analysis."""
from __future__ import annotations

from collections import defaultdict, deque


class ArchGraph:
    def __init__(self, inv: dict):
        self.inv = inv
        self.components = {}
        for c in inv["components"]:
            self.components.setdefault(c["id"], c)
        self.interfaces = {}
        for i in inv["interfaces"]:
            self.interfaces.setdefault(i["id"], i)
        self.signals = {}
        for s in inv["signals"]:
            self.signals.setdefault(s["id"], s)
        self.provides = defaultdict(list)   # signal -> [(component, port)]
        self.requires = defaultdict(list)   # signal -> [(component, port)]
        self.edges: list[dict] = []
        for p in inv["ports"]:
            iface = self.interfaces.get(p["interface"])
            if not iface:
                continue
            for sid in iface["data_elements"]:
                (self.provides if p["direction"] == "provided" else self.requires)[sid].append((p["component"], p))
        seen = set()
        for sid, cons in self.requires.items():
            for cc, cp in cons:
                for pc, pp in self.provides.get(sid, []):
                    if pc == cc:
                        continue
                    key = (pc, cc, sid)
                    if key not in seen:
                        seen.add(key)
                        self.edges.append(dict(source=pc, target=cc, signal=sid, interface=cp["interface"],
                                               kind="signal", prov=pp["prov"]))
        for c in inv["components"]:
            for d in c["depends_on"]:
                key = (d, c["id"], "depends")
                if key not in seen:
                    seen.add(key)
                    self.edges.append(dict(source=d, target=c["id"], signal=None, interface=None, kind="depends",
                                           prov=c["prov"]))
        self.down = defaultdict(set)
        self.up = defaultdict(set)
        for e in self.edges:
            # signal edges: producer -> consumer; "depends" edges: dependency -> dependent (same direction)
            self.down[e["source"]].add(e["target"])
            self.up[e["target"]].add(e["source"])

    def producers(self, signal): return sorted({c for c, _ in self.provides.get(signal, [])})
    def consumers(self, signal): return sorted({c for c, _ in self.requires.get(signal, [])})

    def impact(self, entity: str) -> dict:
        start = [entity] if entity in self.components else (self.consumers(entity) if entity in self.signals else [])
        direct, order, dist = set(), [], {}
        q = deque((s, 0) for s in start)
        seen = set(start)
        while q:
            node, d = q.popleft()
            for nxt in sorted(self.down.get(node, [])):
                if nxt not in seen:
                    seen.add(nxt); dist[nxt] = d + 1; order.append(nxt); q.append((nxt, d + 1))
                    if d == 0:
                        direct.add(nxt)
        return dict(entity=entity, direct=sorted(direct), transitive=order, depth=dist)

    def to_json(self) -> dict:
        nodes = []
        known = set(self.components)
        for cid, c in self.components.items():
            nodes.append(dict(id=cid, level=c["level"], type=c["type"], defined=True))
        for e in self.edges:
            for end in (e["source"], e["target"]):
                if end not in known:
                    known.add(end)
                    nodes.append(dict(id=end, level="?", type="undefined", defined=False))
        return dict(nodes=nodes, edges=[{k: v for k, v in e.items() if k != "prov"} for e in self.edges])
