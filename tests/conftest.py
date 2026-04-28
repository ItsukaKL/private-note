from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def repo_modules(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setenv("PRIVATE_NOTE_DATA_DIR", str(data_dir))
    monkeypatch.setenv("PRIVATE_NOTE_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.setenv("SQLITE_PATH", str(data_dir / "notes.db"))
    monkeypatch.setenv("CHROMA_PATH", str(data_dir / "chroma"))
    monkeypatch.setenv("APP_SETTINGS_PATH", str(data_dir / "app_settings.json"))
    monkeypatch.setenv("DESKTOP_STATE_PATH", str(data_dir / "desktop_state.json"))
    monkeypatch.setenv("PRIVATE_NOTE_OLLAMA_MODELS_DIR", str(data_dir / "ollama-models"))

    import project_paths
    import chroma_store
    import db
    import note_service

    project_paths = importlib.reload(project_paths)
    db = importlib.reload(db)
    chroma_store = importlib.reload(chroma_store)
    note_service = importlib.reload(note_service)

    db.init_db()

    return SimpleNamespace(
        db=db,
        chroma_store=chroma_store,
        note_service=note_service,
        tmp_path=tmp_path,
    )
