# 稳定启动 Spring Boot：先 compile 校验主类，再 spring-boot:run
# 用法：在 clients/hermes-xa 下执行  .\start.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "==> mvn compile ..."
mvn -DskipTests compile
if ($LASTEXITCODE -ne 0) {
    throw "compile failed, exit=$LASTEXITCODE"
}

$classFile = Join-Path $PSScriptRoot "target\classes\com\example\aw\AwApplication.class"
if (-not (Test-Path $classFile)) {
    throw "AwApplication.class missing after compile: $classFile"
}
Write-Host "==> main class ok: $classFile"

$holders = Get-NetTCPConnection -LocalPort 4377 -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique
foreach ($procId in $holders) {
    if ($procId -and $procId -ne 0) {
        Write-Host "==> stop process on 4377: $procId"
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    }
}
Start-Sleep -Seconds 2

Write-Host "==> spring-boot:run"
mvn -DskipTests spring-boot:run
