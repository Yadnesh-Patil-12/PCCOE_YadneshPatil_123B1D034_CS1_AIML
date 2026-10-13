"""Configuration loading (JSON file + environment overrides)."""
from __future__ import annotations

import json
import os
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = CODE_DIR.parent
CONFIG_PATH = PROJECT_DIR / "Model_Prompts_Config" / "config.json"
PROMPTS_PATH = PROJECT_DIR / "Model_Prompts_Config" / "prompts.json"
INPUT_DIR = PROJECT_DIR / "Input_Data"
EVAL_DIR = PROJECT_DIR / "Evaluation_Results"
DATA_DIR = Path(os.environ.get("HLD_DATA_DIR", CODE_DIR / "data"))
WEB_DIR = CODE_DIR / "web"


def _coerce(old, raw: str):
    if isinstance(old, bool):
        return raw.lower() in ("1", "true", "yes")
    if isinstance(old, int):
        return int(raw)
    if isinstance(old, float):
        return float(raw)
    return raw


def load_config() -> dict:
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    for section, values in cfg.items():
        if not isinstance(values, dict):
            continue
        for key, old in values.items():
            env = os.environ.get(f"HLD_{section.upper()}_{key.upper()}")
            if env is not None:
                values[key] = _coerce(old, env)
    return cfg


def load_prompts() -> dict:
    return json.loads(PROMPTS_PATH.read_text(encoding="utf-8"))
