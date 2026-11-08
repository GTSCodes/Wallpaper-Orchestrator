
"""Command-line interface for Wallpaper Orchestrator."""

import argparse, sys

from wallpaper_orchestrator import __version__

from pathlib import Path

from wallpaper_orchestrator.library import find_images, get_image_orientation, infer_theme, load_wallpapers

from wallpaper_orchestrator.models import Monitor

from wallpaper_orchestrator.selector import choose_wallpaper


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

    
    suggest_parser = subparsers.add_parser(
        "suggest",
        help="Randomly suggest a wallpaper for an orientation.",
    )

    suggest_parser.add_argument(
        "folder",
        type=Path,
        help="Root directory of the wallpaper collection.",
    )

    suggest_parser.add_argument(
        "--orientation",
        choices=["portrait", "landscape", "square"],
        required=True,
        help="Required wallpaper orientation.",
    )

    subparsers.add_parser(
            "monitors",
            help="List connected Windows monitors.",
    )

    args = parser.parse_args(argv)

    
    if args.command == "monitors":
        if sys.platform != "win32":
            parser.error("Monitor discovery currently requires Windows.")

        from wallpaper_orchestrator.platforms.windows import discover_monitors

        monitors = discover_monitors()

        print(f"Found {len(monitors)} active monitor(s):")

        for index, monitor in enumerate(monitors, start=1):
            print(
                f"{index}. "
                f"{monitor.width}x{monitor.height} "
                f"— {monitor.orientation}"
            )

        return 0


    
    if args.command == "suggest":
        try:
            wallpapers = load_wallpapers(args.folder)

            monitor = Monitor(
                role="preview",
                orientation=args.orientation,
            )

            selected = choose_wallpaper(monitor, wallpapers)

        except ValueError as error:
            parser.error(str(error))

        print(f"Selected wallpaper: {selected.path}")
        print(f"Orientation: {selected.orientation}")
        print(f"Themes: {', '.join(sorted(selected.themes))}")

        return 0



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
