Clear-Host
$ErrorActionPreference = 'Stop'

# Read-only Poi state snapshot collector for Windows PowerShell 5.1.
# Allowed MCP tools: poi_describe, poi_get, poi_lookup only. No game actions.

$Endpoint = 'http://127.0.0.1:12450/mcp'
$LogPath = Join-Path $PSScriptRoot 'poi-ui-snapshots.jsonl'
$script:RequestId = 0
$script:SessionId = $null
$script:ProtocolVersion = '2025-03-26'
$script:Initialized = $false

function Invoke-McpPost {
    param([Parameter(Mandatory = $true)][hashtable]$Payload, [switch]$Notification)
    $headers = @{ Accept = 'application/json, text/event-stream' }
    if ($script:SessionId) { $headers['Mcp-Session-Id'] = $script:SessionId }
    if ($script:Initialized) { $headers['MCP-Protocol-Version'] = $script:ProtocolVersion }
    $body = $Payload | ConvertTo-Json -Depth 30 -Compress
    $response = Invoke-WebRequest -Uri $Endpoint -Method Post -ContentType 'application/json' -Headers $headers -Body $body -UseBasicParsing -TimeoutSec 8
    if ($response.Headers['Mcp-Session-Id']) { $script:SessionId = [string]$response.Headers['Mcp-Session-Id'] }
    $text = [string]$response.Content
    if ([string]::IsNullOrWhiteSpace($text)) {
        if ($Notification) { return $null }
        throw 'MCP returned an empty response.'
    }
    if ([string]$response.Headers['Content-Type'] -match 'text/event-stream') {
        $dataLines = New-Object System.Collections.Generic.List[string]
        $messages = New-Object System.Collections.Generic.List[object]
        foreach ($line in ($text -split '\r?\n')) {
            if ($line.StartsWith('data:')) { $dataLines.Add($line.Substring(5).TrimStart()) }
            elseif ([string]::IsNullOrWhiteSpace($line) -and $dataLines.Count -gt 0) {
                $joined = [string]::Join([Environment]::NewLine, $dataLines.ToArray())
                $dataLines.Clear()
                if ($joined -ne '[DONE]') {
                    try { $messages.Add(($joined | ConvertFrom-Json -ErrorAction Stop)) }
                    catch { throw 'MCP event stream contained malformed JSON.' }
                }
            }
        }
        if ($messages.Count -eq 0) { throw 'MCP event stream contained no JSON-RPC message.' }
        $parsed = $null
        foreach ($message in $messages) { if ($null -ne $message.id) { $parsed = $message; break } }
        if ($null -eq $parsed) { $parsed = $messages[$messages.Count - 1] }
    } else {
        try { $parsed = $text | ConvertFrom-Json -ErrorAction Stop }
        catch { throw 'MCP returned malformed JSON.' }
    }
    if ($Notification) { return $parsed }
    return $parsed
}

function Invoke-McpRequest {
    param([Parameter(Mandatory = $true)][string]$Method, [hashtable]$Params = @{})
    $script:RequestId++
    $id = $script:RequestId
    $response = Invoke-McpPost -Payload @{ jsonrpc = '2.0'; id = $id; method = $Method; params = $Params }
    if ($null -eq $response -or [int]$response.id -ne $id) { throw "Invalid MCP response ID for $Method." }
    if ($response.error) { throw "MCP $Method failed: $($response.error | ConvertTo-Json -Compress -Depth 8)" }
    return $response.result
}

function Get-ToolValue {
    param([Parameter(Mandatory = $true)]$ToolResult, [Parameter(Mandatory = $true)][string]$ToolName)
    if ($ToolResult.isError -eq $true) { throw "$ToolName returned a tool error." }
    if ($null -ne $ToolResult.structuredContent) { return $ToolResult.structuredContent }
    $contentItems = @($ToolResult.content | Where-Object { $_.type -eq 'text' -and $_.text -is [string] })
    if ($contentItems.Count -ne 1) { throw "$ToolName returned missing or ambiguous text content." }
    try { return ($contentItems[0].text | ConvertFrom-Json -ErrorAction Stop) }
    catch { throw "$ToolName returned non-JSON text." }
}

