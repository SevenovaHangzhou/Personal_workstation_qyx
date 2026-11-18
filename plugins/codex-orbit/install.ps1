$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$installer = Join-Path $here 'scripts/install.py'
$check = 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'
$venv = Join-Path $here '../../.venv/Scripts/python.exe'
$candidates = @($venv, 'python', 'python3', 'python3.14', 'python3.13', 'python3.12', 'python3.11')
foreach ($name in $candidates) {
    $command = Get-Command $name -ErrorAction SilentlyContinue
    if (-not $command) { continue }
    $ErrorActionPreference = 'Continue'
    & $command.Source -c $check 2>$null
    $status = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($status -eq 0) {
        & $command.Source $installer @args
        exit $LASTEXITCODE
    }
}
$launcher = Get-Command 'py' -ErrorAction SilentlyContinue
if ($launcher) {
    & $launcher.Source -3 -c $check
    if ($LASTEXITCODE -eq 0) {
        & $launcher.Source -3 $installer @args
        exit $LASTEXITCODE
    }
}
Write-Error 'Python 3.11+ is required. Install Python and try again.'
exit 2
