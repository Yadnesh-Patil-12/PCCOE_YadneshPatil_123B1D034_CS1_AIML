"""Tokenisation helpers shared by BM25, the hashing embedder and the QA layer."""
from __future__ import annotations

import re

STOPWORDS = set("""a an the of to in on for and or is are was were be been being by with from as at it its this that these
those which what who whom whose how when where why do does did can could should would will shall may might must not no
if then than so such there their them they we you your i me my our us his her he she about into over under between
within without per via also any all each every both either neither some more most other another up down out off again
once only own same too very just because while during before after above below s t""".split())

TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:[_\-\.][A-Za-z0-9]+)*")
CAMEL_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")


def stem(tok: str) -> str:
    for suf in ("ations", "ation", "ings", "ing", "ers", "er", "ed", "es", "s"):
        if tok.endswith(suf) and len(tok) - len(suf) >= 4:
            return tok[: -len(suf)]
    return tok


def split_identifier(tok: str) -> list[str]:    
    parts: list[str] = []
    for chunk in re.split(r"[_\-]", tok):
        parts.extend(CAMEL_RE.findall(chunk))
    return [p.lower() for p in parts if p]


def tokens(text: str, keep_stop: bool = False) -> list[str]:
    """Return normalised tokens. Identifiers such as SWC_DoorLockCtrl yield the full lower-cased
    token plus its camel/underscore parts so that 'door lock' also matches the identifier."""
    out: list[str] = []
    for m in TOKEN_RE.finditer(text):
        raw = m.group(0)
        low = raw.lower()
        is_ident = ("_" in raw) or bool(re.search(r"[a-z][A-Z]", raw))
        if is_ident:
            out.append(low)
            for p in split_identifier(raw):
                if p not in STOPWORDS and len(p) > 1:
                    out.append(stem(p))
        else:
            if not keep_stop and low in STOPWORDS:
                continue
            out.append(stem(low))
    return out


def query_terms(text: str) -> list[str]:
    seen, out = set(), []
    for t in tokens(text):
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip() for p in parts if p and p.strip()]


_NEW_LINE = re.compile(r"^(?:[A-Z][A-Za-z ]{2,24}:|- |Step \d|SIG_\w+\s*\||Revision )")


def logical_lines(text: str) -> list[str]:
    """Re-join lines that were wrapped by the PDF layout. A new logical line starts at a 'Key:' field, a bullet,
    a flow step, or after a line that ended a sentence."""
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if out and not _NEW_LINE.match(line) and not out[-1].endswith((".", "!", "?")):
            out[-1] += " " + line
        else:
            out.append(line)
    return out
