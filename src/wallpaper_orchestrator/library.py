
"""Discover image files in wallpaper libraries."""

from pathlib import Path


SUPPORTED_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".bmp",
    ".webp",
}


def find_images(folder: Path) -> list[Path]:
    """Find supported images in a folder and its subfolders."""

    if not folder.is_dir():
        raise ValueError(f"Wallpaper folder does not exist: {folder}")

    images = [
        path
        for path in folder.rglob("*")
        if path.is_file()
        and path.suffix.lower() in SUPPORTED_EXTENSIONS
    ]

    return sorted(images)
