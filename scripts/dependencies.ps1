# Dot-source only. Installs missing runtime dependencies, not login credentials.
. (Join-Path $PSScriptRoot 'resolve-python.ps1')

function Initialize-IterLabRuntime {
    param([string]$PythonExecutable, [string]$ProjectRoot, [switch]$CheckOnly,
          [switch]$SkipLpai, [switch]$AllowIncomplete)
    $runtime=Join-Path $ProjectRoot '.runtime'
    New-Item -ItemType Directory -Path $runtime -Force | Out-Null
    $lease=$null
    try {
        # OS lock releases after a crash; no stale PID lock to clear.
        try { $lease=[IO.File]::Open((Join-Path $runtime 'setup.lock'), 'OpenOrCreate', 'ReadWrite', 'None') }
        catch { throw '另一安装检查正在执行；本次不重复安装。' }
        try { $python=Resolve-IterLabPython -PythonExecutable $PythonExecutable -ProjectRoot $ProjectRoot }
        catch {
            if ($CheckOnly -or $PythonExecutable -or $env:ITERLAB_PYTHON -or
                $script:IterLabPythonProbeFailure -match '30秒超时') { throw }
            $winget=Get-Command winget.exe -CommandType Application -ErrorAction SilentlyContinue
            if (-not $winget) { throw '未找到 Python 3.11+ 或 winget。请先安装 Python 3.12，再运行 app.cmd。' }
            [Console]::Error.WriteLine('Python 缺失，正在安装 Python 3.12（当前用户）。')
            & (Join-Path $PSScriptRoot 'run-quiet.ps1') -Program $winget.Source -ProgramArgs @(
                'install','--id','Python.Python.3.12','--exact','--source','winget','--scope','user',
                '--silent','--accept-package-agreements','--accept-source-agreements','--disable-interactivity')
            if ($LASTEXITCODE -ne 0) { throw "Python 安装未完成（退出码 $LASTEXITCODE）；未启动服务。" }
            $python=Resolve-IterLabPython -ProjectRoot $ProjectRoot
        }
        $report=Join-Path $runtime 'dependencies.json'
        $options=@((Join-Path $PSScriptRoot 'bootstrap.py'),'--report',$report)
        if ($CheckOnly) { $options+='--check-only' }
        if ($SkipLpai) { $options+='--skip-lpai' }
        $output=Join-Path $runtime 'setup-output.txt'
        & (Join-Path $PSScriptRoot 'run-quiet.ps1') -Program $python -StdoutPath $output -ProgramArgs $options
        $code=$LASTEXITCODE
        if (Test-Path -LiteralPath $output) { [Console]::Error.WriteLine([IO.File]::ReadAllText($output)) }
        if ($code -notin @(0,2)) { throw "依赖检查退出（$code）；未启动服务。" }
        $result=Get-Content -LiteralPath $report -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($path in @($result.paths)) {
            if ($path -and (Test-Path -LiteralPath $path -PathType Container)) { $env:PATH="$path;$env:PATH" }
        }
        if (-not $result.complete -and -not $AllowIncomplete) {
            throw "依赖尚未全部就绪，详见 $report。未修改登录或研究记录。"
        }
        if (-not $result.complete) { [Console]::Error.WriteLine('部分工具尚未就绪，界面仍可打开；相应训练入口继续执行原有预检。') }
        return $python
    } finally {
        if ($lease) { $lease.Dispose() }
    }
}
