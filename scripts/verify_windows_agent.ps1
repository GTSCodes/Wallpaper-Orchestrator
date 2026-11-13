<# Read-only C8.1 acceptance check after enabling background mode in the GUI.
   Close/exit the GUI before running this, leaving the agent enabled.
#>
param([string]$Executable = '')
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $Executable) {
    $Executable = Join-Path $repo 'dist\WallpaperOrchestrator\WallpaperOrchestrator.exe'
}
$Executable = (Resolve-Path $Executable).Path
$stateDir = Join-Path $env:LOCALAPPDATA 'WallpaperOrchestrator'
$statePath = Join-Path $stateDir 'automation.json'
$statusPath = Join-Path $stateDir 'background-status.json'
Write-Host "Checking packaged executable: $Executable"
$smoke = Start-Process -FilePath $Executable -ArgumentList '--self-test' -Wait -PassThru
if ($smoke.ExitCode -ne 0) { throw "Bundle self-test failed: $($smoke.ExitCode)" }
Write-Host 'PASS: standalone identity/import smoke test'
if (-not (Test-Path $statePath)) {
    throw 'No automation.json: opt in to background mode in the GUI first.'
}
$automation = Get-Content -Raw $statePath | ConvertFrom-Json
if (-not $automation.background_enabled) { throw 'Background mode is not enabled.' }
$startup = (Get-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'WallpaperOrchestratorAgent' -ErrorAction Stop).WallpaperOrchestratorAgent
if (-not ($startup -like "*$Executable*")) {
    throw "Startup entry points to a different application: $startup"
}
Write-Host 'PASS: startup entry targets this exact executable'
$agents = @(Get-CimInstance Win32_Process | Where-Object {
    $_.ExecutablePath -eq $Executable -and $_.CommandLine -match '--background-agent'
})
if ($agents.Count -ne 1) {
    throw "Expected exactly one packaged background worker; found $($agents.Count)."
}
Write-Host "PASS: exactly one packaged background process (PID $($agents[0].ProcessId))"
if (-not (Test-Path $statusPath)) { throw 'Agent has not written background-status.json.' }
$previous = Get-Content -Raw $statusPath | ConvertFrom-Json
if (-not $previous.running) { throw "Agent status says stopped: $($previous.message)" }
Start-Sleep -Seconds 12
$current = Get-Content -Raw $statusPath | ConvertFrom-Json
if (-not $current.running -or $current.heartbeat_utc -le $previous.heartbeat_utc) {
    throw 'Agent heartbeat did not advance independently of the GUI.'
}
Write-Host 'PASS: background agent heartbeat advances without the GUI'
Write-Host "Last agent message: $($current.message)"
Write-Host 'NOTE: manually verify wallpaper changes at a scheduled interval and after reboot.'
