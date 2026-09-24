"""
GitPrompt — turn any public GitHub repository into an AI-agent build prompt.

Two modes:
  * Overview    — fast, metadata-only prompt (no LLM required).
  * Deep Rebuild — downloads the real source, selects the important files,
                   and uses a local Ollama model to synthesize a build
                   specification that a coding agent can implement against.

Run:
    pip install -r requirements.txt
    python app.py
Then open http://127.0.0.1:5000

Environment variables:
    GITHUB_TOKEN   personal access token (no scopes needed) — raises the
                   GitHub API limit from 60 to 5,000 requests/hour.
    OLLAMA_HOST    Ollama base URL (default http://localhost:11434).
"""

import json
import os

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

import github_tools
import ollama_client
import prompt_builder
import rebuild_engine
from github_tools import RepoError

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


# ---------------------------------------------------------------- models ---

@app.route("/api/models")
def api_models():
    """List Ollama models installed locally — populates the UI dropdown."""
    try:
        models = ollama_client.list_models()
        return jsonify({"ok": True, "models": models})
    except ollama_client.OllamaError as exc:
        return jsonify({"ok": False, "error": exc.message})


# --------------------------------------------------------------- overview ---

@app.route("/api/generate", methods=["POST"])
def api_generate():
    """Metadata-only prompt (original GitReverse behaviour)."""
    body = request.get_json(silent=True) or {}
    options = body.get("options", {})

    try:
        owner, repo = github_tools.parse_repo_input(body.get("repo", ""))
        prompt, meta = prompt_builder.build_prompt(owner, repo, options)
    except RepoError as exc:
        return jsonify({"error": exc.message}), exc.status
    except Exception:
        return jsonify({"error": "Unexpected server error."}), 500

    return jsonify({
        "prompt": prompt,
        "repo": f"{owner}/{repo}",
        "stars": meta.get("stargazers_count"),
        "default_branch": meta.get("default_branch"),
    })


# ------------------------------------------------------------ deep rebuild ---

@app.route("/api/rebuild", methods=["POST"])
def api_rebuild():
    """Deep Rebuild — streams NDJSON events: status / files / chunk / done."""
    body = request.get_json(silent=True) or {}
    raw_repo = body.get("repo", "")
    model = (body.get("model") or "").strip()
    num_ctx = body.get("num_ctx")  # None = auto-detect from model name

    if not model:
        return jsonify({"error": "Select an Ollama model first."}), 400

    try:
        owner, repo = github_tools.parse_repo_input(raw_repo)
    except RepoError as exc:
        return jsonify({"error": exc.message}), exc.status

    def event_stream():
        try:
            for event in rebuild_engine.run(owner, repo, model, num_ctx):
                yield json.dumps(event) + "\n"
        except RepoError as exc:
            yield json.dumps({"type": "error", "message": exc.message}) + "\n"
        except ollama_client.OllamaError as exc:
            yield json.dumps({"type": "error", "message": exc.message}) + "\n"
        except Exception:
            app.logger.exception("rebuild failed")
            yield json.dumps({
                "type": "error",
                "message": "Unexpected server error. Check the Flask logs.",
            }) + "\n"

    return Response(
        stream_with_context(event_stream()),
        mimetype="application/x-ndjson",
        headers={"Cache-Control": "no-cache"},
    )


if __name__ == "__main__":
    app.run(debug=True)