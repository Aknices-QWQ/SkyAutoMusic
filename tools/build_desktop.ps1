param(
    [string]$ReleaseTag = '',
    [string]$InnoCompiler = '',
    [string]$OutputDir = 'output/windows'
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$outputPath = [IO.Path]::GetFullPath((Join-Path $root $OutputDir))
$env:PYTHONUTF8 = '1'

Push-Location $root
try {
    $metadata = & python tools/prepare_desktop_release.py metadata --release-tag $ReleaseTag
    if ($LASTEXITCODE -ne 0) { throw 'Release metadata validation failed' }
    $version = ($metadata | ConvertFrom-Json).version
    $fileVersion = "$version.0"
    if (-not $InnoCompiler) {
        $InnoCompiler = Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'
    }
    if (-not (Test-Path -LiteralPath $InnoCompiler)) { throw "Inno Setup not found: $InnoCompiler" }
    if (Test-Path -LiteralPath $outputPath) { throw "Use a fresh output directory: $outputPath" }
    New-Item -ItemType Directory -Path $outputPath | Out-Null

    $compileArgs = @(
        '-m', 'nuitka', '--standalone', '--enable-plugin=pyside6',
        '--include-qt-plugins=sensible,multimedia', '--windows-console-mode=disable',
        '--include-package=keyboard', '--nofollow-import-to=keyboard._keyboard_tests,keyboard._mouse_tests',
        '--include-data-dir=assets=assets', '--output-filename=SkyAutoMusic.exe',
        "--output-dir=$outputPath", "--report=$outputPath/compilation-report.xml",
        "--windows-file-version=$fileVersion", "--windows-product-version=$fileVersion",
        '--windows-product-name=SkyAutoMusic', '--windows-file-description=SkyAutoMusic music tools',
        '--assume-yes-for-downloads', '--msvc=latest', '--jobs=2'
    )
    if (Test-Path -LiteralPath (Join-Path $root 'mobile/web/index.html')) {
        $compileArgs += '--include-data-dir=mobile/web=mobile/web'
    }
    $compileArgs += 'play_music_qt.py'
    & python @compileArgs 2>&1 | Tee-Object -FilePath (Join-Path $outputPath 'build.log')
    if ($LASTEXITCODE -ne 0) { throw 'Nuitka compilation failed' }

    & python tools/prepare_desktop_release.py stage --output $outputPath --release-tag $ReleaseTag
    if ($LASTEXITCODE -ne 0) { throw 'Installer staging failed' }
    $installerPath = Join-Path $outputPath 'installers'
    foreach ($edition in @('Lite', 'Full')) {
        $sourcePath = Join-Path $outputPath "staging/$edition"
        & $InnoCompiler /Q "/DEdition=$edition" "/DSourceDir=$sourcePath" "/DOutputDir=$installerPath" installer.iss
        if ($LASTEXITCODE -ne 0) { throw "$edition installer compilation failed" }
    }
    & python tools/prepare_desktop_release.py finalize --output $outputPath --release-tag $ReleaseTag
    if ($LASTEXITCODE -ne 0) { throw 'Installer verification failed' }
    Write-Output "Installers: $installerPath"
} finally {
    Pop-Location
}
