Clear-Host
$ErrorActionPreference = 'Stop'

$report = New-Object System.Collections.Generic.List[string]
$script:cdpId = 0
$script:events = New-Object System.Collections.ArrayList
$ws = $null

function Receive-CdpMessage {
    param(
        [System.Net.WebSockets.ClientWebSocket]$Socket,
        [int]$TimeoutSeconds = 2
    )

    $buffer = New-Object byte[] 32768
    $stream = New-Object System.IO.MemoryStream

    try {
        $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)

        do {
            $remaining = [int][Math]::Max(
                1, [Math]::Ceiling(($deadline - [DateTime]::UtcNow).TotalSeconds)
            )

            $task = $Socket.ReceiveAsync(
                [ArraySegment[byte]]::new($buffer),
                [Threading.CancellationToken]::None
            )

            if (-not $task.Wait($remaining * 1000)) {
                return $null
            }

            $part = $task.GetAwaiter().GetResult()

            if ($part.MessageType -eq [System.Net.WebSockets.WebSocketMessageType]::Close) {
                throw 'CDP WebSocket closed.'
            }

            $stream.Write($buffer, 0, $part.Count)
        } until ($part.EndOfMessage)

        return ([Text.Encoding]::UTF8.GetString($stream.ToArray()) | ConvertFrom-Json)
    }
    finally {
        $stream.Dispose()
    }
}

function Send-CdpCommand {
    param(
        [System.Net.WebSockets.ClientWebSocket]$Socket,
        [string]$Method,
        [hashtable]$Params = @{},
        [int]$TimeoutSeconds = 8
    )

    $script:cdpId++
    $id = $script:cdpId

    $payload = @{
        id = $id
        method = $Method
        params = $Params
    } | ConvertTo-Json -Depth 10 -Compress

    $bytes = [Text.Encoding]::UTF8.GetBytes($payload)

    $Socket.SendAsync(
        [ArraySegment[byte]]::new($bytes),
        [System.Net.WebSockets.WebSocketMessageType]::Text,
        $true,
        [Threading.CancellationToken]::None
    ).GetAwaiter().GetResult()

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)

    while ([DateTime]::UtcNow -lt $deadline) {
        $remaining = [int][Math]::Max(
            1, [Math]::Ceiling(($deadline - [DateTime]::UtcNow).TotalSeconds)
        )

        $message = Receive-CdpMessage -Socket $Socket -TimeoutSeconds $remaining

        if ($null -eq $message) {
            continue
        }

        if ($null -ne $message.id -and [int]$message.id -eq $id) {
            if ($message.error) {
                throw ("{0}: {1}" -f $Method, $message.error.message)
            }
            return $message.result
        }

        [void]$script:events.Add($message)
    }

    throw ("Timed out waiting for {0}." -f $Method)
}

function Get-SafeUrl {
    param([object]$Value)

    $candidate = @($Value) | Select-Object -First 1

    if ($null -eq $candidate -or $candidate -isnot [string]) {
        return '[URL unavailable]'
    }

    try {
        $uri = [System.Uri]::new($candidate)
        return ('{0}://{1}{2}' -f $uri.Scheme, $uri.Authority, $uri.AbsolutePath)
    }
    catch {
        return '[URL omitted]'
    }
}

$report.Add('Poi CDP Execution Context Discovery')
$report.Add(('Timestamp UTC: {0}' -f [DateTime]::UtcNow.ToString('o')))
$report.Add('Game input: NOT SENT')
$report.Add('Runtime.evaluate: NOT USED')
$report.Add('')

