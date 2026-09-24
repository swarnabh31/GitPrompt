"""Thin, dependable client for a locally running Ollama instance."""

import os

import requests

DEFAULT_HOST = "http://localhost:11434"

# Best-effort native context lengths for common Ollama model families.
# Users can always override in the UI; unknown models fall back to 8192.
MODEL_CONTEXT_HINTS = [
    ("llama3.3", 131072), ("llama3.2", 131072), ("llama3.1", 131072),
    ("llama3", 8192), ("qwen3", 40960), ("qwen2.5", 32768),
    ("qwen2", 32768), ("mistral", 32768), ("mixtral", 32768),
    ("gemma3", 131072), ("gemma2", 8192), ("phi4", 16384),
    ("phi3", 12800), ("deepseek-r1", 65536), ("deepseek-coder", 16384),
    ("codellama", 16384), ("codestral", 32768), ("command-r", 131072),
]
DEFAULT_NUM_CTX = 8192


class OllamaError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.message = message
        self.status = status


def base_url() -> str:
    return os.environ.get("OLLAMA_HOST", DEFAULT_HOST).rstrip("/")


def list_models() -> list[dict]:
    """Return locally installed models, sorted by name."""
    try:
        resp = requests.get(f"{base_url()}/api/tags", timeout=4)
    except requests.ConnectionError:
        raise OllamaError(
            "Ollama is not running. Install it from https://ollama.com, "
            "then start it (usually just `ollama serve` or the desktop app) "
            "and pull a model, e.g. `ollama pull llama3.2`.")
    except requests.RequestException:
        raise OllamaError("Couldn't reach Ollama. Is it still starting up?")
    if resp.status_code != 200:
        raise OllamaError(f"Ollama returned an error ({resp.status_code}).")
    models = []
    for m in resp.json().get("models", []):
        size_gb = round((m.get("size") or 0) / 1e9, 1)
        models.append({"name": m.get("name", "unknown"), "size_gb": size_gb})
    return sorted(models, key=lambda m: m["name"])


def guess_num_ctx(model_name: str) -> int:
    lowered = model_name.lower()
    for prefix, ctx in MODEL_CONTEXT_HINTS:
        if lowered.startswith(prefix):
            return ctx
    return DEFAULT_NUM_CTX


def generate(model: str, prompt: str, num_ctx: int):
    """Stream text from /api/generate, yielding chunks as they arrive."""
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": True,
        "options": {"num_ctx": num_ctx, "temperature": 0.2},
    }
    try:
        resp = requests.post(f"{base_url()}/api/generate", json=payload,
                             stream=True, timeout=(5, None))
    except requests.ConnectionError:
        raise OllamaError("Lost connection to Ollama mid-generation.")
    if resp.status_code == 404:
        raise OllamaError(
            f"Model '{model}' is not installed locally. "
            f"Run `ollama pull {model}` and try again.")
    if resp.status_code != 200:
        raise OllamaError(f"Ollama error ({resp.status_code}).")

    try:
        for line in resp.iter_lines(decode_unicode=True):
            if not line:
                continue
            import json
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if data.get("done"):
                break
            chunk = data.get("response")
            if chunk:
                yield chunk
    except requests.RequestException:
        raise OllamaError("Connection to Ollama dropped mid-generation.")
