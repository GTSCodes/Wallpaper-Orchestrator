
"""Command-line interface for Wallpaper Orchestrator."""

import argparse, sys

from wallpaper_orchestrator import __version__

from pathlib import Path

from wallpaper_orchestrator.library import find_images, get_image_orientation, infer_theme, load_wallpapers

from wallpaper_orchestrator.models import Monitor

from wallpaper_orchestrator.selector import choose_wallpaper, choose_for_monitors

from wallpaper_orchestrator.config import load_config

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

    
    shuffle_parser = subparsers.add_parser(
        "shuffle",
        help="Randomly change a monitor's wallpaper.",
    )

    shuffle_target = shuffle_parser.add_mutually_exclusive_group(
        required=True
    )

    shuffle_target.add_argument(
        "--monitor",
        help="Friendly monitor name.",
    )

    shuffle_target.add_argument(
        "--all",
        action="store_true",
        help="Shuffle all active monitors together.",
    )

    shuffle_parser.add_argument(
        "--folder",
        type=Path,
        required=True,
        help="Wallpaper library directory.",
    )

    shuffle_parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the selected wallpaper.",
    )

    plan_parser = subparsers.add_parser(
    "plan",
    help="Preview a coordinated wallpaper combination.",
    )

    plan_parser.add_argument(
        "--folder",
        type=Path,
        required=True,
        help="Wallpaper library directory.",
    )



    args = parser.parse_args(argv)

    
    if args.command == "plan":
        if sys.platform != "win32":
            parser.error("Monitor planning currently requires Windows.")

        from wallpaper_orchestrator.platforms.windows import discover_monitors

        mapping = load_monitor_mapping()
        config = load_config()

        detected = discover_monitors()

        roles_by_id = {
            device_id: role
            for role, device_id in mapping.items()
        }

        unmapped = [
            monitor
            for monitor in detected
            if monitor.device_id not in roles_by_id
        ]

        if unmapped:
            parser.error(
                f"{len(unmapped)} active monitor(s) have no mapping. "
                "Run 'wallpaper-orchestrator setup' first."
            )

        monitors = [
            Monitor(
                role=roles_by_id[monitor.device_id],
                orientation=monitor.orientation,
            )
            for monitor in detected
        ]

        try:
            wallpapers = load_wallpapers(args.folder)

            supported = [
                wallpaper
                for wallpaper in wallpapers
                if wallpaper.path.suffix.lower()
                in {".jpg", ".jpeg", ".png", ".bmp"}
            ]

            limits = config.get("theme_limits", {})

            if not isinstance(limits, dict) or any(
                not isinstance(theme, str)
                or type(limit) is not int
                or limit < 0
                for theme, limit in limits.items()
            ):
                raise ValueError("Invalid theme_limits configuration.")

            assignments = choose_for_monitors(
                monitors,
                supported,
                theme_limits=limits,
            )

        except (OSError, ValueError) as error:
            parser.error(str(error))

        print("\nWallpaper plan:\n")

        for role, wallpaper in assignments.items():
            print(f"{role}:")
            print(f"  Image: {wallpaper.path}")
            print(f"  Themes: {', '.join(sorted(wallpaper.themes))}")
            print()

        print("All theme constraints satisfied.")
        print("No wallpapers changed.")

        return 0


    
    if args.command == "shuffle":
        if args.all:
            if sys.platform != "win32":
                parser.error("Wallpaper shuffling currently requires Windows.")

            from wallpaper_orchestrator.platforms.windows import (
                discover_monitors,
                get_monitor_wallpaper,
                set_monitor_wallpaper,
            )

            mapping = load_monitor_mapping()
            config = load_config()
            detected = discover_monitors()

            roles_by_id = {
                device_id: role
                for role, device_id in mapping.items()
            }

            if any(
                monitor.device_id not in roles_by_id
                for monitor in detected
            ):
                parser.error(
                    "An active monitor has no mapping. "
                    "Run 'wallpaper-orchestrator setup' first."
                )

            if not detected:
                parser.error("No active monitors detected.")

            monitors = [
                Monitor(
                    role=roles_by_id[monitor.device_id],
                    orientation=monitor.orientation,
                )
                for monitor in detected
            ]

            try:
                wallpapers = load_wallpapers(args.folder)

                supported = [
                    wallpaper
                    for wallpaper in wallpapers
                    if wallpaper.path.suffix.lower()
                    in {".jpg", ".jpeg", ".png", ".bmp"}
                    and wallpaper.path.is_file()
                ]

                limits = config.get("theme_limits", {})

                if not isinstance(limits, dict) or any(
                    not isinstance(theme, str)
                    or type(limit) is not int
                    or limit < 0
                    for theme, limit in limits.items()
                ):
                    raise ValueError(
                        "Invalid theme_limits configuration."
                    )

                assignments = choose_for_monitors(
                    monitors,
                    supported,
                    theme_limits=limits,
                )

            except (OSError, ValueError) as error:
                parser.error(str(error))

            print("\nCoordinated wallpaper selection:\n")

            for role, wallpaper in assignments.items():
                print(f"{role}: {wallpaper.path}")

            if not args.apply:
                print("\nDRY RUN — no wallpapers changed.")
                return 0

            # Save the current wallpapers before applying changes.
            previous = {}

            try:
                for monitor in detected:
                    previous[monitor.device_id] = (
                        get_monitor_wallpaper(monitor.device_id)
                    )
            except Exception as error:
                parser.error(
                    f"Could not save current wallpapers: {error}"
                )

            # Apply the complete plan.
            changed = []

            try:
                for monitor in detected:
                    role = roles_by_id[monitor.device_id]
                    wallpaper = assignments[role]

                    set_monitor_wallpaper(
                        monitor.device_id,
                        wallpaper.path,
                    )

                    changed.append(monitor.device_id)

            except Exception as error:
                print(f"\nWallpaper application failed: {error}")

                print("Attempting to restore previous wallpapers...")

                restoration_failed = False

                for device_id in reversed(changed):
                    old_wallpaper = previous[device_id]

                    if old_wallpaper is None:
                        restoration_failed = True
                        print(
                            "Cannot restore a monitor with "
                            "no previous wallpaper path."
                        )
                        continue

                    try:
                        set_monitor_wallpaper(
                            device_id,
                            old_wallpaper,
                        )
                    except Exception as restore_error:
                        restoration_failed = True
                        print(
                            f"Restoration failed: {restore_error}"
                        )

                if restoration_failed:
                    print(
                        "Some wallpapers could not be restored."
                    )
                else:
                    print("Previous wallpapers restored.")

                return 1

            print("\nAll wallpapers applied successfully.")
            return 0

        if sys.platform != "win32":
            parser.error("Wallpaper shuffling currently requires Windows.")

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

        detected = next(
            (
                monitor
                for monitor in discover_monitors()
                if monitor.device_id == device_id
            ),
            None,
        )

        if detected is None:
            parser.error(
                f"Monitor '{args.monitor}' is not currently active."
            )

        try:
            wallpapers = load_wallpapers(args.folder)

            supported = [
                wallpaper
                for wallpaper in wallpapers
                if wallpaper.path.suffix.lower()
                in {".jpg", ".jpeg", ".png", ".bmp"}
            ]

            logical_monitor = Monitor(
                role=args.monitor,
                orientation=detected.orientation,
            )

            selected = choose_wallpaper(
                logical_monitor,
                supported,
            )

        except (OSError, ValueError) as error:
            parser.error(str(error))

        print(f"Monitor: {args.monitor}")
        print(f"Selected: {selected.path}")
        print(f"Theme: {', '.join(sorted(selected.themes))}")

        if not args.apply:
            print("DRY RUN — no wallpaper changed.")
            return 0

        try:
            set_monitor_wallpaper(device_id, selected.path)
        except (OSError, ValueError) as error:
            parser.error(str(error))

        print("Wallpaper shuffled successfully.")
        return 0


    
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
