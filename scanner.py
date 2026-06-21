"""Enumerate installed macOS apps and their last-used dates."""

import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional


@dataclass
class AppInfo:
    name: str
    path: str
    bundle_id: Optional[str] = None
    last_used: Optional[datetime] = None
    source: str = "applications"
    mas_id: Optional[str] = None

    @property
    def days_since_used(self) -> Optional[int]:
        if self.last_used is None:
            return None
        return (datetime.now() - self.last_used).days


def _run(cmd: list, timeout: int = 10) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


def _get_bundle_id(path: str) -> Optional[str]:
    out = _run(["mdls", "-name", "kMDItemCFBundleIdentifier", "-raw", path])
    if out and out != "(null)":
        return out
    # Spotlight sometimes doesn't index this; read Info.plist directly as fallback
    plist = os.path.join(path, "Contents", "Info.plist")
    if os.path.exists(plist):
        out = _run(["/usr/libexec/PlistBuddy", "-c", "Print :CFBundleIdentifier", plist])
        if out and not out.startswith("Print:") and not out.startswith("Error"):
            return out
    return None


def _latest_mtime(paths: list) -> Optional[datetime]:
    dates = []
    for p in paths:
        try:
            if p.exists():
                dates.append(datetime.fromtimestamp(p.stat().st_mtime))
        except Exception:
            pass
    return max(dates) if dates else None


def _get_last_used(
    path: str,
    bundle_id: Optional[str] = None,
    app_name: Optional[str] = None,
) -> Optional[datetime]:
    # Primary: Spotlight metadata on the .app bundle
    out = _run(["mdls", "-name", "kMDItemLastUsedDate", "-raw", path])
    if out and out != "(null)":
        try:
            return datetime.strptime(out[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass

    # Fallback: per-app data directories macOS updates on every launch.
    # Sandboxed (App Store) apps don't reliably update kMDItemLastUsedDate,
    # but their Containers / SavedState / Preferences entries always get touched.
    # Electron apps (e.g. Claude, Slack) often use the app name rather than
    # the bundle ID as their Application Support folder name.
    lib = Path.home() / "Library"
    candidates = []

    if bundle_id:
        candidates += [
            lib / "Containers" / bundle_id,
            lib / "Saved Application State" / f"{bundle_id}.savedState",
            lib / "Application Support" / bundle_id,
            lib / "Preferences" / f"{bundle_id}.plist",
        ]

    if app_name:
        candidates += [
            lib / "Application Support" / app_name,
        ]

    return _latest_mtime(candidates) if candidates else None


def scan_applications() -> List[AppInfo]:
    apps = []
    for base in ["/Applications", str(Path.home() / "Applications")]:
        if not os.path.isdir(base):
            continue
        for entry in sorted(os.listdir(base)):
            if not entry.endswith(".app"):
                continue
            path = os.path.join(base, entry)
            app = AppInfo(name=entry[:-4], path=path, source="applications")
            app.bundle_id = _get_bundle_id(path)
            app.last_used = _get_last_used(path, app.bundle_id, entry[:-4])
            apps.append(app)
    return apps


def scan_homebrew() -> List[AppInfo]:
    out = _run(["brew", "list", "--cask"])
    if not out:
        return []
    return [AppInfo(name=line, path="", source="homebrew") for line in out.split("\n") if line.strip()]


def scan_mas() -> List[AppInfo]:
    out = _run(["mas", "list"])
    if not out:
        return []
    apps = []
    for line in out.split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split(" ", 1)
        if len(parts) == 2:
            mas_id, rest = parts
            name = rest.split(" (")[0] if " (" in rest else rest
            apps.append(AppInfo(name=name, path="", source="mas", mas_id=mas_id))
    return apps


def scan_all() -> List[AppInfo]:
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, TextColumn

    console = Console(stderr=True)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Scanning /Applications...", total=None)
        apps = scan_applications()

        progress.update(task, description="Checking Homebrew casks...")
        brew_apps = scan_homebrew()

        progress.update(task, description="Checking Mac App Store...")
        mas_apps = scan_mas()

    seen: dict = {}
    for a in apps + brew_apps + mas_apps:
        key = a.name.lower()
        if key not in seen or a.source == "applications":
            seen[key] = a

    return sorted(seen.values(), key=lambda x: x.name.lower())
