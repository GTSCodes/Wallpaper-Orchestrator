
"""Command-line interface for Wallpaper Orchestrator."""

import argparse, sys

from wallpaper_orchestrator import __version__

from pathlib import Path

from wallpaper_orchestrator.library import find_images, get_image_orientation, infer_theme, load_wallpapers

from wallpaper_orchestrator.models import Monitor

from wallpaper_orchestrator.selector import choose_wallpaper

from wallpaper_orchestrator.state import (
    load_monitor_mapping,
    save_monitor_mapping,
)


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

    subparsers.add_parser(
    "setup",
    help="Identify and name connected monitors.",
    )

    
    set_parser = subparsers.add_parser(
        "set",
        help="Set a wallpaper on a named monitor.",
    )

    set_parser.add_argument(
        "--monitor",
        required=True,
        help="Friendly monitor name.",
    )

    set_parser.add_argument(
        "--image",
        type=Path,
        required=True,
        help="Path to the wallpaper image.",
    )

    set_parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually apply the wallpaper.",
    )


    args = parser.parse_args(argv)

    
    if args.command == "set":
        if sys.platform != "win32":
            parser.error("Wallpaper application requires Windows.")

        from wallpaper_orchestrator.platforms.windows import (
            discover_monitors,
            set_monitor_wallpaper,
        )

        mapping = load_monitor_mapping()

        if args.monitor not in mapping:
            parser.error(
                f"Unknown monitor: {args.monitor}. "
                "Run 'wallpaper-orchestrator setup' first."
            )

        device_id = mapping[args.monitor]

        active_ids = {
            monitor.device_id
            for monitor in discover_monitors()
        }

        if device_id not in active_ids:
            parser.error(
                f"Monitor '{args.monitor}' is not currently active."
            )

        if not args.image.is_file():
            parser.error(f"Image not found: {args.image}")

        print(f"Monitor: {args.monitor}")
        print(f"Wallpaper: {args.image}")

        if not args.apply:
            print("DRY RUN — no wallpaper changed.")
            print("Add --apply to confirm this change.")
            return 0

        try:
            set_monitor_wallpaper(device_id, args.image)
        except (OSError, ValueError) as error:
            parser.error(str(error))

        print("Wallpaper applied successfully.")
        return 0


    
    if args.command == "setup":
        if sys.platform != "win32":
            parser.error("Monitor setup currently requires Windows.")

        from wallpaper_orchestrator.platforms.windows import discover_monitors

        monitors = discover_monitors()

        if not monitors:
            parser.error("No active monitors were detected.")

        mapping = load_monitor_mapping()

        print("\nWallpaper Orchestrator — Monitor Setup\n")

        for index, monitor in enumerate(monitors, start=1):
            print(
                f"Monitor {index}: "
                f"{monitor.width}x{monitor.height} "
                f"({monitor.orientation}) "
                f"at ({monitor.x}, {monitor.y})"
            )

            while True:
                role = input("Friendly name: ").strip()

                if not role:
                    print("Please enter a non-empty name.")
                    continue

                if (
                    role in mapping
                    and mapping[role] != monitor.device_id
                ):
                    print(
                        "That name is already assigned to "
                        "another monitor."
                    )
                    continue

                break

            for old_role, device_id in list(mapping.items()):
                if device_id == monitor.device_id:
                    del mapping[old_role]

            mapping[role] = monitor.device_id
            print()

        save_monitor_mapping(mapping)

        print("Monitor mappings saved successfully.")
        return 0


    
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
                f"— {monitor.orientation} "
                f"— position ({monitor.x}, {monitor.y})"
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
