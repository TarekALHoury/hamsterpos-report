$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectDir

$python = Get-ChildItem -LiteralPath (Join-Path $env:LOCALAPPDATA 'Programs\Python') -Filter python.exe -Recurse -ErrorAction SilentlyContinue |
  Where-Object { $_.FullName -notlike '*\Lib\venv\*' } |
  Sort-Object FullName -Descending |
  Select-Object -First 1 -ExpandProperty FullName

if (-not $python) {
  $pythonCommand = Get-Command py, python -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($pythonCommand) { $python = $pythonCommand.Source }
}
if (-not $python) { throw 'Python 3 was not found.' }

& $python -m pip install --upgrade pip
& $python -m pip install -r requirements.txt
& $python -m PyInstaller --noconfirm --clean --onefile --windowed --name report `
  --icon assets\app_icon.ico `
  --add-data "assets\app_icon.ico;assets" `
  --collect-data customtkinter `
  --collect-submodules pystray `
  --collect-all windows_toasts `
  --collect-submodules winrt `
  --exclude-module matplotlib --exclude-module numpy --exclude-module pandas `
  main.py

Write-Host "Built: $projectDir\dist\report.exe"
