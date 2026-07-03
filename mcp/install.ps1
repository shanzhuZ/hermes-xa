# Merge mcp_servers.yaml into Hermes config.yaml
$ErrorActionPreference = "Stop"
$HermesHome = if ($env:HERMES_HOME) { $env:HERMES_HOME } else { "D:\hermes-xa" }
$ConfigPath = Join-Path $HermesHome "config.yaml"
$McpYamlPath = Join-Path $HermesHome "mcp\mcp_servers.yaml"

$lines = Get-Content $McpYamlPath -Encoding UTF8
$start = -1
for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -eq "mcp_servers:") { $start = $i; break }
}
if ($start -lt 0) { throw "mcp_servers: not found in mcp_servers.yaml" }
$mcpLines = $lines[$start..($lines.Count - 1)]

$configLines = Get-Content $ConfigPath -Encoding UTF8
$out = New-Object System.Collections.Generic.List[string]
$skip = $false
$inserted = $false
foreach ($line in $configLines) {
    if ($line -eq "mcp_servers:") {
        if (-not $inserted) {
            foreach ($ml in $mcpLines) { $out.Add($ml) }
            $inserted = $true
        }
        $skip = $true
        continue
    }
    if ($skip) {
        if ($line -match '^[a-z_]+:' -and $line -ne "mcp_servers:") {
            $skip = $false
            $out.Add($line)
        }
        continue
    }
    $out.Add($line)
}
if (-not $inserted) {
    $final = New-Object System.Collections.Generic.List[string]
    foreach ($line in $out) {
        if (-not $inserted -and $line -eq "custom_providers:") {
            foreach ($ml in $mcpLines) { $final.Add($ml) }
            $inserted = $true
        }
        $final.Add($line)
    }
    $out = $final
}
if (-not $inserted) {
    foreach ($ml in $mcpLines) { $out.Add($ml) }
}

[System.IO.File]::WriteAllLines($ConfigPath, $out, [System.Text.UTF8Encoding]::new($false))
Write-Host "[OK] mcp_servers merged into $ConfigPath"
