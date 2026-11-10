$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
if (!(Test-Path $python)) { $python = 'python' }
& $python (Join-Path $root 'main.py') @args
exit $LASTEXITCODE
