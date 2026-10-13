"""Document ingestion: text extraction -> boilerplate removal -> sections -> context-aware chunks."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

SECTION_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})\s+([A-Z][^|\n]{1,100})$")
UNIT_START = re.compile(r"^(ms|km|mph|kb|mb|percent|degc|hz|a|v)\b", re.I)


@dataclass
class Section:
    sec_id: str
    title: str
    lines: list[tuple[int, str]] = field(default_factory=list)  # (page, text)

    @property
    def page_start(self) -> int:
        return self.lines[0][0] if self.lines else 1


@dataclass
class Chunk:
    page: int
    sec_id: str
    sec_title: str
    text: str
    pages: list[int] = field(default_factory=list)


def read_pages(path: Path) -> list[tuple[int, str]]:
    ext = path.suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        return [(i + 1, (p.extract_text() or "")) for i, p in enumerate(reader.pages)]
    if ext in (".md", ".txt"):
        return [(1, path.read_text(encoding="utf-8", errors="replace"))]
    raise ValueError(f"Unsupported file type: {ext}")


def strip_boilerplate(pages: list[tuple[int, str]]) -> list[tuple[int, list[str]]]:
    """Remove running headers/footers: only the first 2 / last 3 non-empty lines of a page are candidates,
    and only if the same line (digits normalised) repeats on >=50% of pages."""
    norm = lambda s: re.sub(r"\d+", "#", s.strip())
    page_lines = [(n, [l.rstrip() for l in t.splitlines() if l.strip()]) for n, t in pages]

    def edges(lines):
        return lines[:2] + lines[-3:] if len(lines) > 5 else lines

    repeated: set[str] = set()
    if len(page_lines) >= 3:
        counts: dict[str, int] = {}
        for _, lines in page_lines:
            for k in {norm(l) for l in edges(lines)}:
                counts[k] = counts.get(k, 0) + 1
        repeated = {k for k, c in counts.items() if c >= max(2, 0.5 * len(page_lines))}
    out = []
    for n, lines in page_lines:
        cand = set(map(id, edges(lines)))
        kept = [l for l in lines if not (id(l) in cand and norm(l) in repeated)]
        out.append((n, kept))
    return out


def split_sections(pages: list[tuple[int, list[str]]], default_title: str) -> list[Section]:
    sections: list[Section] = [Section("0", default_title)]
    for page, lines in pages:
        for line in lines:
            s = line.strip()
            m = SECTION_RE.match(s)
            if m and not UNIT_START.match(m.group(2)) and not s.endswith("."):
                sections.append(Section(m.group(1), m.group(2).strip()))
                continue
            sections[-1].lines.append((page, s))
    return [s for s in sections if s.lines]


def make_chunks(sections: list[Section], max_chars: int = 900, overlap_lines: int = 1) -> list[Chunk]:
    chunks: list[Chunk] = []
    for sec in sections:
        buf: list[tuple[int, str]] = []
        size = 0
        def flush():
            if buf:
                text = "\n".join(l for _, l in buf)
                chunks.append(Chunk(buf[0][0], sec.sec_id, sec.title, text, sorted({p for p, _ in buf})))
        for page, line in sec.lines:
            if size + len(line) > max_chars and buf:
                flush()
                buf = buf[-overlap_lines:] if overlap_lines else []
                size = sum(len(l) for _, l in buf)
            buf.append((page, line))
            size += len(line) + 1
        flush()
    return chunks


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def injection_suspect(text: str, patterns: list[str]) -> bool:
    low = text.lower()
    return any(re.search(p, low) for p in patterns)


def ingest_file(path: Path, title: str, cfg: dict, patterns: list[str]):
    """Returns (sections, chunks, page_count). Chunks carry an `injection` attribute."""
    raw = read_pages(path)
    pages = strip_boilerplate(raw)
    sections = split_sections(pages, title)
    chunks = make_chunks(sections, cfg["chunking"]["max_chars"], cfg["chunking"]["overlap_lines"])
    for c in chunks:
        c.injection = injection_suspect(c.text, patterns)  # type: ignore[attr-defined]
    return sections, chunks, len(raw)