function Invoke-ReadTool {
    param([Parameter(Mandatory = $true)][string]$Name, [Parameter(Mandatory = $true)][hashtable]$Arguments)
    if ($Name -notin @('poi_describe', 'poi_get', 'poi_lookup')) { throw "Blocked non-read-only tool: $Name" }
    $result = Invoke-McpRequest -Method 'tools/call' -Params @{ name = $Name; arguments = $Arguments }
    return (Get-ToolValue -ToolResult $result -ToolName $Name)
}

function Initialize-Poi {
    $health = Invoke-RestMethod -Uri 'http://127.0.0.1:12450/health' -Method Get -TimeoutSec 5
    if ($health.name -ne 'poi-nanodesu-mcp' -or $health.status -ne 'ok' -or $health.storeReady -ne $true) {
        throw 'Poi MCP health check failed or Redux store is not ready.'
    }
    $init = Invoke-McpRequest -Method 'initialize' -Params @{
        protocolVersion = $script:ProtocolVersion
        capabilities = @{}
        clientInfo = @{ name = 'poi-ui-snapshot-readonly'; version = '1.0.0' }
    }
    if ($init.protocolVersion) { $script:ProtocolVersion = [string]$init.protocolVersion }
    $script:Initialized = $true
    $null = Invoke-McpPost -Payload @{ jsonrpc = '2.0'; method = 'notifications/initialized' } -Notification
    $tools = Invoke-McpRequest -Method 'tools/list'
    $toolNames = @($tools.tools | ForEach-Object { [string]$_.name })
    foreach ($required in @('poi_describe', 'poi_get', 'poi_lookup')) {
        if ($toolNames -notcontains $required) { throw "Required read-only tool missing: $required" }
    }
}

function Get-PoiSnapshot {
    $null = Invoke-ReadTool -Name 'poi_describe' -Arguments @{ path = 'info.fleets' }
    $fleetResult = Invoke-ReadTool -Name 'poi_get' -Arguments @{ path = 'info.fleets' }
    $shipResult = Invoke-ReadTool -Name 'poi_get' -Arguments @{ path = 'info.ships' }
    if ($fleetResult.kind -ne 'array' -or $fleetResult.truncated -eq $true -or $fleetResult.items -isnot [System.Array]) {
        throw 'info.fleets response was invalid or truncated.'
    }
    if ($shipResult.kind -ne 'object-map' -or $shipResult.truncated -eq $true -or ($shipResult.items -isnot [System.Collections.IDictionary] -and $shipResult.items -isnot [pscustomobject])) {
        throw 'info.ships response was invalid or truncated.'
    }
    $ships = @{}
    if ($shipResult.items -is [System.Collections.IDictionary]) {
        foreach ($key in $shipResult.items.Keys) { $ships[[string]$key] = $shipResult.items[$key] }
    } else {
        foreach ($property in $shipResult.items.PSObject.Properties) { $ships[[string]$property.Name] = $property.Value }
    }
    $fleetRecords = New-Object System.Collections.Generic.List[object]
    for ($i = 0; $i -lt $fleetResult.items.Count; $i++) {
        $fleet = $fleetResult.items[$i]
        if ($null -eq $fleet) { continue }
        $mission = @()
        if ($null -ne $fleet.api_mission) { $mission = @($fleet.api_mission) }
        $shipSlots = New-Object System.Collections.Generic.List[object]
        if ($fleet.api_ship -is [System.Array]) {
            foreach ($shipId in $fleet.api_ship) {
                if ($null -eq $shipId -or [int]$shipId -eq -1) { continue }
                $ship = $ships[[string]$shipId]
                if ($null -ne $ship) {
                    $shipSlots.Add([pscustomobject]@{
                        InstanceId = $shipId; MasterId = $ship.api_ship_id; Level = $ship.api_lv
                        Fuel = $ship.api_fuel; Ammo = $ship.api_bull; Condition = $ship.api_cond
                    })
                } else {
                    $shipSlots.Add([pscustomobject]@{ InstanceId = $shipId; MissingFromShipMap = $true })
                }
            }
        }
        $fleetRecords.Add([pscustomobject]@{
            FleetArrayIndex = $i; FleetId = $fleet.api_id; FleetName = $fleet.api_name
            FleetNameId = $fleet.api_name_id; FlagshipInstanceId = $fleet.api_flagship
            MissionRaw = $mission; ShipSlots = @($shipSlots.ToArray())
        })
    }
    return [pscustomobject]@{
        ObservedAt = (Get-Date).ToString('o'); Endpoint = $Endpoint
        FleetCount = $fleetRecords.Count; ShipInstanceCount = $ships.Count
        Fleets = @($fleetRecords.ToArray())
    }
}

