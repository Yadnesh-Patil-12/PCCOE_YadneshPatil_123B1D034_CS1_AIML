"""Draws the architecture and workflow diagrams used in the report (matplotlib)."""
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

OUT = Path(__file__).resolve().parents[2] / "Documentation" / "diagrams"
OUT.mkdir(parents=True, exist_ok=True)
INK, BLUE, SOFT, LINE, VIOLET, GREEN, AMBER = "#12202F", "#1646C2", "#E4ECFC", "#9FB0C3", "#5B3FB4", "#2B7A59", "#C97700"


def box(ax, x, y, w, h, title, sub="", fc="white", ec=INK, tc=INK, fs=10):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.08", fc=fc, ec=ec, lw=1.4))
    if sub:
        ax.text(x + w / 2, y + h * 0.64, title, ha="center", va="center", fontsize=fs, fontweight="bold", color=tc)
        ax.text(x + w / 2, y + h * 0.28, sub, ha="center", va="center", fontsize=fs - 2.2, color=tc, alpha=.85)
    else:
        ax.text(x + w / 2, y + h / 2, title, ha="center", va="center", fontsize=fs, fontweight="bold", color=tc)


def arrow(ax, p, q, color=INK, style="-|>", ls="-", rad=0.0):
    ax.add_patch(FancyArrowPatch(p, q, arrowstyle=style, mutation_scale=13, lw=1.4, color=color, ls=ls, connectionstyle=f"arc3,rad={rad}"))


def architecture():
    fig, ax = plt.subplots(figsize=(12, 7.4)); ax.set_xlim(0, 12); ax.set_ylim(0, 7.4); ax.axis("off")
    ax.text(6, 7.15, "System architecture - HLD Assistant (fully local)", ha="center", fontsize=14, fontweight="bold", color=INK)
    box(ax, 3.6, 6.2, 4.8, 0.62, "Engineering users", "architect  |  tester  |  viewer", fc=SOFT, ec=BLUE)
    box(ax, 2.2, 5.15, 7.6, 0.72, "Web UI (HTML + CSS + JavaScript, no external libraries)",
        "Ask  |  Architecture + dependency map  |  Checks  |  Compare  |  Documents  |  Evaluation  |  Audit", fc="white", ec=BLUE)
    box(ax, 2.2, 4.1, 7.6, 0.72, "HTTP API service (Python standard library)", "login + roles (RBAC)  |  project isolation  |  input validation  |  audit log", fc="white", ec=BLUE)
    # application layer
    ax.add_patch(FancyBboxPatch((0.3, 1.55), 8.0, 2.2, boxstyle="round,pad=0.02,rounding_size=0.1", fc="#F7F9FC", ec=LINE, lw=1.2, ls="--"))
    ax.text(0.5, 3.52, "Application services", fontsize=9.5, color=INK, fontweight="bold")
    box(ax, 0.5, 2.6, 1.8, 0.75, "Ingestion", "PDF / MD / TXT\nsections, chunks", fs=9.5)
    box(ax, 2.5, 2.6, 1.8, 0.75, "Extraction", "components, ports,\ninterfaces, signals", fs=9.5)
    box(ax, 4.5, 2.6, 1.8, 0.75, "Checks + Compare", "11 rules, revision\ndiff, impact", fs=9.5)
    box(ax, 6.5, 2.6, 1.6, 0.75, "Export", "CSV / JSON", fs=9.5)
    box(ax, 0.5, 1.7, 7.6, 0.72, "QA engine: structured tools + hybrid retrieval + guardrails",
        "BM25 + vectors  |  evidence tags [S1]..  |  confidence  |  refusal when unsupported", fc=SOFT, ec=BLUE, fs=9.5)
    # models/stores
    box(ax, 8.7, 3.1, 3.0, 0.7, "Local LLM (optional)", "Ollama: llama3.2 / qwen\ndefault: extractive, no LLM", ec=VIOLET, tc=VIOLET, fs=9.5)
    box(ax, 8.7, 2.2, 3.0, 0.7, "Embedding model", "hashing TF-IDF (offline)\noptional BGE-small", ec=VIOLET, tc=VIOLET, fs=9.5)
    box(ax, 8.7, 1.3, 3.0, 0.7, "Vector store", "NumPy exact search\noptional FAISS", ec=VIOLET, tc=VIOLET, fs=9.5)
    box(ax, 0.5, 0.25, 4.6, 0.8, "SQLite", "users, documents, chunks, findings,\nanswers, audit trail", ec=GREEN, tc=GREEN, fs=9.5)
    box(ax, 5.4, 0.25, 3.0, 0.8, "Approved documents", "Input_Data (synthetic HLD PDFs)", ec=GREEN, tc=GREEN, fs=9.5)
    box(ax, 8.7, 0.25, 3.0, 0.8, "Security boundary", "no cloud calls; localhost only", fc="#FFF1DB", ec=AMBER, tc=AMBER, fs=9.5)
    for a, b in [((6, 6.2), (6, 5.87)), ((6, 5.15), (6, 4.82)), ((6, 4.1), (6, 3.78))]:
        arrow(ax, a, b)
    arrow(ax, (8.1, 2.05), (8.7, 1.65), VIOLET); arrow(ax, (8.1, 2.15), (8.7, 2.55), VIOLET); arrow(ax, (8.1, 2.3), (8.7, 3.4), VIOLET, ls="--")
    arrow(ax, (2.8, 1.7), (2.8, 1.05), GREEN); arrow(ax, (5.4, 1.4), (5.9, 1.05), GREEN)
    fig.savefig(OUT / "architecture.png", dpi=170, bbox_inches="tight", facecolor="white"); plt.close(fig)


