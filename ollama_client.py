import json
import os
import urllib.request


OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
OLLAMA_LLM_MODEL = os.getenv("OLLAMA_LLM_MODEL", "llama2:7b")


def _post_json(path: str, payload: dict) -> dict:
    url = f"{OLLAMA_BASE_URL}{path}"
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read().decode("utf-8"))


def embed_text(text: str) -> list[float]:
    payload = {"model": OLLAMA_EMBED_MODEL, "prompt": text}
    response = _post_json("/api/embeddings", payload)
    if "embedding" in response:
        return response["embedding"]
    if "data" in response and response["data"]:
        return response["data"][0]["embedding"]
    raise RuntimeError("Embedding response missing embedding field")


def generate_text(prompt: str) -> str:
    payload = {"model": OLLAMA_LLM_MODEL, "prompt": prompt, "stream": False}
    response = _post_json("/api/generate", payload)
    if "response" in response:
        return response["response"]
    raise RuntimeError("Generate response missing response field")
