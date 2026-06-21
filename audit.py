#!/usr/bin/env python3
"""app-audit: find what to uninstall, get FOSS alternatives, send GDPR requests."""

import json
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

app = typer.Typer(
    name="audit",
    help="app-audit: find what to uninstall, get FOSS alternatives, send GDPR requests.",
    no_args_is_help=True,
)
console = Console()

DATA_DIR = Path(__file__).parent / "data"
ALTERNATIVES_DB = DATA_DIR / "alternatives.json"
SAR_CONTACTS_DB = DATA_DIR / "sar_contacts.json"


def _load(path: Path) -> dict:
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def _match(name: str, db: dict) -> Optional[str]:
    """Fuzzy-match an app name against a database keyed by lowercase slugs."""
    needle = name.lower().replace(" ", "").replace("-", "").replace(".", "")
    for key in db:
        haystack = key.lower().replace(" ", "").replace("-", "").replace(".", "")
        if needle == haystack or needle in haystack or haystack in needle:
            return key
    return None


def _relative_time(dt: datetime) -> str:
    days = (datetime.now() - dt).days
    if days == 0:
        return "Today"
    if days == 1:
        return "Yesterday"
    if days < 30:
        return f"{days}d ago"
    if days < 365:
        return f"{days // 30}mo ago"
    return f"{days // 365}y ago"


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------

@app.command()
def scan(
    days: int = typer.Option(180, "--days", "-d", help="Stale threshold in days"),
    all_apps: bool = typer.Option(False, "--all", "-a", help="Show all apps, not just stale"),
):
    """List installed apps, flag stale ones, show if FOSS alternatives exist."""
    from scanner import scan_all

    apps = scan_all()
    alts_db = _load(ALTERNATIVES_DB)
    threshold = timedelta(days=days)
    now = datetime.now()

    table = Table(
        title=f"Installed Apps — {'all' if all_apps else f'stale (>{days}d) or never used'}",
        box=box.ROUNDED,
        show_lines=True,
    )
    table.add_column("App", style="bold", min_width=22)
    table.add_column("Source", style="dim", width=13)
    table.add_column("Last Used", width=14)
    table.add_column("Status", width=14)
    table.add_column("Alternative?", width=13)

    stale_count = 0

    for a in apps:
        never_used = a.last_used is None and a.source == "applications"
        is_stale = never_used or (a.last_used and (now - a.last_used) > threshold)

        if not all_apps and not is_stale:
            continue

        if is_stale:
            stale_count += 1

        used_str = (
            "[dim]Never[/dim]" if never_used
            else "[dim]Unknown[/dim]" if a.last_used is None
            else _relative_time(a.last_used)
        )
        status = (
            "[red]Never used[/red]" if never_used
            else "[yellow]Stale[/yellow]" if is_stale
            else "[green]Active[/green]"
        )
        has_alt = "[green]Yes[/green]" if _match(a.name, alts_db) else "[dim]Unknown[/dim]"

        table.add_row(a.name, a.source, used_str, status, has_alt)

    console.print(table)
    console.print(
        f"\n[bold]{stale_count}[/bold] stale/unused apps found. "
        "Run [bold]audit report[/bold] for alternatives."
    )


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

@app.command()
def report(
    days: int = typer.Option(180, "--days", "-d", help="Stale threshold in days"),
    ai: bool = typer.Option(False, "--ai", help="Use Claude to suggest alternatives for unknown apps"),
):
    """Full audit report with FOSS alternatives for every stale app."""
    from scanner import scan_all

    apps = scan_all()
    alts_db = _load(ALTERNATIVES_DB)
    threshold = timedelta(days=days)
    now = datetime.now()

    stale = [
        a for a in apps
        if (a.last_used is None and a.source == "applications")
        or (a.last_used and (now - a.last_used) > threshold)
    ]

    console.print(Panel(
        f"[bold]App Audit Report[/bold]  ·  {now.strftime('%Y-%m-%d')}\n"
        f"Total apps scanned: [bold]{len(apps)}[/bold]  |  "
        f"Stale / unused: [bold red]{len(stale)}[/bold red]",
        expand=False,
    ))

    if not stale:
        console.print("[green]No stale apps found.[/green]")
        return

    ai_data: dict = {}
    if ai:
        unknown = [a for a in stale if not _match(a.name, alts_db)]
        if unknown:
            ai_data = _ai_categorize(unknown)

    table = Table(
        title=f"Stale Apps & FOSS Alternatives  (threshold: {days} days)",
        box=box.ROUNDED,
        show_lines=True,
        min_width=90,
    )
    table.add_column("App", style="bold", min_width=20)
    table.add_column("Last Used", width=14)
    table.add_column("FOSS Alternatives", min_width=50)

    for a in sorted(stale, key=lambda x: x.name.lower()):
        used_str = (
            "[dim]Never[/dim]" if a.last_used is None
            else _relative_time(a.last_used)
        )

        key = _match(a.name, alts_db)
        if key:
            alts = alts_db[key].get("alternatives", [])[:3]
            lines = []
            for alt in alts:
                tag = "[green]free[/green]" if alt.get("free") else "[yellow]freemium[/yellow]"
                desc = alt.get("description", "")[:55]
                lines.append(f"• {alt['name']} ({tag}) — {desc}")
            alt_text = "\n".join(lines) if lines else "[dim]None listed[/dim]"
        elif a.name in ai_data:
            alt_text = f"[dim italic](AI)[/dim italic] {ai_data[a.name].get('alternatives', '')}"
        else:
            alt_text = "[dim]Not in database — run with --ai for suggestions[/dim]"

        table.add_row(a.name, used_str, alt_text)

    console.print(table)
    console.print(
        "\nGenerate a GDPR deletion request: "
        "[bold]audit sar <app_name> erasure[/bold]"
    )


