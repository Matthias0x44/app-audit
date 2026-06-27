"""Scan, and optionally clear, ~/Library/Caches per app."""

import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Set, Tuple

CACHE_DIR = Path.home() / "Library" / "Caches"


def get_cache_sizes() -> Dict[str, int]:
    cache_dir = CACHE_DIR
    if not cache_dir.exists():
        return {}

    dirs = [str(p) for p in cache_dir.iterdir() if p.is_dir()]
    if not dirs:
        return {}

    try:
        result = subprocess.run(
            ["du", "-sk"] + dirs,
            capture_output=True, text=True, timeout=60
        )
        sizes: Dict[str, int] = {}
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("\t", 1)
            if len(parts) == 2:
                try:
                    size_bytes = int(parts[0]) * 1024
                    name = Path(parts[1]).name
                    if size_bytes > 0:
                        sizes[name] = size_bytes
                except ValueError:
                    pass
        return dict(sorted(sizes.items(), key=lambda x: x[1], reverse=True))
    except Exception:
        return {}


def format_size(size_bytes: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0  # type: ignore[assignment]
    return f"{size_bytes:.1f} TB"


# Vendor prefixes whose caches belong to the OS or system services and must
# never be flagged as orphaned, even though they have no app in /Applications.
_SYSTEM_VENDOR_PREFIXES = (
    "com.apple",
    "com.google.softwareupdate",
    "com.microsoft.autoupdate",
    "org.swift",
)


def _vendor_prefix(bundle_id: str) -> str:
    """First two reverse-DNS components, e.g. com.spotify.client -> com.spotify."""
    parts = bundle_id.lower().split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else bundle_id.lower()


def find_orphaned_caches(installed_bundle_ids: Set[str]) -> Dict[str, int]:
    """Cache dirs that look like a bundle ID but have no matching installed app.

    Deliberately conservative — a destructive operation should under-delete:
      - Only dotted, reverse-DNS-style names (com.foo.bar) are considered, so a
        generic folder like 'pip' or 'Steam' is never touched.
      - Apple and known system-updater vendors are always skipped.
      - A cache is kept (not orphaned) if any installed app shares its full ID
        (prefix match, so com.x.app.ShipIt matches installed com.x.app) OR its
        vendor prefix (so a helper cache from an installed vendor is spared).
    """
    sizes = get_cache_sizes()
    installed_ids = {b.lower() for b in installed_bundle_ids if b}
    installed_vendors = {_vendor_prefix(b) for b in installed_ids}
    orphaned: Dict[str, int] = {}

    for name, size in sizes.items():
        lname = name.lower()
        if lname.count(".") < 2 or " " in name:
            continue
        if any(lname.startswith(p) for p in _SYSTEM_VENDOR_PREFIXES):
            continue

        # Spared if an installed app's ID is a prefix of this cache (or vice
        # versa) — covers .ShipIt updater and helper suffixes.
        id_match = any(
            lname == iid or lname.startswith(iid + ".") or iid.startswith(lname + ".")
            for iid in installed_ids
        )
        if id_match:
            continue

        # Spared if the vendor (com.spotify) matches any installed app's vendor.
        if _vendor_prefix(lname) in installed_vendors:
            continue

        orphaned[name] = size
    return orphaned


def _resolve_cache_path(name: str) -> Path:
    """Resolve a cache entry name to an absolute path, refusing escapes."""
    target = (CACHE_DIR / name).resolve()
    # Refuse anything that resolves outside ~/Library/Caches (path traversal guard)
    if CACHE_DIR.resolve() not in target.parents and target != CACHE_DIR.resolve():
        raise ValueError(f"Refusing to operate outside {CACHE_DIR}: {name}")
    return target


def clear_cache(name: str) -> Tuple[bool, int, str]:
    """Delete a single cache entry. Returns (success, bytes_freed, message)."""
    sizes = get_cache_sizes()
    freed = sizes.get(name, 0)
    try:
        target = _resolve_cache_path(name)
    except ValueError as exc:
        return False, 0, str(exc)

    if not target.exists():
        return False, 0, "not found"

    try:
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink()
        return True, freed, "cleared"
    except PermissionError:
        return False, 0, "permission denied (app may be running)"
    except OSError as exc:
        return False, 0, str(exc)


def clear_caches(names: List[str]) -> Tuple[int, int, List[str]]:
    """Clear multiple cache entries. Returns (cleared_count, bytes_freed, errors)."""
    cleared = 0
    freed = 0
    errors: List[str] = []
    for name in names:
        ok, bytes_freed, msg = clear_cache(name)
        if ok:
            cleared += 1
            freed += bytes_freed
        else:
            errors.append(f"{name}: {msg}")
    return cleared, freed, errors
