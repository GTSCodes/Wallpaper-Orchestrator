"""User-triggered support bundle: allowlisted diagnostics, never user state.

No wallpaper files, databases, JSON settings or monitor profiles are included.
Sensitive paths, addresses and plausible secret lines are removed from logs.
Nothing is uploaded automatically.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import zipfile

from wallpaper_orchestrator import __version__
from wallpaper_orchestrator.state_backup import data_root

_ALLOWED_LOGS = (
    "gui-startup-error.txt", "background-startup-error.txt",
    "self-test-startup-error.txt", "background-stop-startup-error.txt",
    "background-repair-startup-error.txt", "gui-shutdown-startup-error.txt",
    "backup-startup-error.txt", "background-launch.log",
)


def redact(text: str) -> str:
    """Prefer omitting context over leaking personal paths or credentials."""
    sanitized = []
    for line in text.splitlines():
        if re.search(r"(?i)(password|bearer|authorization|api[_-]?key|secret\s*[:=])", line):
            sanitized.append("<sensitive line omitted>")
            continue
        line = re.sub(r"(?i)[A-Z]:[\\/][^\r\n]+", "<local path>", line)
        line = re.sub(r"(?i)(?:/home/|/Users/)[^\s\r\n]+", "<local path>", line)
        line = re.sub(r"(?i)\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b", "<email>", line)
        sanitized.append(line)
    return "\n".join(sanitized) + "\n"


def export_diagnostics(destination: str | Path, *, root: Path | None = None) -> Path:
    """Create a local ZIP for inspection; never read a wallpaper or database."""
    source = Path(root) if root is not None else data_root()
    output = Path(destination)
    if output.suffix.lower() != ".zip":
        raise ValueError("Choose a .zip destination")
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(output.name + ".tmp")
    try:
        with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as archive:
            manifest = {
                "app_version": __version__,
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "system": platform.system(),
                "system_release": platform.release(),
                "note": "Only redacted, allowlisted startup logs. No settings, images, or database.",
            }
            archive.writestr("summary.json", json.dumps(manifest, indent=2))
            for name in _ALLOWED_LOGS:
                path = source / name
                if path.is_file() and not path.is_symlink():
                    text = path.read_text(encoding="utf-8", errors="replace")[-20000:]
                    archive.writestr("logs/" + name, redact(text))
        os.replace(temp, output)
        return output
    finally:
        temp.unlink(missing_ok=True)
