param(
    [string]$PythonExecutable,
    [ValidateRange(1024,65535)][int]$Port=8781,
    [ValidateRange(5,120)][int]$TimeoutSeconds=30,
    [switch]$NoBrowser
)
$ErrorActionPreference='Stop'
$projectRoot=Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$dataRoot=[IO.Path]::GetFullPath($(if ($env:ITERLAB_CODEX_DATA) { $env:ITERLAB_CODEX_DATA } else { Join-Path $projectRoot '.data' }))
$baseUrl="http://127.0.0.1:$Port/"
$receipt=$null

function Test-OwnedService([Nullable[int]]$ExpectedProcessId=$null) {
    $listeners=@(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    if (-not $listeners.Count) { return $false }
    $identityPath=Join-Path $dataRoot 'service.json'
    if (-not (Test-Path -LiteralPath $identityPath -PathType Leaf)) { return $false }
    try {
        $identity=Get-Content -LiteralPath $identityPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $serviceProcessId=[int]$identity.pid
        if ([int]$identity.port -ne $Port -or $identity.runtime -ne 'codex-app-server-stdio') { return $false }
        if ([IO.Path]::GetFullPath([string]$identity.data).TrimEnd('\') -ine $dataRoot.TrimEnd('\')) { return $false }
        if ($null -ne $ExpectedProcessId -and $serviceProcessId -ne $ExpectedProcessId) { return $false }
        if (@($listeners | Where-Object { $_.OwningProcess -ne $serviceProcessId }).Count) { return $false }
        $process=Get-CimInstance Win32_Process -Filter "ProcessId=$serviceProcessId" -ErrorAction Stop
        if (-not $process -or $process.CommandLine -notmatch '(?i)(?:^|\s)-m\s+lab\.web(?:\s|$)') { return $false }
        # Identity must be newer than this exact process; do not accept a reused PID.
        $written=(Get-Item -LiteralPath $identityPath).LastWriteTimeUtc
        if ($process.CreationDate.ToUniversalTime() -gt $written.AddSeconds(2)) { return $false }
        $page=Invoke-WebRequest -Uri $baseUrl -UseBasicParsing -TimeoutSec 2 -MaximumRedirection 0
        if ($page.StatusCode -ne 200 -or $page.Content -notmatch 'IterLab.*Codex 研究工作台' -or $page.Content -notmatch 'name="workbench-token" content="[^"]+"') { return $false }
        return $true
    } catch { return $false }
}

try {
    $existing=@(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    if ($existing.Count) {
        if (-not (Test-OwnedService)) { throw "端口 $Port 已被使用，但不能核对为此项目的数据实例；未停止进程或换端口。" }
        Write-Host '已核对此项目的运行服务，复用原实例；不会自动重启。'
        Write-Host '若刚更新源码，请先明确停止旧服务再启动；刷新页面不会替换服务持有的资源快照。'
    } else {
        $startOptions=@{Port=$Port;Background=$true}
        if ($PythonExecutable) { $startOptions.PythonExecutable=$PythonExecutable }
        $receipt=(& (Join-Path $PSScriptRoot 'start.ps1') @startOptions) | ConvertFrom-Json
        if (-not $receipt.pid) { throw '启动脚本没有返回确切进程身份；未再次启动。' }
        $deadline=[DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
        $ready=$false
        while ([DateTime]::UtcNow -lt $deadline) {
            if (-not (Get-Process -Id ([int]$receipt.pid) -ErrorAction SilentlyContinue)) { throw '新服务进程已退出，未自动重试。' }
            if (Test-OwnedService ([int]$receipt.pid)) { $ready=$true; break }
            Start-Sleep -Milliseconds 300
        }
        if (-not $ready) { throw '等待服务就绪超时；原进程可能仍在启动，未再次启动或强制停止。请先检查日志和原 PID。' }
        Write-Host '服务已就绪。'
    }
    Write-Host "前端链接：$baseUrl"
    if ($receipt) { Write-Host "日志：$($receipt.stdout) / $($receipt.stderr)" }
    if (-not $NoBrowser) {
        try { Start-Process -FilePath $baseUrl -ErrorAction Stop }
        catch { Write-Warning "服务已就绪，但默认浏览器未能打开。请手动访问 $baseUrl"; exit 2 }
    }
    exit 0
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    if ($receipt) { [Console]::Error.WriteLine("原 PID：$($receipt.pid)；日志：$($receipt.stdout) / $($receipt.stderr)") }
    exit 1
}
