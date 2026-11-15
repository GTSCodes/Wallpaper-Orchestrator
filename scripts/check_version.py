"""Enforce one visible version in Python, Windows installer and release tags."""

import argparse
from pathlib import Path
import re
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def check_version(root: Path, *, tag: str | None = None) -> str:
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    version = metadata["project"]["version"]
    package = (root / "src/wallpaper_orchestrator/__init__.py").read_text(encoding="utf-8")
    setup = (root / "installer/WallpaperOrchestrator.iss").read_text(encoding="utf-8")
    pkg = re.search(r'^__version__\s*=\s*"([^"]+)"', package, re.MULTILINE)
    iss = re.search(r'^#define MyAppVersion "([^"]+)"', setup, re.MULTILINE)
    if not pkg or not iss or pkg.group(1) != version or iss.group(1) != version:
        raise ValueError("pyproject, Python package and Inno Setup versions differ")
    if tag is not None and tag != "v" + version:
        raise ValueError(f"Tag {tag!r} does not match v{version}")
    return version


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", help="Validate a release tag, e.g. v0.8.0b3")
    args = parser.parse_args()
    try:
        print(check_version(ROOT, tag=args.tag))
        return 0
    except (ValueError, KeyError, OSError) as error:
        print(f"Version check failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
