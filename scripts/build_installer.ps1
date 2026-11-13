<# C8.2 installer compile. Requires the portable folder and Inno Setup 6 or 7. #>
param([string]$IsccPath = '')

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$bundle = Join-Path $repo 'dist\WallpaperOrchestrator\WallpaperOrchestrator.exe'
if (-not (Test-Path $bundle)) {
    throw 'Build the portable executable first using .\scripts\build_windows.ps1'
}
if (-not $IsccPath) {
    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 7\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 7\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
    )
    $IsccPath = $candidates | Where-Object { $_ -and (Test-Path $_) } |
        Select-Object -First 1
}
if (-not $IsccPath -or -not (Test-Path $IsccPath)) {
    throw 'Install Inno Setup 6 or 7 or pass -IsccPath with the absolute ISCC.exe path.'
}
& $IsccPath (Join-Path $repo 'installer\WallpaperOrchestrator.iss')
if ($LASTEXITCODE -ne 0) { throw 'Inno Setup build failed.' }
Write-Host "Installer generated: $(Join-Path $repo 'dist\WallpaperOrchestrator-Setup.exe')"
