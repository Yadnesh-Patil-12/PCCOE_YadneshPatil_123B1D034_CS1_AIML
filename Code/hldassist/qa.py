"""Question answering: deterministic tool routing over the parsed inventory + hybrid RAG + grounded answer."""
from __future__ import annotations

import re
import time

from .extract import ID_RE
from .llm import ollama_generate, validate_citations
from .textutil import logical_lines, query_terms, sentences, tokens

KEY_PREFIX = re.compile(r"^(Description|Design Rationale|Error Handling|Runnables|Type|Safety Level|Verification|Depends On):\s*")


class QAEngine:
    def __init__(self, cfg, prompts, index, inv, graph, project, docs):
        self.cfg, self.prompts, self.index, self.inv, self.g = cfg, prompts, index, inv, graph
        self.project, self.docs = project, docs
        self.refusal = prompts["refusal_message"]
        self.known_ids = {i["id"] for k in ("components", "interfaces", "signals") for i in inv[k]}
        for c in index.chunks:
            self.known_ids |= set(ID_RE.findall(c["text"]))
        self.lower_ids = {i.lower(): i for i in self.known_ids}
        self.loose = {}
        for i in self.known_ids:
            self.loose[re.sub(r"[_\W]", "", i.split("_", 1)[-1]).lower()] = i

    # ----------------------------------------------------------------- #
    def _entities(self, question: str):
        found, unknown = [], []
        for m in re.finditer(r"\b(?:SWC|SIG|SR|TC|CDD)_\w+", question, re.I):
            low = m.group(0).lower()
            if low in self.lower_ids:
                found.append(self.lower_ids[low])
            else:
                unknown.append(m.group(0))
        for w in re.findall(r"[A-Za-z]{6,}", question):
            hit = self.loose.get(w.lower())
            if hit and hit not in found and len(w) >= 8:
                found.append(hit)
        kind = lambda e: ("component" if e.startswith("SWC_") or e.startswith("CDD_") else "signal" if e.startswith("SIG_")
                          else "interface" if e.startswith("SR_") else "test")
        return [(e, kind(e)) for e in dict.fromkeys(found)], unknown

    def _comp_defs(self, cid): return [c for c in self.inv["components"] if c["id"] == cid]
    def _sig_defs(self, sid): return [s for s in self.inv["signals"] if s["id"] == sid]

    def _tool(self, question: str, ents):
        """Return (tool_name, [fact dicts]) or (None, [])."""
        ql = question.lower()
        comps = [e for e, k in ents if k == "component"]
        sigs = [e for e, k in ents if k == "signal"]
        ifs = [e for e, k in ents if k == "interface"]
        facts: list[dict] = []
        F = lambda text, prov, key=None: facts.append(dict(text=text, prov=prov, key=key))
        target = (comps or sigs or [None])[0]
        if target and re.search(r"impact|affect|downstream|ripple|knock", ql):
            imp = self.g.impact(target)
            if imp["transitive"]:
                d = ", ".join(imp["direct"]) or "none"
                allx = ", ".join(imp["transitive"])
                base = (self._comp_defs(target) or self._sig_defs(target))[0]["prov"]
                F(f"If {target} changes, the directly impacted components are: {d}. Including indirect effects the impacted "
                  f"components are: {allx}.", base, target)
                return "impact_analysis", facts
        if target and re.search(r"\bdefined\b|\bdefinition\b", ql) and re.search(r"where|which|what doc", ql):
            for d in (self._comp_defs(target) if target in {c["id"] for c in self.inv["components"]} else self._sig_defs(target)):
                p = d["prov"]
                F(f"{target} is defined in {p['doc_id']} v{p['version']}, section {p['sec_id']} (page {p['page']}).", p, target)
            if facts:
                return "definition_lookup", facts
        if sigs:
            s = sigs[0]
            if re.search(r"produc|provid|publish|sourc|generat|\bsends?\b|originat", ql):
                for pc, pp in self.g.provides.get(s, []):
                    F(f"{s} is produced by {pc} through port {pp['name']} ({pp['timing_kind']} {pp['timing_ms']} ms).", pp["prov"], pc)
                if not facts:
                    d = self._sig_defs(s)
                    if d:
                        F(f"No component provides {s} in the approved documents.", d[0]["prov"], s)
                return "signal_producers", facts
            if re.search(r"consum|receiv|requir|\buse[sd]?\b|\bread|subscrib|depend", ql):
                for cc, cp in self.g.requires.get(s, []):
                    F(f"{s} is consumed by {cc} through port {cp['name']} ({cp['timing_kind']} {cp['timing_ms']} ms).", cp["prov"], cc)
                if not facts:
                    d = self._sig_defs(s)
                    if d:
                        F(f"No component requires {s} in the approved documents.", d[0]["prov"], s)
                return "signal_consumers", facts
            for d in self._sig_defs(s):
                p = d["prov"]
                F(f"{s} ({p['doc_id']} v{p['version']}): data type {d['dtype']}, unit {d['unit']}, range {d['range']}, "
                  f"update period {d['period']}.", p, s)
            if len(self._sig_defs(s)) > 1 and len({(d['dtype'], d['unit']) for d in self._sig_defs(s)}) > 1:
                F(f"Warning: {s} is defined with conflicting data type or unit in different documents.", self._sig_defs(s)[0]["prov"], s)
            if facts:
                return "signal_info", facts
        if comps:
            cid = comps[0]
            defs = self._comp_defs(cid)
            ports = [p for p in self.inv["ports"] if p["component"] == cid]
            if re.search(r"\btest|verif|\bvalidat", ql):
                for d in defs:
                    t = ", ".join(d["tests"]) or "none defined"
                    F(f"{cid} is verified by: {t}.", d["prov"], cid)
                return "verification_lookup", facts
            if re.search(r"safety|asil", ql):
                for d in defs:
                    F(f"{cid} has safety level {d['level']}.", d["prov"], cid)
                return "safety_lookup", facts
            if re.search(r"timeout|period|cycle|timing|update rate", ql):
                sel = [p for p in ports if set(query_terms(p["name"] + " " + p["interface"])) & set(query_terms(question)) - {cid.lower()}]
                for p in (sel or ports):
                    F(f"{cid} port {p['name']} ({p['direction']}, interface {p['interface']}) has {p['timing_kind']} {p['timing_ms']} ms.", p["prov"], p["name"])
                if facts:
                    return "timing_lookup", facts
            if re.search(r"\bports?\b", ql):
                want = []
                if re.search(r"provid", ql): want.append("provided")
                if re.search(r"requir", ql): want.append("required")
                for p in [p for p in ports if not want or p["direction"] in want]:
                    F(f"{cid} {p['direction']} port {p['name']}: interface {p['interface']}, {p['timing_kind']} {p['timing_ms']} ms.", p["prov"], p["name"])
                return "port_lookup", facts
            if re.search(r"depend|input|needs?\b|relies|rely", ql):
                for d in defs:
                    F(f"{cid} depends on: {', '.join(d['depends_on']) or 'no other component'}.", d["prov"], cid)
                for p in [p for p in ports if p["direction"] == "required"]:
                    iface = self.g.interfaces.get(p["interface"])
                    for se in (iface["data_elements"] if iface else []):
                        prod = self.g.producers(se)
                        F(f"{cid} requires {se} via {p['name']}; produced by {', '.join(prod) or 'no known component'}.", p["prov"], se)
                return "dependency_lookup", facts
            field = None
            if re.search(r"error|fault|fail|loss of|handl", ql): field = ("error_handling", "Error handling")
            elif re.search(r"rationale|\bwhy\b", ql): field = ("rationale", "Design rationale")
            elif re.search(r"runnable|task", ql): field = ("runnables", "Runnables")
            elif re.search(r"\btype\b|kind of", ql): field = ("type", "Type")
            elif re.search(r"what does|purpose|responsib|describe|\bdo\b|function|role|what is", ql): field = ("description", "Description")
            if field:
                for d in defs:
                    val = d[field[0]]
                    val = ", ".join(val) if isinstance(val, list) else val
                    if val:
                        F(f"{cid} - {field[1]}: {val}", d["prov"], cid)
                if facts:
                    return "component_field", facts
        if ifs and not comps:
            for i in self.inv["interfaces"]:
                if i["id"] == ifs[0]:
                    F(f"{i['id']} ({i['paradigm']}) carries {', '.join(i['data_elements'])}. {i['description']}", i["prov"], i["id"])
            if facts:
                return "interface_lookup", facts
        return None, []

    # ----------------------------------------------------------------- #
    def _chunk_for(self, prov: dict, key: str | None):
        cands = [i for i, c in enumerate(self.index.chunks)
                 if c["doc_id"] == prov["doc_id"] and c["version"] == prov["version"] and c["sec_id"] == prov["sec_id"]]
        if not cands:
            return None
        if key:
            for i in cands:
                if key in self.index.chunks[i]["text"]:
                    return i
        return cands[0]

    def _section_answer(self, hits, q_terms):
        """If a retrieved section title is fully contained in the question, return the section's lines."""
        qs = set(q_terms)
        for h in hits:
            c = self.index.chunks[h["idx"]]
            tt = set(tokens(c["sec_title"]))
            if len(tt) >= 2 and tt <= qs:
                return h["idx"]
        return None

    def _best_sentences(self, idxs, q_terms, limit=3):
        qs = set(q_terms)
        scored = []
        for rank, i in enumerate(idxs):
            c = self.index.chunks[i]
            for line in logical_lines(c["text"]):
                if line.startswith("- ") or "|" in line[:30] and line.startswith("SIG_"):
                    continue
                body = KEY_PREFIX.sub("", line)
                if len(body) < 25 or re.match(r"^(Component|Interface|Paradigm|Data Elements|Provided Ports|Required Ports):", line):
                    continue
                st = set(tokens(body))
                if not st:
                    continue
                overlap = sum(self.index.idf.get(t, 1.0) for t in st & qs)
                scored.append((overlap / (len(st) ** 0.35), -rank, i, body))
        scored.sort(reverse=True)
        picked, used = [], set()
        for sc, _, i, body in scored:
            if sc <= 0 or body in used:
                continue
            used.add(body)
            picked.append((i, body))
            if len(picked) >= limit:
                break
        return picked

    # ----------------------------------------------------------------- #
    def ask(self, question: str) -> dict:
        t0 = time.time()
        question = question.strip()
        ents, unknown = self._entities(question)
        res = self.index.search(question)
        hits = res["hits"]
        tool, facts = self._tool(question, ents) if ents else (None, [])
        evidence: list[dict] = []
        tag_of: dict[int, str] = {}

        def tag(chunk_idx):
            if chunk_idx is None:
                return None
            if chunk_idx not in tag_of:
                tag_of[chunk_idx] = f"S{len(tag_of) + 1}"
                c = self.index.chunks[chunk_idx]
                evidence.append(dict(tag=tag_of[chunk_idx], doc_id=c["doc_id"], version=c["version"], title=c.get("doc_title", ""),
                                     page=c["page"], sec_id=c["sec_id"], sec_title=c["sec_title"], text=c["text"]))
            return tag_of[chunk_idx]

        def done(answer, refused, conf, label, mode, extra=None, lines=None):
            out = dict(question=question, answer=answer, refused=refused, confidence=round(conf, 3), label=label,
                       citations=evidence, mode=mode, tool=tool, lines=lines or [],
                       latency_ms=int((time.time() - t0) * 1000))
            out["groundedness"] = self._groundedness(answer, evidence, facts) if not refused else None
            if extra:
                out.update(extra)
            return out

        if unknown:
            msg = (f"{self.refusal} The identifier(s) {', '.join(unknown)} do not exist in the approved documents of "
                   f"project {self.project}.")
            return done(msg, True, 0.0, "No evidence", "refusal")
        if not facts and not ents:
            if res["vocab_cov"] < 0.5 or not hits:
                return done(self.refusal, True, 0.0, "No evidence", "refusal")

        # --------- build lines (answer statements with their evidence tags) ---------
        lines: list[dict] = []
        if facts:
            for f in facts:
                t = tag(self._chunk_for(f["prov"], f.get("key")))
                lines.append(dict(text=f["text"], tags=[t] if t else []))
            for h in hits[:2]:  # supporting passages
                tag(h["idx"])
            conf = 0.92
        else:
            top = [h["idx"] for h in hits]
            sec_idx = self._section_answer(hits, res["terms"])
            if sec_idx is not None:
                c = self.index.chunks[sec_idx]
                t = tag(sec_idx)
                for ln in logical_lines(c["text"])[:8]:
                    lines.append(dict(text=ln, tags=[t]))
            else:
                for i, body in self._best_sentences(top[:4], res["terms"]):
                    c = self.index.chunks[i]
                    prefix = f"{c['sec_title']}: " if re.match(r"^(SWC|SR|SIG|CDD)_", c["sec_title"]) else ""
                    lines.append(dict(text=prefix + body, tags=[tag(i)]))
            for h in hits[:3]:
                tag(h["idx"])
            conf = (0.5 * res["coverage"] + 0.25 * min(1.0, res["bm25_abs"] / 0.5) + 0.25 * min(1.0, res["dense_abs"] / 0.45))
            if not lines or conf < self.cfg["retrieval"]["min_confidence"]:
                evidence.clear()
                return done(self.refusal, True, conf, "Low confidence", "refusal")
        label = "High" if conf >= self.cfg["retrieval"]["high_confidence"] else "Medium"
        answer = "\n".join(f"{l['text']} " + "".join(f"[{t}]" for t in l["tags"]) for l in lines)
        mode = "extractive"
        if self.cfg["llm"]["provider"] == "ollama":
            try:
                ev_txt = "\n".join(f"[{e['tag']}] ({e['doc_id']} v{e['version']} section {e['sec_id']}, page {e['page']})\n{e['text']}" for e in evidence)
                facts_txt = "\n".join(f"- {l['text']}" for l in lines) if facts else "(none)"
                user = self.prompts["user_template"].format(question=question, facts=facts_txt, evidence=ev_txt)
                gen = ollama_generate(self.cfg, self.prompts["system_grounded_answer"], user)
                if validate_citations(gen, {e["tag"] for e in evidence}):
                    answer, mode = gen, "ollama:" + self.cfg["llm"]["ollama_model"]
                else:
                    mode = "extractive (LLM output failed citation validation)"
            except Exception as exc:
                mode = f"extractive (LLM unavailable: {exc.__class__.__name__})"
        return done(answer, False, conf, label, mode, lines=lines)

    @staticmethod
    def _groundedness(answer: str, evidence: list[dict], facts: list[dict]) -> float:
        support = set(tokens(" ".join(e["text"] + " " + e["sec_title"] for e in evidence) + " " + " ".join(f["text"] for f in facts)))
        sents = [s for s in sentences(re.sub(r"\[S\d+\]", "", answer)) if len(s) > 15]
        if not sents:
            return 1.0
        ok = 0
        for s in sents:
            toks = set(tokens(s))
            ok += 1 if not toks or len(toks & support) / len(toks) >= 0.7 else 0
        return round(ok / len(sents), 3)
