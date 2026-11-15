[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Program,
    [string]$StdoutPath,
    [string]$StdoutStreamPath,
    [string]$StderrStreamPath,
    [Parameter(ValueFromRemainingArguments = $true, Position = 1)][string[]]$ProgramArgs
)
# Foreground ownership and real output/exit code, without allocating a child
# console. No background process, task, listener or global window hook.
$ErrorActionPreference = 'Stop'
if (($StdoutStreamPath -and -not $StderrStreamPath) -or ($StderrStreamPath -and -not $StdoutStreamPath) -or ($StdoutPath -and $StdoutStreamPath)) {
    throw 'Use both streaming paths, or the original buffered output option.'
}
# Output switches are named-only. Refuse an explicit output path which is also
# an input file before starting the process; never truncate a script as stdout.
foreach ($outputPath in @($StdoutPath, $StdoutStreamPath, $StderrStreamPath)) {
    if (-not $outputPath) { continue }
    $outputAbsolute = [System.IO.Path]::GetFullPath($outputPath)
    foreach ($argument in @($Program) + @($ProgramArgs)) {
        if ($argument -and (Test-Path -LiteralPath $argument -PathType Leaf) -and
            $outputAbsolute -eq (Resolve-Path -LiteralPath $argument).Path) {
            throw 'Output path is also an input file; refusing to overwrite it.'
        }
    }
}
$start = [System.Diagnostics.ProcessStartInfo]::new()
$start.FileName = if (Test-Path -LiteralPath $Program -PathType Leaf) { (Resolve-Path -LiteralPath $Program).Path } else { $Program }
$start.WorkingDirectory = (Get-Location).Path
$start.UseShellExecute = $false
$start.CreateNoWindow = $true
$start.RedirectStandardOutput = $true
$start.RedirectStandardError = $true
$start.EnvironmentVariables['PYTHONIOENCODING'] = 'utf-8'
$start.EnvironmentVariables['PYTHONUTF8'] = '1'
$start.StandardOutputEncoding = [System.Text.UTF8Encoding]::new($false)
$start.StandardErrorEncoding = [System.Text.UTF8Encoding]::new($false)
if ($start.PSObject.Properties['ArgumentList']) {
    foreach ($item in $ProgramArgs) { $start.ArgumentList.Add($item) }
} else {
    # Windows PowerShell 5.1 / .NET Framework: Windows argv quoting rules.
    $quoted = foreach ($item in $ProgramArgs) {
        '"' + [regex]::Replace([regex]::Replace($item, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1') + '"'
    }
    $start.Arguments = $quoted -join ' '
}
$process = [System.Diagnostics.Process]::new()
$process.StartInfo = $start
$stdoutFile = $null
$stderrFile = $null
try {
    if ($StdoutStreamPath) {
        $stdoutFile = [System.IO.File]::Open([System.IO.Path]::GetFullPath($StdoutStreamPath), [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::Read)
        $stderrFile = [System.IO.File]::Open([System.IO.Path]::GetFullPath($StderrStreamPath), [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::Read)
    }
    [void]$process.Start()
    # Long-lived services stream to owned log files instead of retaining the
    # entire service lifetime in the PowerShell host's memory.
    $stdout = if ($stdoutFile) { $process.StandardOutput.BaseStream.CopyToAsync($stdoutFile) } else { $process.StandardOutput.ReadToEndAsync() }
    $stderr = if ($stderrFile) { $process.StandardError.BaseStream.CopyToAsync($stderrFile) } else { $process.StandardError.ReadToEndAsync() }
    $process.WaitForExit()
    $output = $stdout.GetAwaiter().GetResult()
    if ($stdoutFile) {
        $stdoutFile.Flush()
    } elseif ($StdoutPath) {
        [System.IO.File]::WriteAllText([System.IO.Path]::GetFullPath($StdoutPath), $output, [System.Text.UTF8Encoding]::new($false))
    } else { [Console]::Out.Write($output) }
    if ($stderrFile) { [void]$stderr.GetAwaiter().GetResult(); $stderrFile.Flush() }
    else { [Console]::Error.Write($stderr.GetAwaiter().GetResult()) }
    $code = $process.ExitCode
} finally {
    if ($stdoutFile) { $stdoutFile.Dispose() }
    if ($stderrFile) { $stderrFile.Dispose() }
    $process.Dispose()
}
exit $code
