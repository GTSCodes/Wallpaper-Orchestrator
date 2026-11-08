
"""Command-line interface for Wallpaper Orchestrator."""

import argparse

from wallpaper_orchestrator import __version__

from pathlib import Path

from wallpaper_orchestrator.library import find_images


def main(argv: list[str] | None = None) -> int:
    """Run the Wallpaper Orchestrator CLI."""

    parser = argparse.ArgumentParser(
        prog="wallpaper-orchestrator",
        description="A configurable multi-monitor wallpaper manager.",
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    
    subparsers = parser.add_subparsers(dest="command")

    scan_parser = subparsers.add_parser(
        "scan",
        help="Find images in a wallpaper folder.",
    )

    scan_parser.add_argument(
        "folder",
        type=Path,
        help="Path to the wallpaper folder.",
    )


    
    args = parser.parse_args(argv)

    if args.command == "scan":
        try:
            images = find_images(args.folder)
        except ValueError as error:
            parser.error(str(error))

        print(f"Found {len(images)} wallpaper(s):")

        for image in images:
            print(f"  {image.relative_to(args.folder)}")

        return 0


    print("Wallpaper Orchestrator is under development.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
