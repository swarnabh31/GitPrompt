# GitPrompt

Turn any public GitHub repository into a build prompt for your AI coding agent.

Paste a repo URL, get back a structured prompt an agent (Claude Code, Cursor,
Aider, any LLM-based coding tool) can implement against — so you can rebuild,
study, or bootstrap from existing open-source projects.

## Two modes

| Mode | How it works | Needs Ollama? |
|------|--------------|---------------|
| **Overview** | Reads repo metadata via the GitHub API (tree, dependencies, README, entry points, issues) and assembles a structured prompt in seconds. | No |
| **Deep Rebuild** | Downloads the actual source archive, selects the most important files that fit your model's context window, and uses a **local Ollama model** to synthesize a complete build specification — data models, core logic, interfaces, and an ordered implementation checklist. | Yes |

## Quickstart

```bash
git clone https://github.com/<you>/gitprompt.git
cd gitprompt
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000

### Deep Rebuild mode — Ollama setup

1. Install [Ollama](https://ollama.com) and start it (the desktop app or `ollama serve`).
2. Pull a model — quality scales with model size:

   ```bash
   ollama pull llama3.2        # 3B, fast, small context
   ollama pull qwen2.5:14b     # good balance
   ollama pull llama3.3:70b    # best quality, needs serious hardware
   ```

3. The model dropdown in the UI lists everything installed locally and
   refreshes from Ollama's `/api/tags` endpoint.

## Configuration

| Environment variable | Purpose |
|----------------------|---------|
| `GITHUB_TOKEN` | Personal access token (no scopes needed for public repos). Raises the GitHub API limit from 60 to 5,000 requests/hour. |
| `OLLAMA_HOST` | Ollama base URL. Default: `http://localhost:11434`. |

```bash
export GITHUB_TOKEN=ghp_xxxxxxxx   # macOS/Linux
set GITHUB_TOKEN=ghp_xxxxxxxx      # Windows
```

## How Deep Rebuild works

1. **Download** — repo tarball from `codeload.github.com` (no API quota).
2. **Select** — files are scored (entry points, manifests, routers/models/schemas rank highest; binaries, lockfiles, and build output are skipped) and packed into the context window with room reserved for the model's answer.
3. **Synthesize** — Ollama streams a build specification.
4. **Wrap** — the spec is wrapped in an agent-ready prompt with build/run commands and an ordered checklist, ready to copy or download.

Everything runs locally: repository code goes to your Ollama instance and nowhere else.

## Limitations

- The generated spec is only as good as the model and the files that fit in
  context. Huge monorepos will be partially covered — check the file counts
  shown during generation.
- Always review the license of a repository before recreating its code, and
  respect the original authors' terms.

## Contributing

Issues and pull requests are welcome. Please run the app, try both modes
against a few repos, and include the repo URL in any bug report.

## License

MIT — see [LICENSE](LICENSE).