# ---------------------------------------------------------------------------
# caches
# ---------------------------------------------------------------------------

@app.command()
def caches(top: int = typer.Option(25, "--top", "-n", help="Number of entries to show")):
    """Show cache sizes by app, largest first."""
    from caches import format_size, get_cache_sizes

    console.print("[dim]Scanning ~/Library/Caches…[/dim]")
    sizes = get_cache_sizes()

    if not sizes:
        console.print("No caches found.")
        return

    total = sum(sizes.values())
    table = Table(
        title=f"Cache Sizes  (total: {format_size(total)})",
        box=box.ROUNDED,
    )
    table.add_column("App / Bundle ID", style="bold", min_width=44)
    table.add_column("Size", justify="right", width=10)

    for name, size in list(sizes.items())[:top]:
        table.add_row(name, format_size(size))

    console.print(table)
    console.print("[dim]Clear a cache:  rm -rf ~/Library/Caches/<name>[/dim]")


# ---------------------------------------------------------------------------
# alternatives
# ---------------------------------------------------------------------------

@app.command()
def alternatives(app_name: str = typer.Argument(..., help="App name to look up")):
    """Show FOSS alternatives for a specific app."""
    alts_db = _load(ALTERNATIVES_DB)
    key = _match(app_name, alts_db)

    if not key:
        console.print(f"[yellow]No entry for '{app_name}' in database.[/yellow]")
        console.print("Try [bold]audit report --ai[/bold] to use Claude for unlisted apps.")
        return

    entry = alts_db[key]
    console.print(Panel(
        f"[bold]{entry.get('display_name', key)}[/bold]\n"
        f"Category: {entry.get('category', '—')}  ·  "
        f"Big Tech: {'[red]Yes[/red]' if entry.get('big_tech') else '[green]No[/green]'}",
        expand=False,
    ))

    alts = entry.get("alternatives", [])
    if not alts:
        console.print("[dim]No alternatives listed.[/dim]")
        return

    table = Table(box=box.ROUNDED, show_lines=True)
    table.add_column("Name", style="bold", width=18)
    table.add_column("Type", width=13)
    table.add_column("License", width=14)
    table.add_column("Platforms", min_width=28)
    table.add_column("Free", width=6)
    table.add_column("Description")

    for alt in alts:
        table.add_row(
            alt["name"],
            alt.get("type", ""),
            alt.get("license", ""),
            ", ".join(alt.get("platforms", [])),
            "[green]Yes[/green]" if alt.get("free") else "[yellow]No[/yellow]",
            alt.get("description", ""),
        )

    console.print(table)


# ---------------------------------------------------------------------------
# sar
# ---------------------------------------------------------------------------