def workflow():
    fig, ax = plt.subplots(figsize=(12, 6.2)); ax.set_xlim(0, 12); ax.set_ylim(0, 6.2); ax.axis("off")
    ax.text(6, 5.95, "Workflow - indexing (top) and answering (bottom)", ha="center", fontsize=14, fontweight="bold", color=INK)
    top = [("1 Upload / seed", "approved PDF, MD, TXT"), ("2 Extract text", "per page, remove\nheaders and footers"), ("3 Sections + chunks", "by heading, page kept,\ninjection scan"),
           ("4 Index", "embeddings + BM25\nvector store"), ("5 Entities", "components, ports,\ninterfaces, signals"), ("6 Checks", "11 rules -> findings\nfor review")]
    for i, (t, s) in enumerate(top):
        x = 0.25 + i * 1.95
        box(ax, x, 4.3, 1.7, 1.0, t, s, fc=SOFT if i < 4 else "#EEE9FB", ec=BLUE if i < 4 else VIOLET, tc=INK, fs=9.5)
        if i: arrow(ax, (x - 0.25, 4.8), (x, 4.8))
    bot = [("1 Question", "user, project, role"), ("2 Guard", "unknown IDs and\nout-of-scope -> refuse"), ("3 Route", "structured tool or\nhybrid retrieval"),
           ("4 Evidence", "top passages + facts\nwith page and section"), ("5 Compose", "extractive answer or\nlocal LLM, citations checked"), ("6 Review", "confidence, grounding,\naccept / reject, audit")]
    for i, (t, s) in enumerate(bot):
        x = 0.25 + i * 1.95
        ec = AMBER if i == 1 else BLUE
        box(ax, x, 1.7, 1.7, 1.0, t, s, fc="#FFF1DB" if i == 1 else "white", ec=ec, fs=9.5)
        if i: arrow(ax, (x - 0.25, 2.2), (x, 2.2))
    ax.text(0.25, 3.55, "Indexing happens once per document (a few milliseconds for the demo set).", fontsize=9.5, color=INK)
    arrow(ax, (7.5, 4.3), (7.5, 2.7), VIOLET, ls="--"); ax.text(7.65, 3.35, "inventory and graph feed the tools", fontsize=9, color=VIOLET)
    ax.text(0.25, 0.9, "Rule: every statement carries a source tag; low confidence means no answer; engineers approve before use.", fontsize=10, color=INK, fontweight="bold")
    fig.savefig(OUT / "workflow.png", dpi=170, bbox_inches="tight", facecolor="white"); plt.close(fig)


if __name__ == "__main__":
    architecture(); workflow(); print("diagrams written to", OUT)
