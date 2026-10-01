param(
    [switch]$Release,
    [switch]$Offline
)

$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$gradleTask = if ($Release) { 'assembleRelease' } else { 'assembleDebug' }
$gradleArgs = @('-p', (Join-Path $root 'mobile/android'), $gradleTask, '--no-daemon')
if ($Offline) {
    $gradleArgs += '--offline'
}

Push-Location $root
try {
    & gradle @gradleArgs
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
    $variant = if ($Release) { 'release' } else { 'debug' }
    $apk = Join-Path $root "mobile/android/app/build/outputs/apk/$variant/app-$variant.apk"
    if (-not (Test-Path -LiteralPath $apk)) {
        throw "APK not found: $apk"
    }
    Write-Output "Built $apk"
} finally {
    Pop-Location
}
