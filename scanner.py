"""Enumerate installed macOS apps and their last-used dates."""

import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


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


# ---------------------------------------------------------------------------
# Steam support
# ---------------------------------------------------------------------------

_STEAM_PLAYTIMES: Optional[Dict[str, int]] = None


def _load_steam_playtimes() -> Dict[str, int]:
    """Parse Steam localconfig.vdf for last-played timestamps across all games.

    Uses the per-user config file rather than individual appmanifest files so
    it works for games not currently installed on this machine.
    """
    playtimes: Dict[str, int] = {}
    userdata = Path.home() / "Library" / "Application Support" / "Steam" / "userdata"
    if not userdata.exists():
        return playtimes

    for vdf_path in userdata.glob("*/config/localconfig.vdf"):
        try:
            current_id: Optional[str] = None
            in_apps = False
            depth = 0
            apps_depth: Optional[int] = None

            with open(vdf_path, errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if line == '"apps"':
                        in_apps = True
                        apps_depth = depth
                        continue
                    if line == "{":
                        depth += 1
                        continue
                    if line == "}":
                        depth -= 1
                        if in_apps and apps_depth is not None and depth <= apps_depth:
                            in_apps = False
                        continue
                    if not in_apps:
                        continue
                    if apps_depth is not None and depth == apps_depth + 1:
                        m = re.match(r'^"(\d+)"$', line)
                        if m:
                            current_id = m.group(1)
                    if current_id and '"LastPlayed"' in line:
                        m = re.search(r'"LastPlayed"\s+"(\d+)"', line)
                        if m:
                            ts = int(m.group(1))
                            if ts > 0:
                                playtimes[current_id] = max(playtimes.get(current_id, 0), ts)
        except Exception:
            pass

    return playtimes


def _steam_playtimes() -> Dict[str, int]:
    global _STEAM_PLAYTIMES
    if _STEAM_PLAYTIMES is None:
        _STEAM_PLAYTIMES = _load_steam_playtimes()
    return _STEAM_PLAYTIMES


def _get_steam_app_id(app_path: str) -> Optional[str]:
    """Extract Steam app ID from steam_appid.txt or a run.sh shortcut."""
    # Standard Steam game build
    txt = os.path.join(app_path, "Contents", "MacOS", "steam_appid.txt")
    if os.path.exists(txt):
        try:
            return open(txt).read().strip()
        except Exception:
            pass
    # Paradox / Steam shortcut style: run.sh contains "open steam://run/<ID>"
    run_sh = os.path.join(app_path, "Contents", "MacOS", "run.sh")
    if os.path.exists(run_sh):
        try:
            m = re.search(r"steam://run/(\d+)", open(run_sh).read())
            if m:
                return m.group(1)
        except Exception:
            pass
    return None


def _steam_last_played(app_path: str) -> Optional[datetime]:
    app_id = _get_steam_app_id(app_path)
    if not app_id:
        return None

    # localconfig.vdf has timestamps for all played games, installed or not
    ts = _steam_playtimes().get(app_id, 0)
    if ts > 0:
        return datetime.fromtimestamp(ts)

    # Fallback: appmanifest (only present when game is installed locally)
    manifest = (
        Path.home()
        / "Library" / "Application Support" / "Steam" / "steamapps"
        / f"appmanifest_{app_id}.acf"
    )
    if manifest.exists():
        try:
            for line in open(manifest):
                if '"LastPlayed"' in line:
                    parts = line.strip().split('"')
                    if len(parts) >= 4:
                        ts = int(parts[3])
                        if ts > 0:
                            return datetime.fromtimestamp(ts)
        except Exception:
            pass

    return None


# ---------------------------------------------------------------------------
# Application Support fuzzy scan
# ---------------------------------------------------------------------------

def _fuzzy_support_dirs(app_name: str) -> List[Path]:
    """Scan Application Support and Group Containers for dirs matching the app name.

    Catches mismatches like Anki -> Anki2, Unity games using company.gamename
    paths, and Microsoft apps stored under Group Containers.
    """
    lib = Path.home() / "Library"
    needle = app_name.lower().replace(" ", "").replace("-", "")
    matches: List[Path] = []

    for root in [lib / "Application Support", lib / "Group Containers"]:
        if not root.exists():
            continue
        try:
            for entry in root.iterdir():
                hay = entry.name.lower().replace(" ", "").replace("-", "").replace(".", "")
                if needle in hay or (len(needle) >= 4 and hay.startswith(needle[:5])):
                    matches.append(entry)
        except PermissionError:
            pass

    return matches


# ---------------------------------------------------------------------------
# Last-used resolution
# ---------------------------------------------------------------------------

def _get_last_used(
    path: str,
    bundle_id: Optional[str] = None,
    app_name: Optional[str] = None,
) -> Optional[datetime]:
    # 1. Spotlight — fast, but unreliable for sandboxed / Electron apps
    out = _run(["mdls", "-name", "kMDItemLastUsedDate", "-raw", path])
    if out and out != "(null)":
        try:
            return datetime.strptime(out[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass

    # 2. Steam — reads localconfig.vdf (covers uninstalled games too)
    steam_date = _steam_last_played(path)
    if steam_date:
        return steam_date

    # 3. Per-app data directories macOS touches on every launch
    lib = Path.home() / "Library"
    candidates: List[Path] = []

    if bundle_id:
        candidates += [
            lib / "Containers" / bundle_id,
            lib / "Saved Application State" / f"{bundle_id}.savedState",
            lib / "Application Support" / bundle_id,
            lib / "Preferences" / f"{bundle_id}.plist",
        ]

    if app_name:
        candidates += _fuzzy_support_dirs(app_name)

    return _latest_mtime(candidates) if candidates else None


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------

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
