# Resolve an actual interpreter; never run an app alias or install Python.
# Probes are owned, short-lived, hidden foreground processes, not services.
function Invoke-IterLabPythonProbe {
    param([string]$Executable, [switch]$Launcher)
    $script:IterLabPythonProbeFailure='路径、退出码或版本尚未通过核对'
    if (-not $Executable -or -not (Test-Path -LiteralPath $Executable -PathType Leaf)) { return $null }
    if ($Executable -match '(?i)[\\/]WindowsApps[\\/]') { return $null }
    $code = "import sys; print(str(sys.version_info.major)+'.'+str(sys.version_info.minor)+'.'+str(sys.version_info.micro)); print(sys.executable); sys.exit(0 if sys.version_info >= (3,11) else 11)"
    $start = New-Object System.Diagnostics.ProcessStartInfo
    $start.FileName = $Executable
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.StandardOutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $start.StandardErrorEncoding = New-Object System.Text.UTF8Encoding($false)
    $start.EnvironmentVariables['PYTHONIOENCODING'] = 'utf-8'
    # Version detection needs only sys: skip site/customization startup work.
    $start.Arguments = $(if ($Launcher) { '-3 ' } else { '' }) + '-I -S -c "' + $code + '"'
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $start
    try {
        [void]$process.Start()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(30000)) {
            $script:IterLabPythonProbeFailure='解释器只读探测等待30秒超时；不能据此判定Python未安装或版本不兼容'
            # Only this owned diagnostic child, never a service or port owner.
            $process.Kill()
            [void]$process.WaitForExit(2000)
            return $null
        }
        $output = $stdout.GetAwaiter().GetResult()
        [void]$stderr.GetAwaiter().GetResult()
        if ($process.ExitCode -ne 0) { return $null }
        $lines = @($output.Trim() -split '\r?\n')
        if ($lines.Count -ne 2 -or $lines[0] -notmatch '^\d+\.\d+\.\d+$') { return $null }
        $version = [version]$lines[0]
        $path = $lines[1]
        if ($version -lt [version]'3.11.0' -or -not [IO.Path]::IsPathRooted($path)) { return $null }
        if ($path -match '(?i)[\\/]WindowsApps[\\/]' -or -not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
        return [pscustomobject]@{Path=[IO.Path]::GetFullPath($path);Version=$version}
    } catch { return $null }
    finally { $process.Dispose() }
}

function Resolve-IterLabPython {
    param([string]$PythonExecutable, [string]$ProjectRoot)
    $explicit = if ($PythonExecutable) { $PythonExecutable } else { $env:ITERLAB_PYTHON }
    if ($explicit) {
        $result = Invoke-IterLabPythonProbe -Executable $explicit
        if (-not $result) { throw "指定Python尚未确认：$explicit。$script:IterLabPythonProbeFailure。未安装软件、未停止服务。" }
        return $result.Path
    }
    $paths = New-Object 'System.Collections.Generic.List[string]'
    if ($ProjectRoot) { $paths.Add((Join-Path $ProjectRoot '.venv\Scripts\python.exe')) }
    foreach ($name in @('python.exe','python3.exe')) {
        foreach ($command in @(Get-Command $name -CommandType Application -All -ErrorAction SilentlyContinue)) {
            if ($command.Source) { $paths.Add($command.Source) }
        }
    }
    # Enumerate directories explicitly instead of relying on nested wildcards.
    $roots = @()
    if ($env:LOCALAPPDATA) { $roots += (Join-Path $env:LOCALAPPDATA 'Programs\Python'); $roots += (Join-Path $env:LOCALAPPDATA 'Python') }
    if ($env:USERPROFILE) { $roots += (Join-Path $env:USERPROFILE 'AppData\Local\Programs\Python') }
    if ($env:ProgramFiles) { $roots += $env:ProgramFiles }
    foreach ($root in @($roots | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $root -PathType Container)) { continue }
        foreach ($directory in @(Get-ChildItem -LiteralPath $root -Directory -Filter 'Python*' -ErrorAction SilentlyContinue)) {
            $paths.Add((Join-Path $directory.FullName 'python.exe'))
        }
    }
    $results = @()
    foreach ($path in @($paths | Select-Object -Unique)) {
        $result = Invoke-IterLabPythonProbe -Executable $path
        if ($result) { $results += $result }
    }
    $launchers = @()
    foreach ($command in @(Get-Command py.exe -CommandType Application -All -ErrorAction SilentlyContinue)) {
        if ($command.Source) { $launchers += $command.Source }
    }
    if ($env:LOCALAPPDATA) { $launchers += (Join-Path $env:LOCALAPPDATA 'Programs\Python\Launcher\py.exe') }
    if ($env:WINDIR) { $launchers += (Join-Path $env:WINDIR 'py.exe') }
    foreach ($launcher in @($launchers | Select-Object -Unique)) {
        $fromLauncher = Invoke-IterLabPythonProbe -Executable $launcher -Launcher
        if ($fromLauncher) {
            # The service uses sys.executable, not py.exe; verify it directly.
            $direct = Invoke-IterLabPythonProbe -Executable $fromLauncher.Path
            if ($direct) { $results += $direct }
        }
    }
    $chosen = $results | Sort-Object Version -Descending | Select-Object -First 1
    if ($chosen) { return $chosen.Path }
    throw '未找到可运行的 Python 3.11+。已检查项目虚拟环境、PATH、常见安装目录和 py 启动器。可用 -PythonExecutable 指定真实 python.exe；不会自动安装或修改系统 PATH。'
}
