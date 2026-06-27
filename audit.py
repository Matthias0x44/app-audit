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
CATEGORIES_DB = DATA_DIR / "categories.json"
SUBSCRIPTIONS_DB = DATA_DIR / "subscriptions.json"


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
# overlap
# ---------------------------------------------------------------------------

def _categorize(app_name: str, categories: dict) -> Optional[str]:
    """Return the category whose keyword list matches this app name, if any."""
    name = app_name.lower()
    for category, keywords in categories.items():
        for kw in keywords:
            if kw in name or name in kw:
                return category
    return None


@app.command()
def overlap():
    """Find categories where you have multiple apps doing the same job."""
    from scanner import scan_all

    apps = scan_all()
    categories = _load(CATEGORIES_DB)

    buckets: dict = {}
    for a in apps:
        cat = _categorize(a.name, categories)
        if cat:
            buckets.setdefault(cat, []).append(a)

    overlaps = {c: items for c, items in buckets.items() if len(items) > 1}

    if not overlaps:
        console.print("[green]No category overlaps found — no redundant apps detected.[/green]")
        return

    console.print(Panel(
        f"[bold]Category Overlap[/bold]  ·  "
        f"{len(overlaps)} categories with more than one app installed",
        expand=False,
    ))

    for cat, items in sorted(overlaps.items(), key=lambda x: -len(x[1])):
        # The app used most recently is the one you've likely settled on.
        def sort_key(a):
            return a.last_used or datetime.min
        items_sorted = sorted(items, key=sort_key, reverse=True)
        settled = items_sorted[0]

        table = Table(
            title=f"{cat}  ({len(items)} apps)",
            box=box.SIMPLE_HEAD,
            title_justify="left",
            title_style="bold yellow",
        )
        table.add_column("App", style="bold", min_width=22)
        table.add_column("Last Used", width=14)
        table.add_column("", width=24)

        for a in items_sorted:
            used = (
                "[dim]Never[/dim]" if a.last_used is None
                else _relative_time(a.last_used)
            )
            if a is settled and a.last_used is not None:
                note = "[green]← likely your main one[/green]"
            elif a.last_used is None:
                note = "[red]never opened[/red]"
            else:
                note = "[dim]candidate to remove[/dim]"
            table.add_row(a.name, used, note)

        console.print(table)

    console.print(
        "[dim]Tip: keep the one you've settled on, audit the rest with "
        "[bold]audit alternatives <app>[/bold] or [bold]audit sar <app> erasure[/bold].[/dim]"
    )


# ---------------------------------------------------------------------------
# subscriptions
# ---------------------------------------------------------------------------

@app.command()
def subscriptions(
    days: int = typer.Option(90, "--days", "-d", help="Flag subs unused beyond this many days"),
):
    """Estimate what you're paying for installed apps, flagging unused ones."""
    from scanner import scan_all

    apps = scan_all()
    subs_db = _load(SUBSCRIPTIONS_DB)
    meta = subs_db.get("_meta", {})
    now = datetime.now()

    rows = []
    monthly_total = 0.0
    wasted_total = 0.0

    for a in apps:
        key = _match(a.name, subs_db)
        if not key or key == "_meta":
            continue
        sub = subs_db[key]
        price = sub.get("price", 0.0)
        monthly_total += price

        days_idle = (
            None if a.last_used is None
            else (now - a.last_used).days
        )
        unused = a.last_used is None or (days_idle is not None and days_idle > days)
        if unused:
            wasted_total += price

        rows.append((a, sub, price, days_idle, unused))

    if not rows:
        console.print(
            "[yellow]No known-subscription apps found among installed apps.[/yellow]\n"
            "[dim]This checks a curated price list, not your actual accounts.[/dim]"
        )
        return

    rows.sort(key=lambda r: (not r[4], -r[2]))  # unused first, then by price desc

    table = Table(
        title="Likely Subscriptions  (indicative prices, not from your account)",
        box=box.ROUNDED,
        show_lines=True,
        caption=meta.get("note", ""),
        caption_justify="left",
    )
    table.add_column("App", style="bold", min_width=20)
    table.add_column("Est. /mo", justify="right", width=9)
    table.add_column("Last Used", width=14)
    table.add_column("Verdict", min_width=28)

    for a, sub, price, days_idle, unused in rows:
        used = (
            "[dim]Never[/dim]" if a.last_used is None
            else _relative_time(a.last_used)
        )
        if unused:
            free = " — has free tier" if sub.get("has_free_tier") else ""
            verdict = f"[red]Paying but unused{free}[/red]"
        else:
            verdict = "[green]Actively used[/green]"
        table.add_row(
            sub.get("display_name", a.name),
            f"${price:.2f}",
            used,
            verdict,
        )

    console.print(table)
    console.print(
        f"\nEstimated total: [bold]${monthly_total:.2f}/mo[/bold]  "
        f"(${monthly_total * 12:.0f}/yr)"
    )
    if wasted_total > 0:
        console.print(
            f"Potentially wasted on unused apps: "
            f"[bold red]${wasted_total:.2f}/mo[/bold red]  "
            f"(${wasted_total * 12:.0f}/yr)"
        )
    console.print(
        "[dim]Cancel links and notes: [bold]audit subscriptions[/bold] entries come from "
        "data/subscriptions.json — verify against your real plan before cancelling.[/dim]"
    )


