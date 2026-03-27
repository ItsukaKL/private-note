import json
import os
import urllib.request

import launcher_core

DEFAULT_OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
DEFAULT_OLLAMA_LLM_MODEL = os.getenv("OLLAMA_LLM_MODEL", "llama2:7b")
SETTINGS_PATH = os.getenv("APP_SETTINGS_PATH", "data/app_settings.json")

_runtime_settings = {
    "llm_model": DEFAULT_OLLAMA_LLM_MODEL,
    "embed_model": DEFAULT_OLLAMA_EMBED_MODEL,
}


def _ensure_settings_dir() -> None:
    directory = os.path.dirname(SETTINGS_PATH)
    if directory:
        os.makedirs(directory, exist_ok=True)


def _get_json(path: str) -> dict:
    url = f"{launcher_core.get_ollama_base_url()}{path}"
    request = urllib.request.Request(url, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json(path: str, payload: dict) -> dict:
    url = f"{launcher_core.get_ollama_base_url()}{path}"
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read().decode("utf-8"))


def _load_saved_settings() -> dict:
    if not os.path.exists(SETTINGS_PATH):
        return {}
    with open(SETTINGS_PATH, "r", encoding="utf-8") as file:
        return json.load(file)


def _save_settings() -> None:
    _ensure_settings_dir()
    with open(SETTINGS_PATH, "w", encoding="utf-8") as file:
        json.dump({"llm_model": _runtime_settings["llm_model"]}, file, ensure_ascii=False, indent=2)


def _is_embedding_model(model_info: dict) -> bool:
    name = str(model_info.get("name", "")).lower()
    details = model_info.get("details") or {}
    family = str(details.get("family", "")).lower()
    families = details.get("families") or []
    family_text = " ".join(str(item).lower() for item in families)
    haystack = f"{name} {family} {family_text}"
    return "embed" in haystack or "bert" in haystack


def init_runtime_settings() -> None:
    saved = _load_saved_settings()
    saved_model = saved.get("llm_model")
    if saved_model:
        _runtime_settings["llm_model"] = saved_model

    available_models = list_chat_models()
    if not available_models:
        return
    if _runtime_settings["llm_model"] in available_models:
        return
    if DEFAULT_OLLAMA_LLM_MODEL in available_models:
        _runtime_settings["llm_model"] = DEFAULT_OLLAMA_LLM_MODEL
    else:
        _runtime_settings["llm_model"] = available_models[0]
    _save_settings()


def get_llm_model() -> str:
    return _runtime_settings["llm_model"]


def list_chat_models() -> list[str]:
    response = _get_json("/api/tags")
    models = response.get("models") or []
    if not models:
        return []

    chat_models = [model.get("name", "") for model in models if model.get("name") and not _is_embedding_model(model)]
    if chat_models:
        return chat_models
    return [model.get("name", "") for model in models if model.get("name")]


def set_llm_model(model_name: str) -> str:
    available_models = list_chat_models()
    if model_name not in available_models:
        raise ValueError(f"Model '{model_name}' is not available")
    _runtime_settings["llm_model"] = model_name
    _save_settings()
    return model_name


def embed_text(text: str) -> list[float]:
    payload = {"model": _runtime_settings["embed_model"], "prompt": text}
    response = _post_json("/api/embeddings", payload)
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
        "options": {
            "temperature": 0,
        },
    }
    response = _post_json("/api/generate", payload)
    if "response" in response:
        return response["response"]
    raise RuntimeError("Generate response missing response field")
