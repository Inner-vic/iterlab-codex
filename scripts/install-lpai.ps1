param([string]$PythonExecutable)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
. (Join-Path $PSScriptRoot 'dependencies.ps1')
try {
    [void](Initialize-IterLabRuntime -PythonExecutable $PythonExecutable -ProjectRoot $root)
    exit 0
} catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }
