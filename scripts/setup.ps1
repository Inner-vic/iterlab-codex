param([string]$PythonExecutable, [switch]$CheckOnly, [switch]$SkipLpai)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
. (Join-Path $PSScriptRoot 'dependencies.ps1')
try {
    $python=Initialize-IterLabRuntime -PythonExecutable $PythonExecutable -ProjectRoot $root -CheckOnly:$CheckOnly -SkipLpai:$SkipLpai
    Write-Host "Dependency checks completed. Python: $python"
    Write-Host 'Installation does not sign in. Complete Codex/EPT and LPAI authorization using your own account.'
    exit 0
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
