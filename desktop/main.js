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
  alternatives: "Alternatives",
  caches: "Caches",
  gdpr: "GDPR requests",
  dataset: "Dataset",
};

let currentView = "scan";
// Cross-view state.
const state = {
  alternativesQuery: "",
  cachesOrphanedOnly: false,
  gdpr: { app: "", type: "erasure", name: "", email: "" },
};

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

function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

// Promise-based confirm modal (WKWebView's native confirm() is unreliable).
function confirmModal(html) {
  return new Promise((resolve) => {
    const backdrop = document.getElementById("modal-backdrop");
    document.getElementById("modal-text").innerHTML = html;
    backdrop.classList.remove("hidden");
    const done = (val) => {
      backdrop.classList.add("hidden");
      cancel.onclick = ok.onclick = null;
      resolve(val);
    };
    const cancel = document.getElementById("modal-cancel");
    const ok = document.getElementById("modal-confirm");
    cancel.onclick = () => done(false);
    ok.onclick = () => done(true);
  });
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

// ---- Views -----------------------------------------------------------------

const views = {
  async scan() {
    const apps = await runApi("scan");
    const rows = apps.map((a) => `
      <tr>
        <td class="app-name">${esc(a.name)}</td>
        <td class="dim">${esc(a.source)}</td>
        <td>${fmtDate(a.last_used)}</td>
        <td>${gradeChip(a.privacy_grade)}</td>
        <td>${a.has_alternative
          ? `<a class="link alt-link" data-app="${esc(a.name)}">Yes</a>`
          : '<span class="dim">—</span>'}</td>
      </tr>`).join("");
    body().innerHTML = `
      <div class="summary"><span class="big">${apps.length}</span> apps installed</div>
      <table>
        <thead><tr><th>App</th><th>Source</th><th>Last used</th><th>Privacy</th><th>Alternative?</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
    document.querySelectorAll(".alt-link").forEach((el) =>
      el.onclick = () => { state.alternativesQuery = el.dataset.app; show("alternatives"); });
  },

  async overlap() {
    const cats = await runApi("overlap");
    if (!cats.length) { body().innerHTML = '<div class="placeholder">No overlaps found.</div>'; return; }
    body().innerHTML = cats.map((c) => `
      <div class="card">
        <h3>${esc(c.category)} · ${c.apps.length} apps</h3>
        ${c.apps.map((a) => `
          <div class="row">
            <span>${esc(a.name)} <span class="dim">${fmtDate(a.last_used)}</span></span>
            <span>${a.keeper ? '<span class="keeper">← likely keeper</span>' : '<span class="dim">candidate to remove</span>'}</span>
          </div>`).join("")}
      </div>`).join("");
  },

  async subscriptions() {
    const d = await runApi("subscriptions");
    if (!d.rows.length) { body().innerHTML = '<div class="placeholder">No known subscriptions among installed apps.</div>'; return; }
    const rows = d.rows.map((r) => `
      <tr>
        <td class="app-name">${esc(r.name)}</td>
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
          <tr><td class="app-name">${esc(r.name)}</td><td>${gradeChip(r.grade)}</td>
          <td class="dim">${r.points > 0 ? "+" : ""}${r.points}</td></tr>`).join("")}</tbody>
      </table>`;
  },

  async caches() {
    const cmd = state.cachesOrphanedOnly ? "orphaned_caches" : "caches";
    const d = await runApi(cmd);
    const entries = d.entries.slice(0, 60);

    const toggle = `<button class="btn" id="cache-toggle">${
      state.cachesOrphanedOnly ? "Show all caches" : "Show orphaned only"
    }</button>`;
    const clearAll = state.cachesOrphanedOnly && d.entries.length
      ? `<button class="btn btn-danger" id="cache-clear-all">Clear all ${d.entries.length} orphaned</button>`
      : "";
    const note = state.cachesOrphanedOnly
      ? '<div class="hint">Caches are disposable — apps rebuild them on next launch. Steam/launcher games may appear here since their shortcuts carry no bundle ID.</div>'
      : "";

    const rows = entries.map((e) => `
      <tr>
        <td class="app-name">${esc(e.name)}</td>
        <td>${fmtSize(e.size)}</td>
        <td><button class="btn btn-sm clear-one" data-name="${esc(e.name)}" data-size="${e.size}">Clear</button></td>
      </tr>`).join("");

    body().innerHTML = `
      <div class="summary">
        ${state.cachesOrphanedOnly ? "Orphaned cache" : "Total cache"}:
        <span class="big">${fmtSize(d.total)}</span>
        <div class="toolbar">${toggle}${clearAll}</div>
        ${note}
      </div>
      <table>
        <thead><tr><th>Cache</th><th>Size</th><th></th></tr></thead>
        <tbody>${rows || '<tr><td colspan="3" class="dim">Nothing to show.</td></tr>'}</tbody>
      </table>`;

    document.getElementById("cache-toggle").onclick = () => {
      state.cachesOrphanedOnly = !state.cachesOrphanedOnly;
      show("caches");
    };
    const clearAllBtn = document.getElementById("cache-clear-all");
    if (clearAllBtn) clearAllBtn.onclick = () => clearCaches(d.entries.map((e) => e.name), fmtSize(d.total));
    document.querySelectorAll(".clear-one").forEach((b) =>
      b.onclick = () => clearCaches([b.dataset.name], fmtSize(+b.dataset.size)));
  },

  async alternatives() {
    body().innerHTML = `
      <div class="form-row">
        <input id="alt-input" class="text-input" placeholder="App name, e.g. Spotify, Chrome, Notion"
               value="${esc(state.alternativesQuery)}" />
        <button class="btn btn-primary" id="alt-go">Find alternatives</button>
      </div>
      <div id="alt-results"></div>`;
    const run = async () => {
      const q = document.getElementById("alt-input").value.trim();
      state.alternativesQuery = q;
      if (!q) return;
      const out = document.getElementById("alt-results");
      out.innerHTML = '<div class="placeholder">Looking up…</div>';
      try {
        const d = await runApi("alternatives", { name: q });
        const rows = d.alternatives.map((a) => `
          <tr>
            <td class="app-name">${esc(a.name)}</td>
            <td>${gradeChip(a.privacy_grade)}</td>
            <td class="dim">${esc(a.license || "")}</td>
            <td class="dim">${esc((a.platforms || []).join(", "))}</td>
            <td>${a.free ? '<span class="tag-active">Yes</span>' : '<span class="dim">No</span>'}</td>
            <td class="dim">${esc(a.description || "")}</td>
          </tr>`).join("");
        out.innerHTML = `
          <div class="summary">
            <span class="big">${esc(d.display_name)}</span>
            ${d.category ? `· ${esc(d.category)}` : ""}
            · Privacy ${gradeChip(d.privacy_grade)}
            ${d.big_tech ? '· <span class="tag-never">Big Tech</span>' : ""}
          </div>
          <table>
            <thead><tr><th>Alternative</th><th>Privacy</th><th>License</th><th>Platforms</th><th>Free</th><th>Description</th></tr></thead>
            <tbody>${rows}</tbody>
          </table>`;
      } catch (e) {
        out.innerHTML = `<div class="error">${esc(e.message)}</div>`;
      }
    };
    document.getElementById("alt-go").onclick = run;
    document.getElementById("alt-input").addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") run();
    });
    if (state.alternativesQuery) run();
  },

  async gdpr() {
    const g = state.gdpr;
    body().innerHTML = `
      <div class="card form-card">
        <label>App / company
          <input id="g-app" class="text-input" value="${esc(g.app)}" placeholder="e.g. Spotify" /></label>
        <label>Request type
          <select id="g-type" class="text-input">
            <option value="access"${g.type === "access" ? " selected" : ""}>Access (Art. 15)</option>
            <option value="erasure"${g.type === "erasure" ? " selected" : ""}>Erasure (Art. 17)</option>
            <option value="portability"${g.type === "portability" ? " selected" : ""}>Portability (Art. 20)</option>
          </select></label>
        <label>Your full name
          <input id="g-name" class="text-input" value="${esc(g.name)}" placeholder="Jane Smith" /></label>
        <label>Your account email
          <input id="g-email" class="text-input" value="${esc(g.email)}" placeholder="jane@example.com" /></label>
        <button class="btn btn-primary" id="g-go">Generate email</button>
      </div>
      <div id="g-result"></div>`;

    document.getElementById("g-go").onclick = async () => {
      g.app = document.getElementById("g-app").value.trim();
      g.type = document.getElementById("g-type").value;
      g.name = document.getElementById("g-name").value.trim();
      g.email = document.getElementById("g-email").value.trim();
      if (!g.app) return;
      const out = document.getElementById("g-result");
      out.innerHTML = '<div class="placeholder">Generating…</div>';
      try {
        const d = await runApi("sar", {
          app_name: g.app, request_type: g.type,
          user_name: g.name, user_email: g.email,
        });
        const meta = [];
        if (d.to_email) meta.push(`Recipient: <b>${esc(d.to_email)}</b>`);
        else meta.push('<span class="tag-never">No known privacy contact — find it on their site</span>');
        if (d.deletion_difficulty && d.deletion_difficulty !== "unknown")
          meta.push(`Difficulty: ${esc(d.deletion_difficulty)}`);
        if (d.deletion_url) meta.push(`<a href="${esc(d.deletion_url)}" target="_blank">Self-service link</a>`);
        out.innerHTML = `
          <div class="summary">${meta.join(" · ")}</div>
          <textarea id="g-body" class="email-box" readonly>${esc(d.body)}</textarea>
          <div class="toolbar">
            <button class="btn btn-primary" id="g-copy">Copy email</button>
            <span id="g-copied" class="copied"></span>
          </div>`;
        document.getElementById("g-copy").onclick = async () => {
          const ok = await copyText(d.body);
          document.getElementById("g-copied").textContent =
            ok ? "Copied to clipboard" : "Select the text and copy manually";
        };
      } catch (e) {
        out.innerHTML = `<div class="error">${esc(e.message)}</div>`;
      }
    };
  },

  async dataset() {
    body().innerHTML = `
      <div class="card">
        <p>Export the curated privacy-contact database — every company, not just your
        installed apps — as a timestamped <b>CSV + JSON + markdown</b> artifact. Safe to
        hand to journalists, regulators, or digital-rights groups.</p>
        <p class="dim">Companies are flagged as a contact gap only when no email, DPO, or
        SAR web form is discoverable; web-form-only firms are recorded separately.</p>
        <button class="btn btn-primary" id="ds-go">Export to Downloads</button>
      </div>
      <div id="ds-result"></div>`;
    document.getElementById("ds-go").onclick = async () => {
      const out = document.getElementById("ds-result");
      out.innerHTML = '<div class="placeholder">Writing files…</div>';
      try {
        const d = await runApi("export_dataset");
        const s = d.summary;
        out.innerHTML = `
          <div class="summary">
            Exported <span class="big">${s.total}</span> companies ·
            <span class="warn">${s.non_compliant} non-compliant</span> ·
            ${s.no_discoverable_contact} no contact · ${s.web_form_only} web-form-only
          </div>
          <table>
            <thead><tr><th>File</th><th>Path</th></tr></thead>
            <tbody>${Object.entries(d.paths).map(([k, v]) =>
              `<tr><td class="app-name">${k.toUpperCase()}</td><td class="dim">${esc(v)}</td></tr>`).join("")}</tbody>
          </table>`;
      } catch (e) {
        out.innerHTML = `<div class="error">${esc(e.message)}</div>`;
      }
    };
  },
};

// Shared destructive cache-clear flow with confirmation.
async function clearCaches(names, sizeLabel) {
  const ok = await confirmModal(
    `Delete <b>${names.length}</b> cache item(s) (${sizeLabel})?<br>
     <span class="dim">Apps rebuild caches as needed. This cannot be undone.</span>`
  );
  if (!ok) return;
  try {
    const r = await runApi("clean", { names });
    show("caches");
  } catch (e) {
    fail(e);
  }
}

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
