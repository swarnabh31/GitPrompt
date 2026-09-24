"""GitHub API access, repository archive download, and source-file selection."""

import os
import re
import shutil
import tarfile
import tempfile
from urllib.parse import urlparse

import requests

GITHUB_API = "https://api.github.com"
RAW_BASE = "https://raw.githubusercontent.com"
CODELOAD_BASE = "https://codeload.github.com"

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")

# Hard caps that keep the app dependable on huge repositories.
MAX_ARCHIVE_BYTES = 300 * 1024 * 1024   # 300 MB downloaded archive
MAX_FILE_BYTES = 120 * 1024             # 120 KB per source file
PER_FILE_TOKEN_CAP = 600                # ~2,400 chars of any single file


class RepoError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


# ------------------------------------------------------------------ API ---

def gh_headers():
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return headers


def parse_repo_input(raw: str) -> tuple[str, str]:
    """Accepts 'owner/repo', a full GitHub URL, or a 'gitprompt.com' URL
    (hub -> prompt swap) and returns (owner, repo)."""
    s = (raw or "").strip()
    if not s:
        raise RepoError("Enter a repository.")

    s = re.sub(r"^(https?://)?(www\.)?gitprompt\.com",
               "github.com", s, flags=re.I)

    if "://" not in s and "github.com" in s.lower():
        s = "https://" + s
    elif "://" not in s and re.match(r"^[\w.-]+/[\w.-]+$", s):
        parts = s.rstrip("/").split("/")
        return parts[0], parts[1].removesuffix(".git")

    parsed = urlparse(s)
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) < 2:
        raise RepoError("Couldn't parse that as owner/repository.")
    return parts[0], parts[1].removesuffix(".git")


def gh_get(url, params=None):
    try:
        resp = requests.get(url, headers=gh_headers(), params=params, timeout=15)
    except requests.RequestException:
        raise RepoError("Couldn't reach GitHub. Check your connection.", 502)
    if resp.status_code == 404:
        raise RepoError("Repository not found. Check the URL and try again.", 404)
    if resp.status_code == 403 and "rate limit" in resp.text.lower():
        raise RepoError(
            "GitHub API rate limit hit. Set a GITHUB_TOKEN environment "
            "variable (no scopes needed) and try again.", 429)
    if resp.status_code >= 400:
        raise RepoError(f"GitHub API error ({resp.status_code}).", resp.status_code)
    return resp


def fetch_repo_meta(owner, repo):
    return gh_get(f"{GITHUB_API}/repos/{owner}/{repo}").json()


def fetch_languages(owner, repo):
    return gh_get(f"{GITHUB_API}/repos/{owner}/{repo}/languages").json()


def fetch_tree(owner, repo, branch):
    resp = gh_get(f"{GITHUB_API}/repos/{owner}/{repo}/git/trees/{branch}",
                  params={"recursive": "1"})
    return [i["path"] for i in resp.json().get("tree", []) if i["type"] == "blob"]


def fetch_readme_excerpt(owner, repo, max_chars=1200):
    resp = requests.get(f"{GITHUB_API}/repos/{owner}/{repo}/readme",
                        headers=gh_headers(), timeout=15)
    if resp.status_code != 200:
        return None
    import base64
    try:
        text = base64.b64decode(resp.json()["content"]).decode("utf-8", "ignore")
    except Exception:
        return None
    text = re.sub(r"<[^>]+>", "", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rsplit("\n", 1)[0] + "\n…"
    return text


def fetch_raw(owner, repo, branch, path):
    resp = requests.get(f"{RAW_BASE}/{owner}/{repo}/{branch}/{path}", timeout=15)
    return resp.text if resp.status_code == 200 else None


# --------------------------------------------------------------- archive ---

def download_archive(owner, repo, branch, dest_dir):
    """Download the repo tarball from codeload.github.com (no API quota)."""
    url = f"{CODELOAD_BASE}/{owner}/{repo}/tar.gz/{branch}"
    try:
        with requests.get(url, stream=True, timeout=(10, 60)) as resp:
            if resp.status_code == 404:
                raise RepoError("Couldn't download the source archive (404).", 404)
            resp.raise_for_status()
            archive_path = os.path.join(dest_dir, "repo.tar.gz")
            with open(archive_path, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=1 << 16):
                    fh.write(chunk)
                    if fh.tell() > MAX_ARCHIVE_BYTES:
                        raise RepoError("Repository archive is too large.", 413)
    except requests.RequestException:
        raise RepoError("Couldn't download the repository from GitHub.", 502)
    return archive_path


def extract_archive(archive_path, dest_dir):
    """Extract safely — rejects any member that would escape dest_dir."""
    try:
        with tarfile.open(archive_path, "r:gz") as tar:
            members = []
            dest_real = os.path.realpath(dest_dir)
            for member in tar.getmembers():
                target = os.path.realpath(os.path.join(dest_dir, member.name))
                if target == dest_real or target.startswith(dest_real + os.sep):
                    members.append(member)
            tar.extractall(dest_dir, members=members)
    except (tarfile.TarError, OSError):
        raise RepoError("Failed to extract the repository archive.", 502)
    # The tarball wraps everything in a single "<repo>-<branch>/" folder.
    entries = [e for e in os.listdir(dest_dir) if e != "repo.tar.gz"]
    if len(entries) == 1 and os.path.isdir(os.path.join(dest_dir, entries[0])):
        return os.path.join(dest_dir, entries[0])
    return dest_dir


# ---------------------------------------------------------- file selection ---

SKIP_DIRS = {
    ".git", "node_modules", "dist", "build", "out", ".next", ".nuxt",
    "coverage", "__pycache__", ".pytest_cache", ".mypy_cache", "venv",
    ".venv", "env", ".env", "target", "vendor", "bin", "obj", ".idea",
    ".vscode", ".turbo", ".cache", "tmp", "temp", "Pods", "DerivedData",
    "staticfiles", "site-packages", ".tox", "htmlcov", "bower_components",
}

CODE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rs",
    ".java", ".c", ".h", ".cpp", ".hpp", ".cs", ".rb", ".php", ".swift",
    ".kt", ".kts", ".scala", ".sh", ".bash", ".sql", ".html", ".htm",
    ".css", ".scss", ".sass", ".less", ".vue", ".svelte", ".astro",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".xml", ".md",
}

