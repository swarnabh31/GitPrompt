"""Mode 1 (Overview): metadata-only prompt built purely from the GitHub API."""

import json
import re

import requests

import github_tools as gh

DEP_FILES = [
    "package.json", "requirements.txt", "pyproject.toml", "Pipfile",
    "Cargo.toml", "go.mod", "composer.json", "Gemfile", "pom.xml",
    "build.gradle",
]

CONFIG_FILES = {
    "manage.py": "Django management entry point",
    "settings.py": "Django settings module",
    "wsgi.py": "WSGI application entry point",
    "asgi.py": "ASGI application entry point",
    "tsconfig.json": "TypeScript compiler config",
    "next.config.js": "Next.js config", "next.config.ts": "Next.js config",
    "vite.config.js": "Vite build config", "vite.config.ts": "Vite build config",
    "webpack.config.js": "Webpack build config",
    "tailwind.config.js": "Tailwind CSS config",
    "babel.config.js": "Babel transpiler config",
    "jest.config.js": "Jest test config",
    ".eslintrc.json": "ESLint config", ".eslintrc.js": "ESLint config",
    "docker-compose.yml": "Docker Compose services",
    "docker-compose.yaml": "Docker Compose services",
    "Dockerfile": "Container build definition",
    "Makefile": "Build/task automation",
    "pytest.ini": "Pytest config", "tox.ini": "Tox test automation config",
    "CMakeLists.txt": "CMake build config",
    "pom.xml": "Maven build config", "build.gradle": "Gradle build config",
    "Cargo.toml": "Rust package manifest", "go.mod": "Go module definition",
    "pyproject.toml": "Python packaging/tooling config",
    ".pre-commit-config.yaml": "Pre-commit hook config",
}

ENTRY_POINT_CANDIDATES = [
    "manage.py", "main.py", "app.py", "wsgi.py", "asgi.py", "bin/www",
    "index.js", "index.ts", "index.mjs", "server.js", "server.ts",
    "src/index.js", "src/index.ts", "src/main.js", "src/main.ts",
    "src/main.rs", "main.go", "cmd/main.go", "Program.cs",
]

TEST_PATTERNS = re.compile(r"(^|/)(tests?|spec|__tests__)(/|$)|\.(test|spec)\.", re.I)


def build_tree(paths):
    tree = {}
    for p in paths:
        node = tree
        for part in p.split("/")[:-1]:
            node = node.setdefault(part, {})
        if isinstance(node, dict):
            node.setdefault(p.split("/")[-1], None)
    return tree


def render_tree(tree, prefix="", depth=0, max_depth=3, max_entries=10, lines=None):
    lines = [] if lines is None else lines
    if depth >= max_depth or not isinstance(tree, dict):
        return lines
    entries = sorted(tree.items(), key=lambda kv: (kv[1] is None, kv[0].lower()))
    shown, hidden = entries[:max_entries], len(entries) - max_entries
    for name, sub in shown:
        is_dir = sub is not None
        lines.append(f"{prefix}{name}{'/' if is_dir else ''}")
        if is_dir:
            render_tree(sub, prefix + "  ", depth + 1, max_depth, max_entries, lines)
    if hidden > 0:
        lines.append(f"{prefix}… {hidden} more")
    return lines


def parse_entry_hints(filename, content):
    hints = []
    try:
        if filename == "package.json":
            data = json.loads(content)
            if data.get("main"):
                hints.append(f"package.json main: {data['main']}")
            for key in ("start", "dev", "serve"):
                if key in data.get("scripts", {}):
                    hints.append(f"npm run {key} -> {data['scripts'][key]}")
        elif filename == "pyproject.toml":
            m = re.search(r"\[project\.scripts\]\s*\n(.*?)(\n\[|\Z)", content, re.S)
            if m:
                for line in m.group(1).splitlines():
                    line = line.strip()
                    if line and "=" in line:
                        hints.append(f"console script: {line}")
    except Exception:
        pass
    return hints


def parse_dependencies(filename, content):
    names = []
    try:
        if filename == "package.json":
            data = json.loads(content)
            for key in ("dependencies", "devDependencies"):
                names.extend(data.get(key, {}).keys())
        elif filename == "requirements.txt":
            for line in content.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    names.append(re.split(r"[=<>~\[; ]", line)[0])
        elif filename == "pyproject.toml":
            for m in re.finditer(r'^\s*"?([A-Za-z0-9_.-]+)"?\s*=\s*[">^~]', content, re.M):
                names.append(m.group(1))
        elif filename == "Cargo.toml":
            in_deps = False
            for line in content.splitlines():
                s = line.strip()
                if s.startswith("[dependencies"):
                    in_deps = True
                    continue
                if s.startswith("[") and in_deps:
                    in_deps = False
                if in_deps and "=" in line:
                    names.append(line.split("=")[0].strip())
        elif filename == "go.mod":
            for m in re.finditer(r"^\s*([\w./-]+)\s+v[\d.]+", content, re.M):
                names.append(m.group(1))
        elif filename in ("composer.json",):
            data = json.loads(content)
            for key in ("require", "require-dev"):
                names.extend(data.get(key, {}).keys())
        elif filename == "Gemfile":
            for m in re.finditer(r"gem\s+[\"']([\w-]+)[\"']", content):
                names.append(m.group(1))
    except Exception:
        pass
    return [n for n in names if n][:40]


