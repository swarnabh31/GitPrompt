/* GitPrompt frontend — Overview (metadata) + Deep Rebuild (Ollama, streaming). */

const $ = (id) => document.getElementById(id);

const form = $("prompt-form");
const input = $("repo-input");
const generateBtn = $("generate-btn");
const formHint = $("form-hint");
const modelSelect = $("model-select");
const ctxSelect = $("ctx-select");
const modelNote = $("model-note");
const refreshModelsBtn = $("refresh-models");
const progressLine = $("progress-line");
const progressText = $("progress-text");
const result = $("result");
const resultTitle = $("result-title");
const promptOutput = $("prompt-output");
const copyBtn = $("copy-btn");
const downloadBtn = $("download-btn");
const cancelBtn = $("cancel-btn");

const defaultHint = formHint.textContent;
let currentMode = "overview";
let abortController = null;
let lastPrompt = "";
let lastRepo = "prompt";

/* ------------------------------------------------------------- modes --- */

document.querySelectorAll(".mode-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    if (abortController) return; // don't switch mid-generation
    currentMode = tab.dataset.mode;
    document.querySelectorAll(".mode-tab").forEach((t) =>
      t.classList.toggle("active", t === tab));
    $("overview-panel").classList.toggle("open", currentMode === "overview");
    $("rebuild-panel").classList.toggle("open", currentMode === "rebuild");
  });
});

function getOptions() {
  const options = {};
  document.querySelectorAll("#overview-panel input[type=checkbox]").forEach((cb) => {
    options[cb.name] = cb.checked;
  });
  return options;
}

/* ------------------------------------------------------------- models --- */

async function loadModels() {
  modelSelect.disabled = true;
  try {
    const res = await fetch("/api/models");
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "Ollama unavailable");

    modelSelect.innerHTML = "";
    data.models.forEach((m) => {
      const opt = document.createElement("option");
      opt.value = m.name;
      opt.textContent = m.size_gb ? `${m.name} (${m.size_gb} GB)` : m.name;
      modelSelect.appendChild(opt);
    });
    modelSelect.disabled = false;
    modelNote.innerHTML = `${data.models.length} model(s) found locally. ` +
      `Pull more with <code>ollama pull &lt;model&gt;</code>.`;

    const saved = localStorage.getItem("gitprompt:model");
    if (saved && [...modelSelect.options].some((o) => o.value === saved)) {
      modelSelect.value = saved;
    }
  } catch (err) {
    modelSelect.innerHTML = "<option>No Ollama models available</option>";
    modelNote.innerHTML =
      `<span class="error">Ollama isn't reachable.</span> Install it from ` +
      `<a href="https://ollama.com" target="_blank" rel="noopener">ollama.com</a>, ` +
      `start it, and pull a model (e.g. <code>ollama pull llama3.2</code>). ` +
      `Overview mode works without Ollama.`;
  }
}

refreshModelsBtn.addEventListener("click", loadModels);
modelSelect.addEventListener("change", () =>
  localStorage.setItem("gitprompt:model", modelSelect.value));
loadModels();

/* ------------------------------------------------------------ helpers --- */

function setLoading(isLoading) {
  generateBtn.disabled = isLoading;
  document.querySelectorAll(".chip, .mode-tab").forEach((el) => (el.disabled = isLoading));
  generateBtn.innerHTML = isLoading
    ? '<span class="spinner"></span>Generating…'
    : "Get prompt";
  cancelBtn.hidden = !isLoading || currentMode !== "rebuild";
  progressLine.hidden = !isLoading || currentMode !== "rebuild";
}

function setHint(message, isError) {
  formHint.textContent = message;
  formHint.classList.toggle("error", !!isError);
}

function setProgress(message) {
  progressText.textContent = message;
}

function showResult(repoName) {
  resultTitle.textContent = `Prompt for ${repoName}`;
  result.classList.add("open");
  downloadBtn.hidden = false;
  result.scrollIntoView({ behavior: "smooth", block: "start" });
}

/* --------------------------------------------------------- overview mode --- */

async function generateOverview(rawValue) {
  const res = await fetch("/api/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ repo: rawValue, options: getOptions() }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "Something went wrong.");
  lastPrompt = data.prompt;
  lastRepo = data.repo;
  promptOutput.textContent = data.prompt;
  showResult(data.repo);
}

/* --------------------------------------------------------- rebuild mode --- */

async function generateRebuild(rawValue) {
  const model = modelSelect.value;
  if (!model || modelSelect.disabled) {
    throw new Error("No local Ollama model selected. Start Ollama and pull a model first.");
  }
  abortController = new AbortController();
  promptOutput.textContent = "";

  const res = await fetch("/api/rebuild", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    signal: abortController.signal,
    body: JSON.stringify({
      repo: rawValue,
      model,
      num_ctx: ctxSelect.value ? parseInt(ctxSelect.value, 10) : null,
    }),
  });

  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `Server error (${res.status}).`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, idx).trim();
      buffer = buffer.slice(idx + 1);
      if (!line) continue;
      handleRebuildEvent(JSON.parse(line));
    }
  }
}

function handleRebuildEvent(event) {
  if (event.type === "status") {
    setProgress(event.message);
  } else if (event.type === "files") {
    const s = event.stats;
    setProgress(
      `Context: ${s.selected}/${s.candidates} files (~${s.context_tokens.toLocaleString()} tokens, ` +
      `${(event.num_ctx / 1024).toFixed(0)}k window). Generating…`);
  } else if (event.type === "chunk") {
    promptOutput.textContent += event.text;
  } else if (event.type === "done") {
    lastPrompt = event.prompt;
    lastRepo = event.repo;
    showResult(event.repo);
  } else if (event.type === "error") {
    throw new Error(event.message);
  }
}

/* -------------------------------------------------------------- submit --- */

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const rawValue = input.value.trim();
  if (!rawValue) { input.focus(); return; }

  setLoading(true);
  setHint("Reading the repository from GitHub…", false);
  result.classList.remove("open");
  promptOutput.textContent = "";
  downloadBtn.hidden = true;

  try {
    if (currentMode === "overview") {
      await generateOverview(rawValue);
    } else {
      await generateRebuild(rawValue);
    }
    setHint(defaultHint, false);
  } catch (err) {
    if (err.name === "AbortError") {
      setHint("Generation cancelled.", false);
    } else {
      setHint(err.message, true);
    }
  } finally {
    abortController = null;
    setLoading(false);
  }
});

cancelBtn.addEventListener("click", () => abortController && abortController.abort());

document.querySelectorAll(".chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    input.value = chip.dataset.repo;
    form.requestSubmit();
  });
});

copyBtn.addEventListener("click", () => {
  navigator.clipboard.writeText(promptOutput.textContent).then(() => {
    const original = copyBtn.textContent;
    copyBtn.textContent = "Copied";
    setTimeout(() => (copyBtn.textContent = original), 1500);
  });
});

downloadBtn.addEventListener("click", () => {
  const blob = new Blob([lastPrompt], { type: "text/markdown" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${lastRepo.replace("/", "_")}.prompt.md`;
  a.click();
  URL.revokeObjectURL(a.href);
});
