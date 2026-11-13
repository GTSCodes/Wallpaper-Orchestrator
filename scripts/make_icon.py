"""Generate a simple multi-monitor Windows icon from vector-like Pillow shapes."""

from pathlib import Path
import sys

from PIL import Image, ImageDraw


def render_icon(path: Path) -> None:
    size = 256
    icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    painter = ImageDraw.Draw(icon)
    painter.rounded_rectangle((16, 45, 165, 170), radius=24,
                              fill="#2773ac", outline="#dceaff", width=10)
    painter.rounded_rectangle((95, 93, 239, 208), radius=19,
                              fill="#12314e", outline="#dceaff", width=10)
    painter.line((82, 171, 82, 199), fill="#dceaff", width=12)
    painter.line((47, 205, 115, 205), fill="#dceaff", width=12)
    path.parent.mkdir(parents=True, exist_ok=True)
    icon.save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                           (64, 64), (128, 128), (256, 256)])


if __name__ == "__main__":
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("assets/wallpaper-orchestrator.ico")
    render_icon(output)
