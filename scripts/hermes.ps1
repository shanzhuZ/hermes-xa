# 在本仓库根目录下运行 hermes CLI / gateway，确保读取仓库内 config.yaml。
# 用法：
#   .\scripts\hermes.ps1 chat
#   .\scripts\hermes.ps1 gateway
#   .\scripts\hermes.ps1 hooks list

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$env:HERMES_HOME = $RepoRoot

Write-Host "HERMES_HOME=$RepoRoot" -ForegroundColor DarkGray
& hermes @args
