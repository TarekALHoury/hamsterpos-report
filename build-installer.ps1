param(
    [switch]$SkipAppBuild
)

$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectDir

if (-not $SkipAppBuild) {
    & (Join-Path $projectDir 'build.ps1')
}

$compilerCandidates = @(
    (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
    (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
)
$compiler = $compilerCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1

if (-not $compiler) {
    throw @"
Inno Setup 6 is not installed.
Install it from https://jrsoftware.org/isdl.php, then run this script again.
"@
}

$appExe = Join-Path $projectDir 'dist\report.exe'
if (-not (Test-Path -LiteralPath $appExe)) {
    throw "Application executable was not found: $appExe"
}

& $compiler (Join-Path $projectDir 'installer.iss')
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup failed with exit code $LASTEXITCODE"
}

$installer = Get-ChildItem -LiteralPath (Join-Path $projectDir 'installer_output') -Filter 'HamsterPOSReportsSetup-*.exe' |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1

Write-Host "Installer built successfully: $($installer.FullName)"
