from __future__ import annotations

import importlib


def test_load_saved_settings_ignores_corrupt_json(repo_modules):
    import ollama_client

    client = importlib.reload(ollama_client)
    client.SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    client.SETTINGS_PATH.write_text("{", encoding="utf-8")

    assert client._load_saved_settings() == {}


def test_model_list_cache_reused_during_runtime_refresh(repo_modules, monkeypatch):
    import ollama_client

    client = importlib.reload(ollama_client)
    calls: list[tuple[str, float]] = []

    def fake_get_json(path: str, *, timeout: float = 5.0) -> dict:
        calls.append((path, timeout))
        return {
            "models": [
                {"name": "qwen2.5:7b", "details": {"family": "qwen"}},
                {"name": "nomic-embed-text", "details": {"family": "bert"}},
            ]
        }

    monkeypatch.setattr(client, "_get_json", fake_get_json)

    client.init_runtime_settings(force_refresh=True)

    assert client.list_chat_models() == ["qwen2.5:7b"]
    assert client.list_embedding_models() == ["nomic-embed-text"]
    assert len(calls) == 1


def test_model_lists_do_not_cross_fill_missing_model_types(repo_modules, monkeypatch):
    import ollama_client

    client = importlib.reload(ollama_client)

    monkeypatch.setattr(
        client,
        "_get_json",
        lambda path, *, timeout=5.0: {"models": [{"name": "nomic-embed-text:latest", "details": {"family": "bert"}}]},
    )

    client.init_runtime_settings(force_refresh=True)

    assert client.list_chat_models() == []
    assert client.list_embedding_models() == ["nomic-embed-text:latest"]


def test_generate_and_embedding_requests_keep_models_alive(repo_modules, monkeypatch):
    import ollama_client

    client = importlib.reload(ollama_client)
    captured: list[dict[str, object]] = []

    def fake_post_json(path: str, payload: dict, *, timeout: float) -> dict:
        captured.append({"path": path, "payload": dict(payload), "timeout": timeout})
        return {"response": "ok", "embedding": [1.0]}

    monkeypatch.setattr(client, "_post_json", fake_post_json)
    monkeypatch.setattr(client.launcher_core, "OLLAMA_MODEL_KEEP_ALIVE", "-1")

    client.embed_text("hello")
    client.generate_text("prompt")

    assert captured[0]["path"] == "/api/embeddings"
    assert captured[0]["payload"]["keep_alive"] == "-1"
    assert captured[1]["path"] == "/api/generate"
    assert captured[1]["payload"]["keep_alive"] == "-1"
