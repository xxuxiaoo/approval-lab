param([int]$Port = 8765, [switch]$Foreground)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$BootstrapArguments = @('bootstrap.py', '--port', "$Port")
if ($Foreground) { $BootstrapArguments += '--foreground' }
$ProjectPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $ProjectPython -PathType Leaf) {
    & $ProjectPython @BootstrapArguments
    exit $LASTEXITCODE
}

function Test-PythonRuntime {
    param([string]$Executable, [string[]]$PrefixArguments = @())
    try {
        & $Executable @PrefixArguments -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

$PythonLauncher = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue
if ($PythonLauncher -and (Test-PythonRuntime -Executable $PythonLauncher.Source -PrefixArguments @('-3'))) {
    & $PythonLauncher.Source -3 @BootstrapArguments
    exit $LASTEXITCODE
}
$SystemPython = Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue
if ($SystemPython -and (Test-PythonRuntime -Executable $SystemPython.Source)) {
    & $SystemPython.Source @BootstrapArguments
    exit $LASTEXITCODE
}
throw "Python 3.11+ was not found. Install Python or restore the project's .venv folder."
