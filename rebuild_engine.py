"""Mode 2 (Deep Rebuild): source download -> file selection -> Ollama spec."""

import os
import shutil
import tempfile

import github_tools as gh
import ollama_client

# Tokens reserved for the instruction template and the model's answer.
TEMPLATE_RESERVE = 1_600
MIN_OUTPUT_RESERVE = 2_000


def _resolve_num_ctx(model, requested):
    if requested:
        return requested
    return ollama_client.guess_num_ctx(model)


def _metadata_block(meta, languages):
    lines = [
        f"Name: {meta.get('name')}",
        f"Description: {meta.get('description') or 'none'}",
        f"Default branch: {meta.get('default_branch')}",
    ]
    if languages:
        total = sum(languages.values()) or 1
        lines.append("Languages: " + ", ".join(
            f"{l} ({b * 100 // total}%)" for l, b in
            sorted(languages.items(), key=lambda kv: -kv[1])[:6]))
    return "\n".join(lines)


LLM_TEMPLATE = """You are a senior software architect. You are given the \
metadata and the most important source files of an existing open-source \
project. Write a precise, self-contained BUILD SPECIFICATION that an AI \
coding agent could use to recreate the project from scratch without ever \
seeing the original code.

Follow this structure exactly:

1. PROJECT OVERVIEW - what the project does and who it is for
2. TECH STACK - languages, frameworks, and key libraries
3. ARCHITECTURE - modules/components, their responsibilities, how they interact
4. DATA MODELS - every schema/struct/class with its fields and types
5. CORE LOGIC - important functions: name, signature, and exact behavior
6. INTERFACES - HTTP endpoints, CLI commands, or public APIs with signatures
7. CONFIGURATION - environment variables and config files
8. BUILD & RUN - exact commands to install, test, build, and run
9. BUILD ORDER - a numbered implementation checklist for the agent

Rules:
- Base everything ONLY on the provided files. Do not invent features.
- Use exact identifiers: function names, route paths, env var names.
- Be complete but concise. Start directly with "1. PROJECT OVERVIEW".

<metadata>
{metadata}
</metadata>

<files>
{files}
</files>

BUILD SPECIFICATION:
"""


def _files_block(files):
    parts = []
    for f in files:
        parts.append(f'### FILE: {f["path"]}\n{f["content"]}')
    return "\n\n".join(parts)


def _agent_prompt(meta, full_name, spec):
    """Wrap the LLM's spec in the final prompt handed to the coding agent."""
    return f"""# BUILD TASK: Recreate "{meta.get('name')}" from scratch

Reference repository: github.com/{full_name} (used as ground truth for the
specification below; write clean, original code — do not copy comments or
license headers from the original).

{spec.strip()}

## Agent instructions
- Implement the project in the order of the BUILD ORDER checklist above.
- Match the specified module structure, data models, and interfaces exactly.
- Use the BUILD & RUN commands to verify the project works before finishing.
- Where the specification is silent, make a reasonable engineering decision
  and record it under an "ASSUMPTIONS" section at the end.
"""


def run(owner, repo, model, requested_ctx):
    """Yield NDJSON event dicts: status / files / chunk / done / error."""
    yield {"type": "status", "message": "Fetching repository metadata…"}
    meta = gh.fetch_repo_meta(owner, repo)
    branch = meta.get("default_branch", "main")
    full_name = meta.get("full_name", f"{owner}/{repo}")
    languages = gh.fetch_languages(owner, repo)

    num_ctx = _resolve_num_ctx(model, requested_ctx)
    output_reserve = max(MIN_OUTPUT_RESERVE, int(num_ctx * 0.35))
    file_budget = max(2000, num_ctx - TEMPLATE_RESERVE - output_reserve)

    yield {"type": "status", "message": "Downloading source archive…"}
    tmp = tempfile.mkdtemp(prefix="gitprompt-")
    try:
        archive = gh.download_archive(owner, repo, branch, tmp)
        yield {"type": "status", "message": "Extracting and scanning files…"}
        root = gh.extract_archive(archive, tmp)
        files, stats = gh.collect_source_files(root, file_budget)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    yield {"type": "files", "stats": stats, "num_ctx": num_ctx}
    yield {"type": "status",
           "message": f"Generating build spec with {model} (this can take a few minutes)…"}

    llm_prompt = LLM_TEMPLATE.format(
        metadata=_metadata_block(meta, languages),
        files=_files_block(files),
    )
    spec_parts = []
    for chunk in ollama_client.generate(model, llm_prompt, num_ctx):
        spec_parts.append(chunk)
        yield {"type": "chunk", "text": chunk}

    spec = "".join(spec_parts).strip()
    if len(spec) < 200:
        raise gh.RepoError(
            "The model returned an unusable response. Try a larger model "
            "or a larger context window.", 502)

    yield {"type": "done", "prompt": _agent_prompt(meta, full_name, spec),
           "repo": full_name}
