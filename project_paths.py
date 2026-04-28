from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


def _path_from_env(env_name: str, default: Path) -> Path:
    raw_value = os.getenv(env_name)
    if not raw_value:
        return default
    candidate = Path(raw_value)
    if not candidate.is_absolute():
        candidate = ROOT_DIR / candidate
    return candidate


DATA_DIR = _path_from_env("PRIVATE_NOTE_DATA_DIR", ROOT_DIR / "data")
RUNTIME_DIR = _path_from_env("PRIVATE_NOTE_RUNTIME_DIR", ROOT_DIR / "runtime")
VENDOR_DIR = _path_from_env("PRIVATE_NOTE_VENDOR_DIR", ROOT_DIR / "vendor")

LOG_DIR = DATA_DIR / "logs"
RUN_DIR = DATA_DIR / "run"

SQLITE_PATH = _path_from_env("SQLITE_PATH", DATA_DIR / "notes.db")
CHROMA_PATH = _path_from_env("CHROMA_PATH", DATA_DIR / "chroma")
APP_SETTINGS_PATH = _path_from_env("APP_SETTINGS_PATH", DATA_DIR / "app_settings.json")
DESKTOP_STATE_PATH = _path_from_env("DESKTOP_STATE_PATH", DATA_DIR / "desktop_state.json")
PRIVATE_OLLAMA_MODELS_DIR = _path_from_env("PRIVATE_NOTE_OLLAMA_MODELS_DIR", DATA_DIR / "ollama-models")
