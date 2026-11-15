param([int]$Port=8781)
$ErrorActionPreference='Stop'
$baseUrl="http://127.0.0.1:$Port"
$page=Invoke-WebRequest -Uri $baseUrl -UseBasicParsing
$tokenMatch=[regex]::Match($page.Content,'name="workbench-token" content="([^"]+)"')
if (-not $tokenMatch.Success -or $page.Content -notlike '*IterLab*Codex*') { throw '该端口不是本项目的服务，拒绝停止。' }
Invoke-RestMethod -Method Post -Uri "$baseUrl/api/service/stop" -Headers @{'X-Workbench-Token'=$tokenMatch.Groups[1].Value} -ContentType 'application/json' -Body '{}'
