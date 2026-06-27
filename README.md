# app-audit

AI-assisted app audit tool for macOS. Find what to uninstall, get FOSS alternatives, clear caches, and send GDPR Subject Access / Erasure requests in one shot.

**macOS only · Python 3.8+ · No account required**

> **CLI + desktop.** The Python CLI below is the full tool. A native desktop
> shell (Tauri + Python sidecar) lives in [`desktop/`](desktop/) — run it with
> `cargo tauri dev`. See [desktop/README.md](desktop/README.md).

---

## Install

```bash
git clone https://github.com/Matthias0x44/app-audit
cd app-audit
pip3 install -r requirements.txt
```

Optional — add a Claude API key to get AI-powered suggestions for apps not in the database:

```bash
cp .env.example .env
# edit .env and add your ANTHROPIC_API_KEY
```

---

## Commands

### Scan installed apps

Enumerates `/Applications`, Homebrew casks, and Mac App Store apps. Flags anything not opened in 180+ days (configurable).

```
python3 audit.py scan
python3 audit.py scan --days 90     # tighter threshold
python3 audit.py scan --all         # show every app, not just stale ones
```

### Full report with FOSS alternatives

```
python3 audit.py report
python3 audit.py report --ai        # use Claude to suggest alternatives for unlisted apps
```

### Cache sizes

Ranks `~/Library/Caches` by size. Useful before a fresh start.

```
python3 audit.py caches
python3 audit.py caches --top 10
```

### Clear caches

Deletes cache entries after a confirmation. Caches are disposable — apps rebuild
them on next launch. Apple/system caches are never touched.

```
python3 audit.py clean com.spotify.client     # clear one app's cache
python3 audit.py clean --orphaned             # clear caches with no matching app
python3 audit.py clean --orphaned --yes       # skip the confirmation prompt
```

### Category overlap

Finds categories where you have several apps doing the same job, and points at
the one you've most recently used as the likely keeper.

```
python3 audit.py overlap
```

### Subscriptions

Estimates monthly spend on installed apps using a curated public price list
(*not* your actual account), flagging anything you're likely paying for but no
longer using.

```
python3 audit.py subscriptions
python3 audit.py subscriptions --days 60      # stricter "unused" threshold
```

### Privacy grades

Shows a privacy grade computed **locally** from cited facts (open-source status,
tracker counts per Exodus Privacy, ToS;DR grade, business model, known
incidents). The grade is never our opinion — every point is shown with its
source. Grades also appear next to each entry in `alternatives`, turning the
neutral list into one ranked by third-party data.

```
python3 audit.py privacy              # grade every installed app, worst first
python3 audit.py privacy spotify      # full breakdown for one app
```

### Export the public dataset

Exports the curated privacy-contact database (every company, not just your
installed apps) as a timestamped CSV + JSON + markdown artifact — ready to hand
to journalists, regulators, or digital-rights groups. Companies are flagged as a
contact gap only when no email, DPO, *or* SAR web form is discoverable;
web-form-only firms are recorded separately rather than overstated as
non-compliant.

```
python3 audit.py export-dataset                 # writes to ./output/
python3 audit.py export-dataset -o ~/Desktop    # custom location
```

### Look up alternatives for a specific app

```
python3 audit.py alternatives spotify
python3 audit.py alternatives "microsoft office"
```

### Generate a GDPR email

Produces a legally-worded request pre-filled with the company's known privacy contact. Can open directly in your mail client.

```
python3 audit.py sar spotify erasure
python3 audit.py sar zoom access
python3 audit.py sar discord portability --name "Jane Smith" --email "jane@example.com"
python3 audit.py sar spotify erasure --output ~/Desktop/spotify_erasure.txt
```

Request types: `access` (Art. 15) · `erasure` (Art. 17) · `portability` (Art. 20)

### Non-compliant companies

Lists installed apps with no known privacy contact — potential GDPR Art. 37–39 breach.

```
python3 audit.py noncompliant
```

---

## Databases

Two plain JSON files power the tool — contributions welcome.

| File | Contents |
|---|---|
| `data/alternatives.json` | ~30 apps with FOSS/indie alternatives, license, platforms |
| `data/sar_contacts.json` | ~25 companies with privacy emails, deletion URLs, difficulty rating |
| `data/categories.json` | App categories (keyword lists) powering overlap detection |
| `data/subscriptions.json` | Indicative public subscription prices and cancel links |
| `data/privacy_scores.json` | Cited privacy attributes (trackers, ToS;DR grade, etc.) per app |

To add an entry, follow the existing format and open a PR.

---

## Philosophy

- No big tech backing — no skin in the game of pushing you toward a competitor
- Never recommends a specific alternative — only lists them
- Local-first: no data leaves your machine unless you choose to send a SAR email
- The non-compliance list is public data useful to regulators and digital rights groups

---

## Requirements

- macOS (Spotlight metadata via `mdls`)
- Python 3.8+
- `brew` and/or `mas` optional — detected automatically if installed