try {
    $request = [System.Net.HttpWebRequest]::Create('http://127.0.0.1:9222/json/list')
    $request.Method = 'GET'
    $request.Timeout = 3000
    $request.Proxy = $null
    $request.AllowAutoRedirect = $false

    $response = $request.GetResponse()
    try {
        $reader = New-Object System.IO.StreamReader($response.GetResponseStream())
        try {
            $body = $reader.ReadToEnd()
        }
        finally {
            $reader.Dispose()
        }
    }
    finally {
        $response.Dispose()
    }

    $targets = @($body | ConvertFrom-Json)
    $matches = @(
        $targets | Where-Object {
            $_.type -eq 'page' -and
            ([string]$_.url) -match 'play\.games\.dmm\.com/game/kancolle'
        }
    )

    if ($matches.Count -ne 1) {
        throw ("Expected one game page target; found {0}." -f $matches.Count)
    }

    $target = $matches[0]
    $rawWs = @($target.webSocketDebuggerUrl) | Select-Object -First 1

    if ($rawWs -isnot [string] -or [string]::IsNullOrWhiteSpace($rawWs)) {
        throw 'Target WebSocket URL is missing or invalid.'
    }

    $wsUri = [System.Uri]::new($rawWs)

    if ($wsUri.Scheme -ne 'ws' -or
        $wsUri.Host -notin @('127.0.0.1', 'localhost', '::1') -or
        $wsUri.Port -ne 9222) {
        throw 'Rejected non-loopback debugger endpoint.'
    }

    $ws = New-Object System.Net.WebSockets.ClientWebSocket
    $ws.Options.Proxy = $null
    $ws.ConnectAsync(
        $wsUri, [Threading.CancellationToken]::None
    ).GetAwaiter().GetResult()

    # Enable event delivery. This does not evaluate page JavaScript.
    $null = Send-CdpCommand -Socket $ws -Method 'Runtime.enable'

    # Collect contexts created during Runtime.enable and shortly afterwards.
    $until = [DateTime]::UtcNow.AddSeconds(4)

    while ([DateTime]::UtcNow -lt $until) {
        $left = [int][Math]::Max(
            1, [Math]::Ceiling(($until - [DateTime]::UtcNow).TotalSeconds)
        )

        $message = Receive-CdpMessage -Socket $ws -TimeoutSeconds $left

        if ($null -eq $message) {
            break
        }

        [void]$script:events.Add($message)
    }

    $contexts = New-Object System.Collections.Generic.List[object]

    foreach ($event in $script:events) {
        if ($event.method -ne 'Runtime.executionContextCreated') {
            continue
        }

        $context = $event.params.context
        if ($null -eq $context) {
            continue
        }

        $aux = $context.auxData

        $contexts.Add([pscustomobject]@{
            ContextId = [string]$context.id
            FrameId = [string]$aux.frameId
            IsDefault = [bool]$aux.isDefault
            Name = [string]$context.name
            Origin = Get-SafeUrl ([string]$context.origin)
        })
    }

    # Retrieve the frame tree for a fresh frameId-to-URL mapping.
    $treeResult = Send-CdpCommand -Socket $ws -Method 'Page.getFrameTree'
    $frameRows = New-Object System.Collections.Generic.List[object]

    function Add-FrameNode {
        param($Node, [string]$ParentId)

        if ($null -eq $Node -or $null -eq $Node.frame) {
            return
        }

        $frame = $Node.frame

        $frameRows.Add([pscustomobject]@{
            Id = [string]$frame.id
            ParentId = $ParentId
            Name = [string]$frame.name
            Url = Get-SafeUrl ([string]$frame.url)
        })

        foreach ($child in @($Node.childFrames)) {
            Add-FrameNode -Node $child -ParentId ([string]$frame.id)
        }
    }

    Add-FrameNode -Node $treeResult.frameTree -ParentId ''

    $gameFrames = @(
        $frameRows | Where-Object {
            $_.Name -eq 'htmlWrap' -and
            $_.Url -match 'w14h\.kancolle-server\.com/kcs2/index\.php'
        }
    )

    $report.Add(('CDP endpoint: PASS; page targets: {0}' -f $targets.Count))
    $report.Add(('Runtime.enable: PASS; execution-context events collected: {0}' -f $contexts.Count))
    $report.Add('')
    $report.Add('Target htmlWrap frames:')

    foreach ($frame in $gameFrames) {
        $report.Add(('FrameId={0}; ParentId={1}; URL={2}' -f
            $frame.Id, $frame.ParentId, $frame.Url))

        $matched = @(
            $contexts | Where-Object { $_.FrameId -eq $frame.Id }
        )

        if ($matched.Count -eq 0) {
            $report.Add('Context mapping: NOT OBSERVED')
        }
        else {
            foreach ($ctx in $matched) {
                $report.Add(('ContextId={0}; IsDefault={1}; Name="{2}"; Origin={3}' -f
                    $ctx.ContextId,
                    $ctx.IsDefault,
                    ($ctx.Name -replace '[\r\n"]', ' '),
                    $ctx.Origin))
            }
        }
    }

    if ($gameFrames.Count -eq 0) {
        $report.Add('Target htmlWrap frame: NOT FOUND')
    }

    $report.Add('')
    $report.Add('All observed execution contexts:')

    foreach ($ctx in $contexts) {
        $report.Add(('FrameId={0}; ContextId={1}; IsDefault={2}; Name="{3}"; Origin={4}' -f
            $ctx.FrameId,
            $ctx.ContextId,
            $ctx.IsDefault,
            ($ctx.Name -replace '[\r\n"]', ' '),
            $ctx.Origin))
    }

    $report.Add('')
    if ($gameFrames.Count -eq 1 -and
        @($contexts | Where-Object { $_.FrameId -eq $gameFrames[0].Id }).Count -gt 0) {
        $report.Add('Status: TARGET_CONTEXT_IDENTIFIED')
    }
    elseif ($gameFrames.Count -eq 1) {
        $report.Add('Status: FRAME_FOUND_CONTEXT_NOT_OBSERVED')
    }
    else {
        $report.Add('Status: TARGET_FRAME_NOT_UNIQUELY_IDENTIFIED')
    }
}
catch {
    $report.Add('')
    $report.Add('Status: FAILED_OR_BLOCKED')
    $report.Add(('ErrorType: {0}' -f $_.Exception.GetType().FullName))
    $report.Add(('Error: {0}' -f ($_.Exception.Message -replace '[\r\n]+', ' ')))
}
finally {
    if ($ws) {
        try {
            if ($ws.State -eq [System.Net.WebSockets.WebSocketState]::Open) {
                $ws.CloseAsync(
                    [System.Net.WebSockets.WebSocketCloseStatus]::NormalClosure,
                    'Read-only context discovery complete',
                    [Threading.CancellationToken]::None
                ).GetAwaiter().GetResult()
            }
        }
        catch {}
        $ws.Dispose()
    }
}

$baseReport = $report -join [Environment]::NewLine
$clipboardStatus = 'FAILED'

try {
    Set-Clipboard -Value $baseReport
    if ((Get-Clipboard -Raw) -ceq $baseReport) {
        $clipboardStatus = 'PASS'
    }
}
catch {}

$finalReport = $baseReport + [Environment]::NewLine +
    ('Clipboard verification: {0}' -f $clipboardStatus)

Clear-Host
Write-Host $finalReport -ForegroundColor Magenta
