# app-audit

AI-assisted app audit tool for macOS. Find what to uninstall, get FOSS alternatives, clear caches, and send GDPR Subject Access / Erasure requests in one shot.

**macOS only · Python 3.8+ · No account required**

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
