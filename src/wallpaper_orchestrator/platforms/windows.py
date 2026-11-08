
"""Windows monitor discovery using IDesktopWallpaper."""

from ctypes import POINTER, c_uint, c_wchar_p
from ctypes.wintypes import RECT
from dataclasses import dataclass

from comtypes import COMMETHOD, GUID, HRESULT, IUnknown, COMError
from comtypes.client import CreateObject

from pathlib import Path


class IDesktopWallpaper(IUnknown):
    _iid_ = GUID("{B92B56A9-8B55-4E14-9A89-0199BBB6F93B}")

    _methods_ = [
        COMMETHOD(
            [], HRESULT, "SetWallpaper",
            (["in"], c_wchar_p, "monitor_id"),
            (["in"], c_wchar_p, "wallpaper"),
        ),
        COMMETHOD(
            [], HRESULT, "GetWallpaper",
            (["in"], c_wchar_p, "monitor_id"),
            (["out"], POINTER(c_wchar_p), "wallpaper"),
        ),
        COMMETHOD(
            [], HRESULT, "GetMonitorDevicePathAt",
            (["in"], c_uint, "index"),
            (["out"], POINTER(c_wchar_p), "monitor_id"),
        ),
        COMMETHOD(
            [], HRESULT, "GetMonitorDevicePathCount",
            (["out"], POINTER(c_uint), "count"),
        ),
        COMMETHOD(
            [], HRESULT, "GetMonitorRECT",
            (["in"], c_wchar_p, "monitor_id"),
            (["out"], POINTER(RECT), "rectangle"),
        ),
    ]


@dataclass(frozen=True)
class DetectedMonitor:
    device_id: str
    x: int
    y: int
    width: int
    height: int

    @property
    def orientation(self) -> str:
        return (
            "portrait" if self.height > self.width
            else "landscape" if self.width > self.height
            else "square"
        )


def discover_monitors() -> list[DetectedMonitor]:
    """Discover attached Windows displays and their geometry."""

    desktop = CreateObject(
        "{C2CF3110-460E-4FC1-B9D0-8A1C0C9CC4BD}",
        interface=IDesktopWallpaper,
    )

    monitors = []

    count = desktop.GetMonitorDevicePathCount()

    print(f"Windows reports {count} associated monitor(s).")

    for index in range(count):
        device_id = desktop.GetMonitorDevicePathAt(index)
        
        print(f"Checking monitor entry {index + 1}...")

        try:
            rect = desktop.GetMonitorRECT(device_id)

        except COMError as error:
            print(f"  Could not retrieve monitor geometry: {error}")
            continue


        if rect is None:
            continue

        width = rect.right - rect.left
        height = rect.bottom - rect.top

        if width <= 0 or height <= 0:
            continue

        monitors.append(
            DetectedMonitor(
                device_id=device_id,
                x=rect.left,
                y=rect.top,
                width=width,
                height=height,
            )
        )

    return monitors



def set_monitor_wallpaper(
    device_id: str,
    image_path: Path,
) -> None:
    """Apply an image to a specific physical Windows monitor."""

    if not image_path.is_file():
        raise FileNotFoundError(
            f"Wallpaper image does not exist: {image_path}"
        )

    if image_path.suffix.lower() not in {
        ".jpg", ".jpeg", ".png", ".bmp"
    }:
        raise ValueError(
            "This wallpaper format is not currently supported."
        )

    desktop = CreateObject(
        "{C2CF3110-460E-4FC1-B9D0-8A1C0C9CC4BD}",
        interface=IDesktopWallpaper,
    )

    desktop.SetWallpaper(
        device_id,
        str(image_path.resolve()),
    )