ALWAYS_INCLUDE_NAMES = {
    "dockerfile", "makefile", "docker-compose.yml", "docker-compose.yaml",
    "package.json", "pyproject.toml", "requirements.txt", "go.mod",
    "cargo.toml", "gemfile", "composer.json", "pom.xml", "build.gradle",
    "tsconfig.json", "vite.config.js", "vite.config.ts", "next.config.js",
    ".env.example", "readme.md",
}

LOCK_FILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "cargo.lock",
    "poetry.lock", "pipfile.lock", "composer.lock", "gemfile.lock",
    "bun.lockb", "uv.lock", "npm-shrinkwrap.json",
}

NAME_HINTS = (
    "main", "app", "index", "server", "entry", "cli", "routes", "router",
    "controller", "controllers", "service", "services", "model", "models",
    "schema", "schemas", "types", "config", "core", "engine", "auth",
    "api", "store", "manager", "handlers", "views", "commands", "middleware",
)


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _is_candidate(rel_path: str) -> bool:
    base = rel_path.rsplit("/", 1)[-1].lower()
    if base in LOCK_FILES or base.endswith(".min.js") or base.endswith(".map"):
        return False
    if base in ALWAYS_INCLUDE_NAMES:
        return True
    ext = os.path.splitext(base)[1]
    return ext in CODE_EXTENSIONS


def _score(rel_path: str, token_count: int) -> float:
    base = os.path.splitext(rel_path.rsplit("/", 1)[-1].lower())[0]
    score = 0.0
    if rel_path.rsplit("/", 1)[-1].lower() in ALWAYS_INCLUDE_NAMES:
        score += 100
    if any(h in base for h in NAME_HINTS):
        score += 20
    score -= rel_path.count("/") * 4          # shallower files matter more
    score -= min(token_count, 800) * 0.01     # mild preference for focused files
    return score


def collect_source_files(root: str, token_budget: int):
    """Walk the extracted repo and pick the most valuable files that fit
    inside token_budget. Returns (files, stats)."""
    candidates = []
    skipped_binary = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fname in filenames:
            full = os.path.join(dirpath, fname)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if not _is_candidate(rel):
                skipped_binary += 1
                continue
            try:
                if os.path.getsize(full) > MAX_FILE_BYTES:
                    skipped_binary += 1
                    continue
                with open(full, encoding="utf-8", errors="ignore") as fh:
                    content = fh.read()
            except OSError:
                continue
            if "\x00" in content:               # binary masquerading as text
                skipped_binary += 1
                continue
            candidates.append((rel, content))

    if not candidates:
        raise RepoError("No readable source files found in this repository.", 422)

    scored = sorted(
        candidates,
        key=lambda item: _score(item[0], estimate_tokens(item[1])),
        reverse=True,
    )

    files, used, truncated_count = [], 0, 0
    for rel, content in scored:
        tokens = estimate_tokens(content)
        truncated = False
        if tokens > PER_FILE_TOKEN_CAP:
            # Truncate by whole lines to roughly the per-file cap.
            char_cap = PER_FILE_TOKEN_CAP * 4
            kept = content[:char_cap].rsplit("\n", 1)[0]
            content = kept + "\n# …(truncated by GitPrompt)"
            tokens = estimate_tokens(content)
            truncated = True
        if used + tokens > token_budget:
            continue                          # skip; maybe a smaller file fits
        files.append({"path": rel, "content": content, "truncated": truncated})
        used += tokens
        truncated_count += truncated

    if not files:
        raise RepoError(
            "Context window is too small for even one file. "
            "Select a larger context size or a model with a bigger window.", 422)

    files.sort(key=lambda f: f["path"])       # stable, readable order
    stats = {
        "candidates": len(candidates),
        "selected": len(files),
        "skipped": skipped_binary,
        "context_tokens": used,
        "truncated_files": truncated_count,
    }
    return files, stats