@app.command()
def sar(
    app_name: str = typer.Argument(..., help="App / company name"),
    request_type: str = typer.Argument("access", help="access | erasure | portability"),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Your full name"),
    email: Optional[str] = typer.Option(None, "--email", "-e", help="Your account email"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Save email to file"),
):
    """Generate a GDPR Subject Access Request or Right to Erasure email."""
    from sar import SAR_TYPES, generate_sar_email

    if request_type not in SAR_TYPES:
        console.print(f"[red]Unknown type '{request_type}'. Use: access, erasure, portability[/red]")
        raise typer.Exit(1)

    sar_db = _load(SAR_CONTACTS_DB)
    key = _match(app_name, sar_db)

    if key:
        contact = sar_db[key]
        company_name = contact.get("display_name", key)
        privacy_email = contact.get("privacy_email", "")
        dpo_email = contact.get("dpo_email", "")
        deletion_url = contact.get("deletion_url", "")
        difficulty = contact.get("deletion_difficulty", "unknown")
    else:
        company_name = app_name
        privacy_email = dpo_email = deletion_url = ""
        difficulty = "unknown"
        console.print(
            f"[yellow]'{app_name}' not in database — you'll need to find the privacy "
            "contact manually (usually /privacy or /legal on their website).[/yellow]"
        )

    if not name:
        name = Prompt.ask("Your full name")
    if not email:
        email = Prompt.ask("Your account email address")

    to_email = privacy_email or dpo_email
    body = generate_sar_email(company_name, name, email, request_type, to_email)

    info_lines = [f"[bold]GDPR {request_type.title()} Request — {company_name}[/bold]"]
    if to_email:
        info_lines.append(f"To: {to_email}")
    if deletion_url:
        info_lines.append(f"Self-service URL: {deletion_url}")
    if difficulty != "unknown":
        colour = {"easy": "green", "medium": "yellow", "hard": "red"}.get(difficulty, "dim")
        info_lines.append(f"Deletion difficulty: [{colour}]{difficulty}[/{colour}]")

    console.print(Panel("\n".join(info_lines), expand=False))
    console.print(Panel(body, title="Generated Email", expand=False))

    if output:
        output.write_text(body)
        console.print(f"[green]Saved to {output}[/green]")

    if to_email and Confirm.ask("Open in default mail client?"):
        subj = f"GDPR {request_type.title()} Request".replace(" ", "%20")
        subprocess.run(["open", f"mailto:{to_email}?subject={subj}"])


# ---------------------------------------------------------------------------
# noncompliant
# ---------------------------------------------------------------------------

@app.command()
def noncompliant():
    """List installed apps confirmed non-compliant or absent from the privacy database."""
    from scanner import scan_all

    apps = scan_all()
    sar_db = _load(SAR_CONTACTS_DB)
    alts_db = _load(ALTERNATIVES_DB)

    table = Table(
        title="Apps with Missing or Unknown Privacy Contact",
        box=box.ROUNDED,
        caption="Potential GDPR Art. 37–39 non-compliance · Export: audit noncompliant > report.txt",
    )
    table.add_column("App", style="bold", min_width=22)
    table.add_column("Source", width=13)
    table.add_column("Compliance", width=22)
    table.add_column("FOSS Alt?", width=10)

    issues = []
    for a in apps:
        key = _match(a.name, sar_db)
        if key:
            if not sar_db[key].get("compliant", True):
                issues.append((a, "confirmed", "Known non-compliant"))
        else:
            issues.append((a, "unknown", "No data"))

    for a, severity, label in sorted(issues, key=lambda x: (x[1], x[0].name.lower())):
        colour = "red" if severity == "confirmed" else "yellow"
        has_alt = "[green]Yes[/green]" if _match(a.name, alts_db) else "[dim]—[/dim]"
        table.add_row(a.name, a.source, f"[{colour}]{label}[/{colour}]", has_alt)

    console.print(table)
    console.print(
        f"\n[bold]{len(issues)}[/bold] apps flagged. "
        "[dim]Contribute missing contacts: github.com/Matthias0x44/app-audit[/dim]"
    )


# ---------------------------------------------------------------------------
# AI helper
# ---------------------------------------------------------------------------

def _ai_categorize(apps) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        console.print("[yellow]ANTHROPIC_API_KEY not set — skipping AI step.[/yellow]")
        return {}

    try:
        import anthropic
    except ImportError:
        console.print("[yellow]Run: pip install anthropic[/yellow]")
        return {}

    names = [a.name for a in apps]
    prompt = (
        "You help users find free/open-source alternatives to macOS apps.\n"
        "For each app listed, provide: what it does (one line) and 1–3 FOSS alternatives "
        "(name + one-line description each). Skip any you don't recognise.\n\n"
        f"Apps: {json.dumps(names)}\n\n"
        "Respond with JSON only:\n"
        '{"AppName": {"description": "...", "alternatives": "Alt1 (desc), Alt2 (desc)"}}'
    )

    console.print("[dim]Asking Claude for alternatives…[/dim]")
    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text
        start, end = text.find("{"), text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
    except Exception as exc:
        console.print(f"[yellow]AI step failed: {exc}[/yellow]")

    return {}


if __name__ == "__main__":
    app()
