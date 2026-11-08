
"""Command-line interface for Wallpaper Orchestrator."""

import argparse

from wallpaper_orchestrator import __version__

from pathlib import Path

from wallpaper_orchestrator.library import find_images, get_image_orientation, infer_theme


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
            try:
                width, height, orientation = get_image_orientation(image)
            except OSError as error:
                print(f"  Could not read {image.name}: {error}")
                continue

            relative_path = image.relative_to(args.folder)

            theme = infer_theme(image, args.folder)

            
            print(
                f"  [{orientation:9}] "
                f"{width}x{height} "
                f"[{theme}] "
                f"{relative_path}"
            )



        return 0


    print("Wallpaper Orchestrator is under development.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
