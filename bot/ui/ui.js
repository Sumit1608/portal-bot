// portal-bot UI: talks only to this API (same origin). Text is always set with
// textContent, never as HTML, so job data can't inject anything into the page.
"use strict";

const KEY = "portal-bot-token";
const $ = (id) => document.getElementById(id);
const FILTERS = {
  all: () => true,
  active: (j) => j.status === "pending" || j.status === "running",
  done: (j) => j.status === "done",
  failed: (j) => j.status === "failed",
};
let timer = null;
let filter = "all";
let lastData = null;

function token() {
  try { return sessionStorage.getItem(KEY) || ""; } catch { return ""; }
}
function setToken(value) {
  try { value ? sessionStorage.setItem(KEY, value) : sessionStorage.removeItem(KEY); } catch {}
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: { "Authorization": `Bearer ${token()}`, "Content-Type": "application/json" },
    cache: "no-store",
  });
  let body = {};
  try { body = await res.json(); } catch {}
  return { status: res.status, body };
}

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function setStatus(text, state) {
  $("status").textContent = text;
  $("status-chip").dataset.state = state;
}

function ago(iso) {
  if (!iso) return "";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString();
}

function took(job) {
  if (!job.started_at) return "";
  const end = job.finished_at ? new Date(job.finished_at) : new Date();
  const s = Math.max(0, Math.round((end - new Date(job.started_at)) / 1000));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

function setCounts(c) {
  for (const k of ["pending", "running", "done", "failed"]) {
    $(`n-${k}`).textContent = c ? String(c[k] || 0) : "–";
  }
}

function showEmpty(text) {
  $("empty-text").textContent = text;
  $("empty").classList.toggle("show", Boolean(text));
}

function render(data) {
  lastData = data;
  setCounts(data.counts || {});
  if (data.worker_running) setStatus("Connected · worker running", "on");
  else setStatus("Connected · worker off (start with --worker)", "idle");

  const body = $("jobs");
  body.replaceChildren();
  const jobs = [...(data.jobs || [])].reverse().filter(FILTERS[filter]);   // newest first
  if (!jobs.length) {
    showEmpty(data.jobs && data.jobs.length ? "No jobs match this filter." : "No jobs yet. Queue one on the left.");
    return;
  }
  showEmpty("");
  for (const j of jobs) {
    const row = body.insertRow();
    const i = j.inputs || {};

    row.insertCell().append(el("span", `pill ${j.status}`, j.status));

    const jobCell = row.insertCell();
    jobCell.append(el("div", "job-label", i.label || j.id));
    const br = el("div", "branches");
    br.append(el("span", "branch", i.base_branch || "?"), el("span", "vs", "vs"),
              el("span", "branch", i.head_branch || "?"));
    jobCell.append(br);

    const added = el("span", "when", ago(j.created_at));
    added.title = j.created_at ? new Date(j.created_at).toLocaleString() : "";
    row.insertCell().append(added);
    row.insertCell().append(el("span", "when", took(j)));

    const result = row.insertCell();
    if (j.status === "failed") {
      result.className = "result error";
      result.textContent = j.error || "failed";
    } else {
      result.className = "result";
      result.textContent = j.output_dir || (j.status === "running" ? "working…" : "waiting for the worker");
    }
  }
}

async function refresh() {
  if (!token()) return;
  try {
    const { status, body } = await api("/jobs");
    if (status === 401) {
      setStatus("Wrong token", "error");
      document.body.classList.remove("connected");
      stop();
      return;
    }
    if (status === 200) {
      document.body.classList.add("connected");
      render(body);
    }
  } catch {
    setStatus("API not reachable", "error");
  }
}

function start() {
  stop();
  refresh();
  timer = setInterval(refresh, 3000);
}
function stop() {
  if (timer) clearInterval(timer);
  timer = null;
}

function disconnect(text) {
  setToken("");
  stop();
  lastData = null;
  document.body.classList.remove("connected");
  setStatus(text, "off");
  setCounts(null);
  $("jobs").replaceChildren();
  showEmpty("Connect with your token to see jobs.");
}

$("token-form").addEventListener("submit", (e) => {
  e.preventDefault();
  setToken($("token").value.trim());
  $("token").value = "";
  start();
});

$("forget").addEventListener("click", () => disconnect("Not connected"));

for (const button of document.querySelectorAll(".filter")) {
  button.addEventListener("click", () => {
    filter = button.dataset.filter;
    for (const b of document.querySelectorAll(".filter")) b.classList.toggle("active", b === button);
    if (lastData) render(lastData);
  });
}

$("job-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const msg = $("job-message");
  if (!token()) {
    msg.className = "message error";
    msg.textContent = "Connect with your token first.";
    return;
  }
  const data = {};
  for (const [k, v] of new FormData(e.target)) if (v.trim()) data[k] = v.trim();
  const button = e.target.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    const { status, body } = await api("/jobs", { method: "POST", body: JSON.stringify(data) });
    if (status === 201) {
      msg.className = "message success";
      msg.textContent = `Queued job ${body.id}`;
      refresh();
    } else {
      msg.className = "message error";
      msg.textContent = body.error || `Request failed (HTTP ${status})`;
    }
  } catch {
    msg.className = "message error";
    msg.textContent = "API not reachable";
  } finally {
    button.disabled = false;
  }
});

showEmpty("Connect with your token to see jobs.");
if (token()) start();
