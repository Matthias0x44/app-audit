// app-audit desktop frontend.
// Talks to the Rust `run_api` command, which shells out to the Python sidecar
// (api.py) and returns its JSON. Uses the global Tauri API so the frontend
// needs no bundler.

const invoke = window.__TAURI__.core.invoke;

const titles = {
  scan: "Installed apps",
  overlap: "Overlap",
  subscriptions: "Subscriptions",
  privacy: "Privacy grades",
  caches: "Caches",
};

let currentView = "scan";

async function runApi(command, args = {}) {
  const raw = await invoke("run_api", { command, args: JSON.stringify(args) });
  const parsed = JSON.parse(raw);
  if (!parsed.ok) throw new Error(parsed.error || "unknown error");
  return parsed.data;
}

function fmtDate(iso) {
  if (!iso) return '<span class="tag-never">Never</span>';
  const days = Math.floor((Date.now() - new Date(iso)) / 86400000);
  if (days <= 0) return "Today";
  if (days < 30) return `${days}d ago`;
  if (days < 365) return `${Math.floor(days / 30)}mo ago`;
  return `${Math.floor(days / 365)}y ago`;
}

function fmtSize(bytes) {
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (bytes >= 1024 && i < u.length - 1) { bytes /= 1024; i++; }
  return `${bytes.toFixed(1)} ${u[i]}`;
}

function gradeChip(g) {
  return g ? `<span class="chip grade-${g}">${g}</span>` : '<span class="dim">—</span>';
}

const body = () => document.getElementById("view-body");
const loading = () => { body().innerHTML = '<div class="placeholder">Running…</div>'; };
const fail = (e) => { body().innerHTML = `<div class="error">Error: ${e.message}</div>`; };

// ---- Views -----------------------------------------------------------------

const views = {
  async scan() {
    const apps = await runApi("scan");
    const rows = apps.map((a) => `
      <tr>
        <td class="app-name">${a.name}</td>
        <td class="dim">${a.source}</td>
        <td>${fmtDate(a.last_used)}</td>
        <td>${gradeChip(a.privacy_grade)}</td>
        <td>${a.has_alternative ? '<span class="tag-active">Yes</span>' : '<span class="dim">—</span>'}</td>
      </tr>`).join("");
    body().innerHTML = `
      <div class="summary"><span class="big">${apps.length}</span> apps installed</div>
      <table>
        <thead><tr><th>App</th><th>Source</th><th>Last used</th><th>Privacy</th><th>Alternative?</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  },

  async overlap() {
    const cats = await runApi("overlap");
    if (!cats.length) { body().innerHTML = '<div class="placeholder">No overlaps found.</div>'; return; }
    body().innerHTML = cats.map((c) => `
      <div class="card">
        <h3>${c.category} · ${c.apps.length} apps</h3>
        ${c.apps.map((a) => `
          <div class="row">
            <span>${a.name} <span class="dim">${fmtDate(a.last_used)}</span></span>
            <span>${a.keeper ? '<span class="keeper">← likely keeper</span>' : '<span class="dim">candidate to remove</span>'}</span>
          </div>`).join("")}
      </div>`).join("");
  },

  async subscriptions() {
    const d = await runApi("subscriptions");
    if (!d.rows.length) { body().innerHTML = '<div class="placeholder">No known subscriptions among installed apps.</div>'; return; }
    const rows = d.rows.map((r) => `
      <tr>
        <td class="app-name">${r.name}</td>
        <td>$${r.price.toFixed(2)}</td>
        <td>${fmtDate(r.last_used)}</td>
        <td>${r.unused ? '<span class="tag-never">Paying but unused</span>' : '<span class="tag-active">Active</span>'}</td>
      </tr>`).join("");
    body().innerHTML = `
      <div class="summary">
        Estimated <span class="big">$${d.monthly_total.toFixed(2)}/mo</span>
        ${d.wasted_total > 0 ? `· <span class="warn">$${d.wasted_total.toFixed(2)}/mo on unused</span>` : ""}
        <div class="dim" style="font-size:11px;margin-top:6px">Indicative public prices, not from your account.</div>
      </div>
      <table>
        <thead><tr><th>Subscription</th><th>Est. /mo</th><th>Last used</th><th>Verdict</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  },

  async privacy() {
    const rows = await runApi("privacy_all");
    if (!rows.length) { body().innerHTML = '<div class="placeholder">No installed apps in the privacy database yet.</div>'; return; }
    body().innerHTML = `
      <table>
        <thead><tr><th>App</th><th>Grade</th><th>Score</th></tr></thead>
        <tbody>${rows.map((r) => `
          <tr><td class="app-name">${r.name}</td><td>${gradeChip(r.grade)}</td>
          <td class="dim">${r.points > 0 ? "+" : ""}${r.points}</td></tr>`).join("")}</tbody>
      </table>`;
  },

  async caches() {
    const d = await runApi("caches");
    const rows = d.entries.slice(0, 40).map((e) => `
      <tr><td class="app-name">${e.name}</td><td>${fmtSize(e.size)}</td></tr>`).join("");
    body().innerHTML = `
      <div class="summary">Total cache: <span class="big">${fmtSize(d.total)}</span></div>
      <table>
        <thead><tr><th>Cache</th><th>Size</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  },
};

// ---- Navigation ------------------------------------------------------------

async function show(view) {
  currentView = view;
  document.getElementById("view-title").textContent = titles[view];
  document.querySelectorAll(".nav-item").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === view));
  loading();
  try { await views[view](); } catch (e) { fail(e); }
}

document.querySelectorAll(".nav-item").forEach((btn) =>
  btn.addEventListener("click", () => show(btn.dataset.view)));
document.getElementById("refresh").addEventListener("click", () => show(currentView));

show("scan");
