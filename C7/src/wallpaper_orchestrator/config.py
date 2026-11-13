"""Load user configuration and resolve wallpaper themes."""

import json
import os
from pathlib import Path


def get_config_path() -> Path:
    """Return the user's configuration file path."""

    return (
        Path(os.environ["LOCALAPPDATA"])
        / "WallpaperOrchestrator"
        / "config.json"
    )


def load_config() -> dict:
    """Load user preferences from JSON."""

    path = get_config_path()

    if not path.exists():
        return {}

    data = json.loads(path.read_text(encoding="utf-8"))

    if not isinstance(data, dict):
        raise ValueError("Configuration must be a JSON object.")

    return data


def resolve_themes(
    image_path: Path,
    library_root: Path,
    config: dict,
) -> frozenset[str]:
    """Resolve image themes using configuration and folder fallback."""

    relative = image_path.relative_to(library_root)
    image_key = relative.as_posix()
    folder_key = relative.parent.as_posix()

    image_overrides = config.get("image_themes", {})
    folder_overrides = config.get("folder_themes", {})

    if image_key in image_overrides:
        themes = image_overrides[image_key]

    elif folder_key in folder_overrides:
        themes = folder_overrides[folder_key]

    elif folder_key != ".":
        themes = [relative.parent.name]

    else:
        themes = ["untagged"]

    if not isinstance(themes, list) or not all(
        isinstance(theme, str) and theme.strip()
        for theme in themes
    ):
        raise ValueError(
            f"Invalid themes configured for {image_key}"
        )

    return frozenset(themes)
