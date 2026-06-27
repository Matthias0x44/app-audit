# app-audit desktop

A [Tauri](https://tauri.app) shell around the app-audit Python backend. The Rust
layer exposes a single `run_api` command that shells out to the Python sidecar
(`../api.py`) and returns JSON; the frontend renders it natively.

```
desktop/
  index.html, main.js, styles.css   ← build-step-free frontend (global Tauri API)
  src-tauri/
    src/lib.rs                       ← run_api command (calls the Python sidecar)
    tauri.conf.json                  ← window + frontendDist config
  build-sidecar.sh                   ← freezes api.py into a binary for production
```

## Architecture

```
frontend (JS)  ──invoke("run_api")──▶  Rust  ──exec──▶  python3 ../api.py <cmd> <json>
      ▲                                                          │
      └──────────────  JSON  ◀───────────────────────────────────┘
```

The tested Python modules (`scanner`, `caches`, `privacy`, `sar`, …) are reused
as-is — `api.py` is just a JSON front door over them.

## Run it (dev)

Requires the Rust toolchain and Python 3 (the same one that runs the CLI).

```bash
cargo tauri dev
```

In dev the Rust command calls system `python3 ../api.py`. Override with
`APP_AUDIT_PYTHON` / `APP_AUDIT_API` env vars if needed.

## Build a distributable .app

The dev path depends on the user's Python. For distribution, freeze the backend
into a standalone sidecar binary first:

```bash
pip install pyinstaller
./build-sidecar.sh          # writes src-tauri/binaries/audit-api-<triple>
cargo tauri build
```

Then wire the frozen binary in as a Tauri `externalBin` (see the script's output)
and point `APP_AUDIT_API` at it.

## Status

Scaffold. Wired end-to-end: **Installed apps, Overlap, Subscriptions, Privacy
grades, Caches**. Not yet surfaced in the UI (backend ready via `api.py`):
alternatives detail, SAR email composer, cache clearing, dataset export.