try {
    Initialize-Poi
    Write-Host 'Poi read-only snapshot collector ready.' -ForegroundColor Cyan
    Write-Host "Log: $LogPath"
    Write-Host 'At each game screen, press Enter to capture. Type Q then Enter to quit.'
    Write-Host 'No clicks, dispatches, reward claims, or game-state changes are performed.'
    while ($true) {
        $choice = Read-Host 'Enter = capture current state; Q = quit'
        if ($choice -match '^[Qq]$') { break }
        try {
            $snapshot = Get-PoiSnapshot
            $json = $snapshot | ConvertTo-Json -Depth 30 -Compress
            Add-Content -LiteralPath $LogPath -Value $json -Encoding UTF8
            Write-Host ("CAPTURED {0} | fleets={1} | shipInstances={2}" -f $snapshot.ObservedAt, $snapshot.FleetCount, $snapshot.ShipInstanceCount) -ForegroundColor Green
            foreach ($fleet in $snapshot.Fleets) {
                Write-Host ("  FleetIndex={0} FleetId={1} Name={2} api_mission={3}" -f $fleet.FleetArrayIndex, $fleet.FleetId, $fleet.FleetName, ($fleet.MissionRaw | ConvertTo-Json -Compress))
            }
        } catch {
            Write-Host ("CAPTURE FAILED: {0}" -f $_.Exception.Message) -ForegroundColor Red
        }
    }
    $finalReport = "TASK: POI_READ_ONLY_UI_SNAPSHOT" + [Environment]::NewLine +
        "RESULT: SCRIPT_FINISHED" + [Environment]::NewLine +
        "LOG_PATH: $LogPath" + [Environment]::NewLine +
        "GAME_ACTIONS: NONE" + [Environment]::NewLine +
        "GITHUB_SYNC: NOT_PERFORMED" + [Environment]::NewLine +
        "NOTE: Script completion does not validate an expedition loop."
} catch {
    $finalReport = "TASK: POI_READ_ONLY_UI_SNAPSHOT" + [Environment]::NewLine +
        "RESULT: FAILED" + [Environment]::NewLine +
        "ERROR: $($_.Exception.Message)" + [Environment]::NewLine +
        "GAME_ACTIONS: NONE" + [Environment]::NewLine +
        "GITHUB_SYNC: NOT_PERFORMED"
}

try {
    $candidateReport = $finalReport + [Environment]::NewLine + 'CLIPBOARD_VERIFICATION: PASS'
    Set-Clipboard -Value $candidateReport -ErrorAction Stop
    $clipboardReadback = Get-Clipboard -Raw -ErrorAction Stop
    if ($clipboardReadback -ceq $candidateReport) {
        $finalReport = $candidateReport
    } else {
        $finalReport = $finalReport + [Environment]::NewLine + 'CLIPBOARD_VERIFICATION: FAILED (content mismatch)'
        Set-Clipboard -Value $finalReport -ErrorAction Stop
        $failedReadback = Get-Clipboard -Raw -ErrorAction Stop
        if ($failedReadback -cne $finalReport) {
            $finalReport = $finalReport + [Environment]::NewLine + 'FINAL_REPORT_CLIPBOARD_READBACK: FAILED'
        }
    }
} catch {
    $finalReport = $finalReport + [Environment]::NewLine + "CLIPBOARD_VERIFICATION: FAILED ($($_.Exception.Message))"
}
Clear-Host
Write-Host $finalReport -ForegroundColor Magenta
