import json
import os
import time
import urllib.error
import urllib.request

import launcher_core
from project_paths import APP_SETTINGS_PATH

DEFAULT_OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", launcher_core.DEFAULT_EMBED_MODEL)
DEFAULT_OLLAMA_LLM_MODEL = os.getenv("OLLAMA_LLM_MODEL", launcher_core.DEFAULT_LLM_MODEL)
SETTINGS_PATH = APP_SETTINGS_PATH

_runtime_settings = {
    "llm_model": DEFAULT_OLLAMA_LLM_MODEL,
    "embed_model": DEFAULT_OLLAMA_EMBED_MODEL,
}
_MODEL_LIST_CACHE_TTL_SECONDS = 2.0
_model_list_cache: dict[str, object] = {
    "base_url": "",
    "timestamp": 0.0,
    "models": [],
}


def _ensure_settings_dir() -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)


def _request_json(request: urllib.request.Request, *, timeout: float, error_context: str) -> dict:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore").strip()
        raise RuntimeError(detail or error_context) from exc
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(error_context) from exc


def _get_json(path: str, *, timeout: float = 5.0) -> dict:
    url = f"{launcher_core.get_ollama_base_url()}{path}"
    request = urllib.request.Request(url, headers={"Content-Type": "application/json"})
    return _request_json(request, timeout=timeout, error_context=f"Ollama GET failed: {path}")


def _post_json(path: str, payload: dict, *, timeout: float) -> dict:
    url = f"{launcher_core.get_ollama_base_url()}{path}"
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    return _request_json(request, timeout=timeout, error_context=f"Ollama POST failed: {path}")


def _load_saved_settings() -> dict:
    if not SETTINGS_PATH.exists():
        return {}
    try:
        with SETTINGS_PATH.open("r", encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_settings() -> None:
    _ensure_settings_dir()
    payload = _load_saved_settings()
    if not isinstance(payload, dict):
        payload = {}
    payload.update(
        {
            "llm_model": _runtime_settings["llm_model"],
            "embed_model": _runtime_settings["embed_model"],
        }
    )
    launcher_core.atomic_write_json(SETTINGS_PATH, payload)


def _is_embedding_model(model_info: dict) -> bool:
    name = str(model_info.get("name", "")).lower()
    details = model_info.get("details") or {}
    family = str(details.get("family", "")).lower()
    families = details.get("families") or []
    family_text = " ".join(str(item).lower() for item in families)
    haystack = f"{name} {family} {family_text}"
    return "embed" in haystack or "bert" in haystack


def _partition_model_names(models: list[dict]) -> tuple[list[str], list[str]]:
    chat_models = [str(model.get("name") or "") for model in models if model.get("name") and not _is_embedding_model(model)]
    embed_models = [str(model.get("name") or "") for model in models if model.get("name") and _is_embedding_model(model)]
    return chat_models, embed_models


def init_runtime_settings(*, force_refresh: bool = False) -> None:
    saved = _load_saved_settings()
    saved_llm_model = saved.get("llm_model")
    saved_embed_model = saved.get("embed_model")
    if saved_llm_model:
        _runtime_settings["llm_model"] = saved_llm_model
    if saved_embed_model:
        _runtime_settings["embed_model"] = saved_embed_model

    changed = False

    models = _list_models(force_refresh=force_refresh)
    available_chat_models, available_embedding_models = _partition_model_names(models)
    if available_chat_models and _runtime_settings["llm_model"] not in available_chat_models:
        if DEFAULT_OLLAMA_LLM_MODEL in available_chat_models:
            _runtime_settings["llm_model"] = DEFAULT_OLLAMA_LLM_MODEL
        else:
            _runtime_settings["llm_model"] = available_chat_models[0]
        changed = True

    if available_embedding_models and _runtime_settings["embed_model"] not in available_embedding_models:
        if DEFAULT_OLLAMA_EMBED_MODEL in available_embedding_models:
            _runtime_settings["embed_model"] = DEFAULT_OLLAMA_EMBED_MODEL
        else:
            _runtime_settings["embed_model"] = available_embedding_models[0]
        changed = True

    if changed:
        _save_settings()


def get_llm_model() -> str:
    return _runtime_settings["llm_model"]


def get_embed_model() -> str:
    return _runtime_settings["embed_model"]


def _list_models(*, force_refresh: bool = False) -> list[dict]:
    base_url = launcher_core.get_ollama_base_url()
    now = time.monotonic()
    cached_base_url = str(_model_list_cache.get("base_url") or "")
    cached_timestamp = float(_model_list_cache.get("timestamp") or 0.0)
    cached_models = _model_list_cache.get("models") or []
    if (
        not force_refresh
        and cached_base_url == base_url
        and now - cached_timestamp < _MODEL_LIST_CACHE_TTL_SECONDS
        and isinstance(cached_models, list)
    ):
        return [dict(item) for item in cached_models if isinstance(item, dict)]

    response = _get_json("/api/tags", timeout=5.0)
    models = [item for item in list(response.get("models") or []) if isinstance(item, dict)]
    _model_list_cache.update(
        {
            "base_url": base_url,
            "timestamp": now,
            "models": [dict(item) for item in models],
        }
    )
    return models


def list_chat_models() -> list[str]:
    models = _list_models()
    chat_models, _ = _partition_model_names(models)
    return chat_models


def list_embedding_models() -> list[str]:
    models = _list_models()
    _, embedding_models = _partition_model_names(models)
    return embedding_models


def set_llm_model(model_name: str) -> str:
    available_models = list_chat_models()
    if model_name not in available_models:
        raise ValueError(f"Model '{model_name}' is not available")
    _runtime_settings["llm_model"] = model_name
    _save_settings()
    return model_name


def set_embed_model(model_name: str) -> str:
    available_models = list_embedding_models()
    if model_name not in available_models:
        raise ValueError(f"Embedding model '{model_name}' is not available")
    _runtime_settings["embed_model"] = model_name
    _save_settings()
    return model_name


def embed_text(text: str) -> list[float]:
    payload = {"model": _runtime_settings["embed_model"], "prompt": text, "keep_alive": launcher_core.OLLAMA_MODEL_KEEP_ALIVE}
    response = _post_json("/api/embeddings", payload, timeout=120.0)
    if "embedding" in response:
        return response["embedding"]
    if "data" in response and response["data"]:
        return response["data"][0]["embedding"]
    raise RuntimeError("Embedding response missing embedding field")


def generate_text(prompt: str) -> str:
    payload = {
        "model": get_llm_model(),
        "prompt": prompt,
        "stream": False,
        "keep_alive": launcher_core.OLLAMA_MODEL_KEEP_ALIVE,
        "options": {
            "temperature": 0,
        },
    }
    response = _post_json("/api/generate", payload, timeout=600.0)
    if "response" in response:
        return response["response"]
    raise RuntimeError("Generate response missing response field")
