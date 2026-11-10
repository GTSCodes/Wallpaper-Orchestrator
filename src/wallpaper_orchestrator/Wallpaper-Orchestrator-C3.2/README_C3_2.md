# Wallpaper Orchestrator — C3.2: fix background automation blocked by scanning

This update fixes a **confirmed** problem from the Windows agent status:

```
Background agent will retry: 'WallpaperCatalog' object has no attribute 'get_folder_link'
```

The independent background process was alive, but it attempted an automatic
folder scan before checking whether wallpapers were due. A scanner exception
stopped that tick before the timer ran. C3.1's GUI fallback could not help
because the old agent was still sending a healthy heartbeat.

## Install over C3.1

Back up or commit your local edits, then extract the ZIP in the **root of your
existing repository**, maintaining the `src/wallpaper_orchestrator/` structure.
The patch replaces only these files:

```
src/wallpaper_orchestrator/background.py
src/wallpaper_orchestrator/indexer.py
src/wallpaper_orchestrator/library_view.py
tests/test_automation.py
tests/test_indexer.py
README_C3_2.md
```

**Important: restart the hidden background process after installing.** A
running `pythonw.exe` keeps the previous Python code in memory even when you
replace `.py` files. Restarting only the GUI does not restart that agent.

From PowerShell, inspect the agent process specifically:

```powershell
Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" |
  Where-Object { $_.CommandLine -match 'wallpaper_orchestrator[\\/]background\.py' } |
  Select-Object ProcessId, CommandLine
```

If it shows your Wallpaper Orchestrator agent, stop **only that verified PID**:

```powershell
Stop-Process -Id <VERIFIED_AGENT_PID>
```

Replace `<VERIFIED_AGENT_PID>` with the number just shown. Do not stop all
Python or Pythonw processes. If no process matches, it is already stopped.
Reopen Wallpaper Orchestrator and allow up to one minute for it to launch a
new agent once its previous heartbeat expires.

## Verify

```powershell
$env:PYTHONPATH = "src"
python -m wallpaper_orchestrator.background --status
```

Once the agent is running the new code, the old `get_folder_link` error should
disappear. A message such as `Applied 2 wallpaper(s)` is the desired outcome.
If the scan itself fails for another reason, a due wallpaper change can still
run; the status will report both the application result and scan failure.

The screenshot from this failure shows **two saved interval rules**: one for
both displays and another for an individual monitor. Both are overdue because
the scanner kept blocking execution. When the agent is repaired it will
coalesce overlapping due targets into one wallpaper plan. If you only want
one rule, select its exact monitor group in Displays > Automate and stop it.

## What we changed, technically

| Module / function | What it does and why |
| --- | --- |
| `background.py::_run_cycle()` | Independently runs folder indexing and wallpaper scheduling; neither exception can prevent the other task from being attempted. |
| `background.py::_scan_changed_folders()` | Verifies linked folder IDs using the existing folder listing instead of requiring a missing convenience method. |
| `indexer.py::resolve_folder_link()` | Uses the fast `get_folder_link()` API when available, or the older `list_collections()` and `list_folder_links()` APIs when not. Returns the same immutable `FolderLink` data. |
| `indexer.py::scan_folder()` | Calls the compatibility resolver and continues indexing images without moving or changing originals. |
| `library_view.py::ScanWorker` | Uses the same resolver so automatic Library scanning works even with an older catalogue module. |
| `library_view.py::_selected_folder_path()` | Uses the resolver for Open Folder / Copy Path, preserving older catalogue compatibility. |

No new dependencies or database migrations are necessary. The code continues
to use standard-library `sqlite3`, `pathlib`, `threading`, Windows `ctypes`,
and existing Pillow / PySide6 components.

## Tests and known limits

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -q
```

88 tests passed in the Linux development environment, including regressions
for older catalogues without `get_folder_link()` and an indexing failure that
must not stop wallpaper scheduling. Windows `pythonw.exe`, COM and desktop
wallpaper application still need to be verified on the actual Windows system.
