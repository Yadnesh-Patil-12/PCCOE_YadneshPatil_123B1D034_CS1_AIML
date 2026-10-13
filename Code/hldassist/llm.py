"""Answer generators. 'extractive' is deterministic and cannot hallucinate; 'ollama' calls a LOCAL model."""
from __future__ import annotations

import json
import re
import urllib.request


def ollama_generate(cfg: dict, system: str, user: str) -> str:
    body = json.dumps({
        "model": cfg["llm"]["ollama_model"], "stream": False,
        "options": {"temperature": cfg["llm"]["temperature"]},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(cfg["llm"]["ollama_url"].rstrip("/") + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=cfg["llm"]["timeout_s"]) as r:
        return json.loads(r.read().decode())["message"]["content"].strip()


def validate_citations(answer: str, valid_tags: set[str]) -> bool:
    tags = set(re.findall(r"\[(S\d+)\]", answer))
    return bool(tags) and tags <= valid_tags and "INSUFFICIENT EVIDENCE" not in answer.upper()