def fetch_open_issues(owner, repo, limit=5):
    resp = requests.get(
        f"{gh.GITHUB_API}/repos/{owner}/{repo}/issues",
        headers=gh.gh_headers(),
        params={"state": "open", "per_page": limit,
                "sort": "comments", "direction": "desc"},
        timeout=15)
    if resp.status_code != 200:
        return []
    return [i["title"] for i in resp.json() if "pull_request" not in i][:limit]


def build_prompt(owner, repo, options):
    meta = gh.fetch_repo_meta(owner, repo)
    branch = meta.get("default_branch", "main")
    full_name = meta.get("full_name", f"{owner}/{repo}")
    description = meta.get("description") or "No description provided."
    paths = gh.fetch_tree(owner, repo, branch)

    top_level = {p for p in paths if "/" not in p}
    dep_contents = {}
    for fname in [f for f in DEP_FILES if f in top_level]:
        content = gh.fetch_raw(owner, repo, branch, fname)
        if content:
            dep_contents[fname] = content

    lines = [
        f"PROJECT: {meta.get('name', repo)}",
        f"SOURCE: github.com/{full_name}",
        f"DESCRIPTION: {description}",
        "",
        "You are rebuilding this project from scratch. Use the context below "
        "as ground truth for structure, stack, and intent. Ask before "
        "deviating from it.",
    ]

    if options.get("architecture", True):
        lines.append("\nARCHITECTURE")
        lines.append("Directory tree (depth 3, top entries per folder):")
        lines.extend(render_tree(build_tree(paths)))
        entry_points = [c for c in ENTRY_POINT_CANDIDATES if c in set(paths)]
        for fname, content in dep_contents.items():
            entry_points += parse_entry_hints(fname, content)
        if entry_points:
            lines.append("\nEntry points:")
            lines.extend(f"  - {ep}" for ep in entry_points)
        config_files = [(p, CONFIG_FILES[p.rsplit('/', 1)[-1]])
                        for p in paths if p.rsplit("/", 1)[-1] in CONFIG_FILES][:15]
        ci = [p for p in paths
              if p.startswith(".github/workflows/") and p.endswith((".yml", ".yaml"))]
        if config_files or ci:
            lines.append("\nConfig & build files:")
            for path, desc in config_files:
                lines.append(f"  - {path} — {desc}")
            if ci:
                shown = [w.rsplit("/", 1)[-1] for w in ci[:6]]
                extra = len(ci) - len(shown)
                lines.append("  - .github/workflows/ — CI: " + ", ".join(shown)
                             + (f" …+{extra} more" if extra > 0 else ""))

    if options.get("dependencies", True):
        lines.append("\nDEPENDENCIES")
        languages = gh.fetch_languages(owner, repo)
        if languages:
            total = sum(languages.values()) or 1
            lines.append("Languages: " + ", ".join(
                f"{l} ({b * 100 // total}%)" for l, b in
                sorted(languages.items(), key=lambda kv: -kv[1])[:6]))
        for fname, content in dep_contents.items():
            deps = parse_dependencies(fname, content)
            if deps:
                lines.append(f"From {fname}: " + ", ".join(deps[:25])
                             + (" …" if len(deps) > 25 else ""))

    if options.get("structure", True):
        lines.append(f"\nFILE COUNT\n{len(paths)} tracked files.")

    if options.get("tests", False):
        test_files = [p for p in paths if TEST_PATTERNS.search(p)]
        lines.append("\nTESTING")
        if test_files:
            lines.append(f"{len(test_files)} test-related files, e.g.:")
            lines.extend(f"  - {p}" for p in test_files[:8])
        else:
            lines.append("No obvious test files detected.")

    if options.get("setup", True):
        lines.append("\nSETUP")
        has_docker = any(p.lower() in ("dockerfile", "docker-compose.yml",
                                       "docker-compose.yaml") for p in paths)
        has_env = any(p.lower().endswith(".env.example") for p in paths)
        lines.append(f"Docker config present: {'yes' if has_docker else 'no'}")
        lines.append(f".env.example present: {'yes' if has_env else 'no'}")
        readme = gh.fetch_readme_excerpt(owner, repo)
        if readme:
            lines.append("README excerpt:")
            lines.append(readme)

    if options.get("issues", True):
        lines.append("\nOPEN ITEMS")
        issues = fetch_open_issues(owner, repo)
        lines.extend(f"  - {t}" for t in issues) if issues else \
            lines.append("No open issue titles retrieved.")

    lines.append(
        f"\nTASK\nRecreate {meta.get('name', repo)} as a working build, matching "
        "its structure and behavior as closely as the above context allows.")

    return "\n".join(lines), meta
