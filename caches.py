"""Scan ~/Library/Caches and report sizes per app."""

import subprocess
from pathlib import Path
from typing import Dict


def get_cache_sizes() -> Dict[str, int]:
    cache_dir = Path.home() / "Library" / "Caches"
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
