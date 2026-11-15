param([string]$PythonExecutable, [int]$Port=8781, [switch]$Background)
$ErrorActionPreference='Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$proxyVariables = @('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'GIT_HTTP_PROXY', 'GIT_HTTPS_PROXY')
foreach ($name in $proxyVariables) {
    [Uri]$proxy = $null
    $value = [Environment]::GetEnvironmentVariable($name, 'Process')
    if ($value -and [Uri]::TryCreate($value, [UriKind]::Absolute, [ref]$proxy) -and
        $proxy.Host -eq '127.0.0.1' -and $proxy.Port -eq 9) {
        throw "启动环境的 $name 指向不可用的占位代理 127.0.0.1:9。请在能访问公司网络的 Windows 终端启动 IterLab。"
    }
}
$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listener) { throw "端口 $Port 已被使用；不会覆盖或停止已有服务。" }
. (Join-Path $PSScriptRoot 'dependencies.ps1')
$PythonExecutable = Initialize-IterLabRuntime -PythonExecutable $PythonExecutable -ProjectRoot $projectRoot -AllowIncomplete
[Console]::Error.WriteLine("已核对 Python 3.11+ 解释器：$PythonExecutable")
$dataRoot = if ($env:ITERLAB_CODEX_DATA) { $env:ITERLAB_CODEX_DATA } else { Join-Path $projectRoot '.data' }
New-Item -ItemType Directory -Path $dataRoot -Force | Out-Null
if ($Background) {
    $logRoot=Join-Path $dataRoot 'logs'
    New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
    $stamp=[DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss')
    $application=Start-Process -FilePath $PythonExecutable -ArgumentList @('-m','lab.web','--port',"$Port") -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput "$logRoot/$stamp.stdout.log" -RedirectStandardError "$logRoot/$stamp.stderr.log" -PassThru
    [pscustomobject]@{pid=$application.Id;url="http://127.0.0.1:$Port";data=$dataRoot;stdout="$logRoot/$stamp.stdout.log";stderr="$logRoot/$stamp.stderr.log"} | ConvertTo-Json
} else {
    & "$PSScriptRoot/run-quiet.ps1" -Program $PythonExecutable -ProgramArgs @('-m','lab.web','--port',"$Port")
    exit $LASTEXITCODE
}
