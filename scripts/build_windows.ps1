<#
Build a portable Windows application folder. The *build machine* needs Python,
MSVC tools, and an internet connection; the end user needs none of them.
#>
param(
    [switch]$SkipInstall,
    [switch]$SkipTests
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $IsWindows -and $env:OS -ne 'Windows_NT') {
    throw 'C8 Windows executable builds must run on Windows.'
}

Push-Location $repo
try {
    if (-not $SkipInstall) {
        python -m pip install -e '.[gui,dev]'
        if ($LASTEXITCODE -ne 0) { throw 'Installing Wallpaper Orchestrator failed.' }
        python -m pip install 'Nuitka>=2.7,<5' 'ordered-set>=4.1' 'zstandard>=0.22'
        if ($LASTEXITCODE -ne 0) { throw 'Installing build dependencies failed.' }
    }
    if (-not $SkipTests) {
        python -m unittest discover -s tests -q
        if ($LASTEXITCODE -ne 0) { throw 'Unit tests failed; packaging cancelled.' }
    }

    $icon = Join-Path $repo 'assets\wallpaper-orchestrator.ico'
    python scripts/make_icon.py $icon
    if ($LASTEXITCODE -ne 0) { throw 'Generating Windows icon failed.' }

    $bundle = Join-Path $repo 'build\desktop.dist'
    $portable = Join-Path $repo 'dist\WallpaperOrchestrator'
    if (Test-Path $portable) {
        throw "Previous portable build exists at $portable. Move it aside before rebuilding."
    }

    $compileOptions = @(
        '--mode=standalone',
        '--enable-plugin=pyside6',
        '--include-package=wallpaper_orchestrator',
        '--windows-console-mode=disable',
        '--output-dir=build',
        '--output-filename=WallpaperOrchestrator.exe',
        "--windows-icon-from-ico=$icon",
        '--assume-yes-for-downloads',
        'desktop.py'
    )
    python -m nuitka @compileOptions
    if ($LASTEXITCODE -ne 0) { throw 'Nuitka build failed.' }
    if (-not (Test-Path (Join-Path $bundle 'WallpaperOrchestrator.exe'))) {
        throw 'Nuitka did not create the expected standalone executable.'
    }

    New-Item -ItemType Directory -Path (Split-Path $portable -Parent) -Force | Out-Null
    Copy-Item -LiteralPath $bundle -Destination $portable -Recurse

    Write-Host "C8.1 standalone folder created at: $portable"
    Write-Host 'Run WallpaperOrchestrator.exe --self-test before publishing.'
}
finally {
    Pop-Location
}
