$ErrorActionPreference = 'Stop'
$evaluatorPython = $env:MOMO_PYTHON
if (!$evaluatorPython) { $evaluatorPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe' }
if (!(Test-Path $evaluatorPython)) { $evaluatorPython = 'python' }
& $evaluatorPython -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else "评估开发版需要 Python 3.11+")'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $evaluatorPython (Join-Path $PSScriptRoot 'check_env.py') --quiet
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $evaluatorPython (Join-Path $PSScriptRoot 'evaluator.py') @args
exit $LASTEXITCODE
