# Wallpaper Orchestrator

**Wallpaper Orchestrator** is a Windows 10/11 multi-monitor wallpaper manager.
Use collections, tags, linked folders, independent display assignments, shuffle
intervals and seasonal rules. Wallpaper changes can continue in a separate
background agent after the main window closes.

## Install on Windows

1. Open [Releases](https://github.com/GTSCodes/Wallpaper-Orchestrator/releases)
   and download the latest **Windows-Setup.exe** from the release assets.
2. Run the setup file **as your normal Windows user** (administrator privileges
   are not normally needed). Follow the installer, then open **Wallpaper
   Orchestrator** from the Start menu.
3. On first launch, complete the setup wizard. Use **Library** to add collections
   and images, then **Displays** to assign collections or folders to monitors.
4. Select a monitor and use **Automate** to create a timed shuffle. The
   background agent can operate even when the window is closed to the tray.

The install creates application files under `%LOCALAPPDATA%\Programs\WallpaperOrchestrator`.
Your images are **never moved or deleted by the installer**. The catalogue,
profiles, schedules and restore points remain in `%LOCALAPPDATA%\WallpaperOrchestrator`.
Removing the application does not remove this data.

**Portable alternative:** Download the `Windows-portable.zip` asset, extract the
entire folder and launch `WallpaperOrchestrator.exe`. Don't run it directly from
inside the ZIP. Avoid running installed, portable and source agents together.

**Beta trust notice:** Beta installers may be unsigned and could trigger
Windows SmartScreen warnings. Verify the download source and checksum before
running; never disable Windows security protections to install the app. If
Inno Setup reports `Error 5: Access is denied` while creating a temporary
folder, see the troubleshooting document instead of changing system-wide ACLs.

### Verify a release checksum

Download the release's `SHA256SUMS.txt` and compare the installer hash:

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath 'C:\path\to\WallpaperOrchestrator-0.8.0b3-Windows-Setup.exe'
```

The hash should match the filename's line in `SHA256SUMS.txt`.

## Develop from source

Requires Python 3.13 (project accepts Python 3.12+):

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e '.[gui,dev]'
python -m unittest discover -s tests -q
python desktop.py
```

A development build uses `pythonw.exe` in the active virtual environment for
its background agent. To compile a standalone Windows folder and installer:

```powershell
.\scripts\build_windows.ps1
.\scripts\build_installer.ps1
```

The standalone build script refuses to overwrite an existing `dist\WallpaperOrchestrator`
folder: rename or move the old build aside before running it.

## Diagnostics, backups and releases

- **Help → Export redacted diagnostics…** saves a local ZIP for support. Nothing
  is uploaded automatically. Review any ZIP before sharing it.
- Before a database schema migration, SQLite's online backup API snapshots the
  catalogue and saved JSON settings. The installer also backs up per-user state
  before automatically launching an upgraded app. Backups are under
  `%LOCALAPPDATA%\WallpaperOrchestrator\backups`.
- The **First Restore Point** is a write-once wallpaper snapshot and is not
  overwritten during upgrades or automatic backups.
- GitHub Actions runs tests, compiles and validates the Windows executable and
  installer, and uploads ZIP/EXE/checksum artifacts. Only an explicit matching
  `vX.Y.Z` tag publishes an **automatic GitHub pre-release**, after CI succeeds.

For detailed acceptance tests, upgrade and rollback steps, see
[docs/C8.3-RELEASE-VALIDATION.md](docs/C8.3-RELEASE-VALIDATION.md).
For Windows troubleshooting, see
[docs/C8-WINDOWS-TESTING.md](docs/C8-WINDOWS-TESTING.md).