# ---------------------------------------------------------------------------
# clean
# ---------------------------------------------------------------------------

@app.command()
def clean(
    name: Optional[str] = typer.Argument(None, help="Specific cache to clear (omit for interactive)"),
    orphaned: bool = typer.Option(False, "--orphaned", help="Clear caches with no matching installed app"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation (use with care)"),
):
    """Clear caches in ~/Library/Caches. Asks before deleting anything."""
    from caches import clear_caches, find_orphaned_caches, format_size, get_cache_sizes

    sizes = get_cache_sizes()
    if not sizes:
        console.print("No caches found.")
        return

    # Decide the target set of cache entries to clear.
    if name:
        key = name if name in sizes else None
        if not key:
            # case-insensitive / partial match
            matches = [k for k in sizes if name.lower() in k.lower()]
            if len(matches) == 1:
                key = matches[0]
            elif len(matches) > 1:
                console.print(f"[yellow]'{name}' matches {len(matches)} entries:[/yellow]")
                for m in matches[:15]:
                    console.print(f"  • {m} ({format_size(sizes[m])})")
                console.print("Be more specific.")
                raise typer.Exit(1)
        if not key:
            console.print(f"[yellow]No cache entry matching '{name}'.[/yellow]")
            raise typer.Exit(1)
        targets = {key: sizes[key]}

    elif orphaned:
        from scanner import scan_all
        bundle_ids = {a.bundle_id for a in scan_all() if a.bundle_id}
        targets = find_orphaned_caches(bundle_ids)
        if not targets:
            console.print("[green]No orphaned caches found.[/green]")
            return
        console.print(
            f"[bold]{len(targets)}[/bold] cache(s) with no matching app bundle. "
            "[dim]Caches are disposable — apps rebuild them on next launch. "
            "Steam/launcher games may appear here since their shortcuts carry no "
            "bundle ID.[/dim]"
        )

    else:
        # Interactive: show the top entries and let the user pick by clearing all
        console.print("Specify a cache name, or use [bold]--orphaned[/bold] to clear "
                      "caches with no matching app.\n")
        table = Table(box=box.SIMPLE)
        table.add_column("Cache", style="bold")
        table.add_column("Size", justify="right")
        for n, s in list(sizes.items())[:15]:
            table.add_row(n, format_size(s))
        console.print(table)
        console.print("[dim]Example: audit clean com.spotify.client[/dim]")
        return

    total = sum(targets.values())
    table = Table(box=box.SIMPLE)
    table.add_column("Will clear", style="bold")
    table.add_column("Size", justify="right")
    for n, s in sorted(targets.items(), key=lambda x: -x[1]):
        table.add_row(n, format_size(s))
    console.print(table)
    console.print(f"Total to free: [bold]{format_size(total)}[/bold]\n")

    if not yes and not Confirm.ask(
        f"[red]Delete {len(targets)} cache item(s)?[/red] (apps will rebuild caches as needed)"
    ):
        console.print("Aborted.")
        return

    cleared, freed, errors = clear_caches(list(targets.keys()))
    console.print(f"[green]Cleared {cleared} item(s), freed {format_size(freed)}.[/green]")
    for err in errors:
        console.print(f"[yellow]  skipped {err}[/yellow]")


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
