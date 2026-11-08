
"""Discover image files in wallpaper libraries."""

from pathlib import Path

from PIL import Image


SUPPORTED_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".bmp",
    ".webp",
}


def get_image_orientation(path: Path) -> tuple[int, int, str]:
    """Return an image's width, height and orientation."""

    with Image.open(path) as image:
        width, height = image.size

    if width > height:
        orientation = "landscape"
    elif height > width:
        orientation = "portrait"
    else:
        orientation = "square"

    return width, height, orientation


def infer_theme(image_path: Path, library_root: Path) -> str:
    """Infer an image's theme from its containing folder."""

    relative_path = image_path.relative_to(library_root)

    if len(relative_path.parts) == 1:
        return "untagged"

    return relative_path.parent.name



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
