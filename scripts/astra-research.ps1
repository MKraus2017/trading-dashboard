#requires -Version 5.1
<#
.SYNOPSIS
Scoped, read-only Astra research access. This script cannot place orders.
.DESCRIPTION
Enroll generates a token locally, protects it with Windows DPAPI CurrentUser,
and prints only its SHA-256 digest for the server configuration. Read retrieves
one complete immutable snapshot and its history pages from the fixed HTTPS
service. Credentials and captures must stay outside this repository.
.EXAMPLE
powershell.exe -NoProfile -File scripts/astra-research.ps1 -Action SelfTest
.EXAMPLE
powershell.exe -NoProfile -File scripts/astra-research.ps1 -Action Enroll
.EXAMPLE
powershell.exe -NoProfile -File scripts/astra-research.ps1 -Action Read
#>
[CmdletBinding()]
param(
    [ValidateSet('Enroll', 'Read', 'SelfTest')]
    [string] $Action = 'Read',
    [string] $CredentialPath,
    [string] $OutputDirectory,
    [switch] $Rotate
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:Origin = 'https://trading-dashboard-6n5w.onrender.com'
$script:ApiPath = '/api/astra/research/v1'
$script:MaxResponseBytes = 20 * 1024 * 1024
$script:MaxTotalBytes = 64 * 1024 * 1024
$script:MaxRows = 40000
$script:PageSize = 500
$script:Utf8 = New-Object System.Text.UTF8Encoding($false, $true)
$script:RepoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$script:DefaultAccessRoot = Join-Path (Split-Path -Parent $script:RepoRoot) 'astra-access'

function Stop-Research([string] $Message) {
    $failure = New-Object System.InvalidOperationException($Message)
    $failure.Data['AstraSafe'] = $true
    throw $failure
}

function Get-Field($Object, [string] $Name) {
    if ($null -eq $Object -or $null -eq $Object.PSObject.Properties[$Name]) {
        Stop-Research 'The research response is missing a required field.'
    }
    return ,$Object.PSObject.Properties[$Name].Value
}

function Get-UnixNow {
    return [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
}

function Test-FiniteNumber($Value) {
    if ($null -eq $Value -or $Value -is [string] -or $Value -is [bool]) { return $false }
    if ($Value -isnot [ValueType]) { return $false }
    try { $number = [double] $Value } catch { return $false }
    return -not ([double]::IsNaN($number) -or [double]::IsInfinity($number))
}

function Get-RowCount($Value) {
    if (-not (Test-FiniteNumber $Value) -or [double] $Value -lt 0 -or
        [double] $Value -gt $script:MaxRows -or [math]::Floor([double] $Value) -ne [double] $Value) {
        Stop-Research 'The research response has an invalid or excessive history count.'
    }
    return [int] $Value
}

function Resolve-PrivatePath([string] $Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { Stop-Research 'A local output path is required.' }
    $full = [System.IO.Path]::GetFullPath($Path)
    $repoPrefix = $script:RepoRoot.TrimEnd([char[]] @('\', '/')) + [System.IO.Path]::DirectorySeparatorChar
    if ($full.Equals($script:RepoRoot, [StringComparison]::OrdinalIgnoreCase) -or
        $full.StartsWith($repoPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        Stop-Research 'Credentials and research captures must be stored outside the repository.'
    }
    if ($full.StartsWith('\\', [StringComparison]::Ordinal)) {
        Stop-Research 'Use a local Windows path, not a network share, for research credentials and captures.'
    }
    $ancestor = $full
    while (-not [string]::IsNullOrEmpty($ancestor)) {
        if ([System.IO.File]::Exists($ancestor) -or [System.IO.Directory]::Exists($ancestor)) {
            if (([System.IO.File]::GetAttributes($ancestor) -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                Stop-Research 'Research storage paths must not pass through a symbolic link or directory junction.'
            }
        }
        $ancestor = [System.IO.Path]::GetDirectoryName($ancestor)
    }
    return $full
}

function New-PrivateFileAcl {
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $acl = New-Object Security.AccessControl.FileSecurity
    $acl.SetOwner($sid)
    $acl.SetAccessRuleProtection($true, $false)
    $rule = New-Object Security.AccessControl.FileSystemAccessRule(
        $sid, [Security.AccessControl.FileSystemRights]::FullControl,
        [Security.AccessControl.AccessControlType]::Allow)
    $acl.AddAccessRule($rule)
    return $acl
}

function Set-PrivateFileAcl([string] $Path) {
    $acl = New-PrivateFileAcl
    if ($PSVersionTable.PSEdition -eq 'Desktop') { [System.IO.File]::SetAccessControl($Path, $acl) }
    else { [System.IO.FileSystemAclExtensions]::SetAccessControl((New-Object System.IO.FileInfo($Path)), $acl) }
}

function Write-PrivateAtomicFile([string] $Path, [string] $Content, [bool] $AllowReplace = $false) {
    $full = Resolve-PrivatePath $Path
    $parent = [System.IO.Path]::GetDirectoryName($full)
    [void] [System.IO.Directory]::CreateDirectory($parent)
    if ([System.IO.File]::Exists($full) -and -not $AllowReplace) {
        Stop-Research 'The target file already exists; it has not been overwritten.'
    }
    $temporary = Join-Path $parent ('.astra-' + [guid]::NewGuid().ToString('N') + '.tmp')
    $stream = $null
    try {
        $privateAcl = New-PrivateFileAcl
        if ($PSVersionTable.PSEdition -eq 'Desktop') {
            $stream = New-Object System.IO.FileStream(
                $temporary, [System.IO.FileMode]::CreateNew, [Security.AccessControl.FileSystemRights]::FullControl,
                [System.IO.FileShare]::None, 4096, [System.IO.FileOptions]::None, $privateAcl)
        } else {
            $stream = [System.IO.FileSystemAclExtensions]::Create(
                (New-Object System.IO.FileInfo($temporary)), [System.IO.FileMode]::CreateNew,
                [Security.AccessControl.FileSystemRights]::FullControl, [System.IO.FileShare]::None,
                4096, [System.IO.FileOptions]::None, $privateAcl)
        }
        $bytes = $script:Utf8.GetBytes($Content)
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
        $stream.Dispose()
        $stream = $null
        Set-PrivateFileAcl $temporary
        if ([System.IO.File]::Exists($full)) {
            if (-not $AllowReplace) { Stop-Research 'The target appeared during the write; it has not been overwritten.' }
            $backup = $full + '.previous-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffffffZ')
            [System.IO.File]::Replace($temporary, $full, $backup)
            Set-PrivateFileAcl $backup
        } else {
            [System.IO.File]::Move($temporary, $full)
        }
        Set-PrivateFileAcl $full
    } finally {
        if ($null -ne $stream) { $stream.Dispose() }
        if ([System.IO.File]::Exists($temporary)) { [System.IO.File]::Delete($temporary) }
    }
    return $full
}

function Get-TokenDigest([string] $Token) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $digest = $sha.ComputeHash($script:Utf8.GetBytes($Token)) } finally { $sha.Dispose() }
    return ([BitConverter]::ToString($digest)).Replace('-', '').ToLowerInvariant()
}

function New-ResearchCredential([string] $Path, [bool] $AllowRotate) {
    $full = Resolve-PrivatePath $Path
    if ([System.IO.File]::Exists($full) -and -not $AllowRotate) {
        Stop-Research 'The credential already exists. Use -Rotate only when intentionally rotating the server digest too.'
    }
    Add-Type -AssemblyName System.Security
    $random = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($random) } finally { $rng.Dispose() }
    $token = [Convert]::ToBase64String($random).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    [Array]::Clear($random, 0, $random.Length)
    $plain = $script:Utf8.GetBytes($token)
    try {
        $protected = [Security.Cryptography.ProtectedData]::Protect(
            $plain, $null, [Security.Cryptography.DataProtectionScope]::CurrentUser)
        $digest = Get-TokenDigest $token
        $envelope = [ordered] @{
            version = 1
            origin = $script:Origin
            scope = 'astra_research_read_only'
            protection = 'DPAPI_CurrentUser'
            created_at = [DateTime]::UtcNow.ToString('o')
            protected_token = [Convert]::ToBase64String($protected)
        }
        $saved = Write-PrivateAtomicFile $full ($envelope | ConvertTo-Json -Depth 5 -Compress) $AllowRotate
    } finally {
        [Array]::Clear($plain, 0, $plain.Length)
        $token = $null
    }
    return [ordered] @{
        ok = $true; action = 'Enroll'; credential_path = $saved
        server_environment_key = 'ASTRA_RESEARCH_TOKEN_SHA256'; sha256 = $digest
        rotated = $AllowRotate
        note = 'Only the digest belongs on Render. The token remains DPAPI-protected on this Windows account.'
    }
}

function Read-ResearchToken([string] $Path) {
    $full = Resolve-PrivatePath $Path
    if (-not [System.IO.File]::Exists($full)) { Stop-Research 'No local research credential is enrolled yet.' }
    $file = New-Object System.IO.FileInfo($full)
    if ($file.Length -gt 16384) { Stop-Research 'The protected credential file is invalid.' }
    try { $credential = [System.IO.File]::ReadAllText($full, $script:Utf8) | ConvertFrom-Json }
    catch { Stop-Research 'The protected credential file cannot be read.' }
    if ((Get-Field $credential 'version') -ne 1 -or
        (Get-Field $credential 'origin') -cne $script:Origin -or
        (Get-Field $credential 'scope') -cne 'astra_research_read_only' -or
        (Get-Field $credential 'protection') -cne 'DPAPI_CurrentUser') {
        Stop-Research 'The protected credential does not match this research service.'
    }
    Add-Type -AssemblyName System.Security
    $plain = $null
    try {
        $encrypted = [Convert]::FromBase64String((Get-Field $credential 'protected_token'))
        $plain = [Security.Cryptography.ProtectedData]::Unprotect(
            $encrypted, $null, [Security.Cryptography.DataProtectionScope]::CurrentUser)
        $token = $script:Utf8.GetString($plain)
        if ($token -cnotmatch '^[A-Za-z0-9_-]{43}$') { Stop-Research 'The protected credential has an invalid token format.' }
        return $token
    } catch {
        Stop-Research 'The credential cannot be decrypted by this Windows user on this computer.'
    } finally {
        if ($null -ne $plain) { [Array]::Clear($plain, 0, $plain.Length) }
    }
}

function Invoke-ResearchRequest([string] $RelativePath, [string] $Token, $Budget) {
    # Only callers below construct these two paths. Tokens are never accepted in a URL.
    if ($RelativePath -cnotmatch '^/api/astra/research/v1/(snapshot|history\?[A-Za-z0-9%_=&.-]+)$') {
        Stop-Research 'The client refused an endpoint outside the research API.'
    }
    if ((Get-UnixNow) - $Budget.started_at -gt 480) { Stop-Research 'The research capture exceeded its total timeout.' }
    $request = [System.Net.HttpWebRequest]::Create($script:Origin + $RelativePath)
    $request.Method = 'GET'
    $request.AllowAutoRedirect = $false
    $request.Timeout = 30000
    $request.ReadWriteTimeout = 30000
    $request.UserAgent = 'AstraResearchReadOnly/1.0'
    $request.Accept = 'application/json'
    $request.Headers['Authorization'] = 'Bearer ' + $Token
    $response = $null
    $body = $null
    $stream = $null
    try {
        try { $response = $request.GetResponse() }
        catch [System.Net.WebException] {
            if ($null -ne $_.Exception.Response) {
                $status = [int] $_.Exception.Response.StatusCode
                $_.Exception.Response.Close()
                Stop-Research ('The research API returned HTTP ' + $status + '. No complete capture was saved.')
            }
            Stop-Research 'The research API could not be reached securely within the timeout.'
        }
        if ([int] $response.StatusCode -ne 200) {
            Stop-Research ('The research API returned HTTP ' + [int] $response.StatusCode + '; redirects are never followed.')
        }
        if ($response.ContentType -notmatch '^application/json(?:\s*;|$)') {
            Stop-Research 'The research API did not return JSON.'
        }
        if ($response.ContentLength -gt $script:MaxResponseBytes) { Stop-Research 'The research response exceeds the safety size limit.' }
        $stream = $response.GetResponseStream()
        $body = New-Object System.IO.MemoryStream
        $buffer = New-Object byte[] 8192
        while (($read = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
            if ((Get-UnixNow) - $Budget.started_at -gt 480) { Stop-Research 'The research capture exceeded its total timeout.' }
            $Budget.bytes = [long] $Budget.bytes + $read
            if (($body.Length + $read) -gt $script:MaxResponseBytes -or $Budget.bytes -gt $script:MaxTotalBytes) {
                Stop-Research 'The research capture exceeds the safety size limit.'
            }
            $body.Write($buffer, 0, $read)
        }
        try { return ($script:Utf8.GetString($body.ToArray()) | ConvertFrom-Json) }
        catch { Stop-Research 'The research API returned malformed UTF-8 or JSON.' }
    } finally {
        if ($null -ne $stream) { $stream.Dispose() }
        if ($null -ne $body) { $body.Dispose() }
        if ($null -ne $response) { $response.Close() }
    }
}

function Assert-Envelope($Envelope) {
    if ((Get-Field $Envelope 'ok') -isnot [bool] -or -not (Get-Field $Envelope 'ok') -or
        -not (Test-FiniteNumber (Get-Field $Envelope 'schema_version')) -or
        (Get-Field $Envelope 'schema_version') -ne 1 -or
        (Get-Field $Envelope 'mode') -cne 'paper_only') {
        Stop-Research 'The research response has an unsupported schema or scope.'
    }
    $identifier = Get-Field $Envelope 'snapshot_id'
    if ($identifier -isnot [string] -or $identifier -cnotmatch '^[a-f0-9]{32}$') {
        Stop-Research 'The research snapshot identifier is invalid.'
    }
    $captured = Get-Field $Envelope 'captured_at'
    $expires = Get-Field $Envelope 'expires_at'
    if (-not (Test-FiniteNumber $captured) -or -not (Test-FiniteNumber $expires) -or
        $captured -le 0 -or $expires -le $captured -or $captured -gt ((Get-UnixNow) + 300)) {
        Stop-Research 'The research snapshot timestamps are invalid.'
    }
    if ($expires -le (Get-UnixNow)) { Stop-Research 'The research snapshot expired; request a fresh complete capture.' }
}

function Get-RowIdentity($Row, [string] $Kind) {
    $version = Get-Field $Row 'version'
    if ($version -isnot [string] -or [string]::IsNullOrEmpty($version) -or $version.Length -gt 128) {
        Stop-Research 'A research history row has an invalid version.'
    }
    $timeField = if ($Kind -ceq 'decisions') { 'bar_end' } else { 'observed_at' }
    $time = Get-Field $Row $timeField
    if (-not (Test-FiniteNumber $time)) { Stop-Research 'A research history row has an invalid timestamp.' }
    $identity = @($version, ([double] $time).ToString('R', [Globalization.CultureInfo]::InvariantCulture))
    if ($Kind -ceq 'decisions') {
        $symbol = Get-Field $Row 'symbol'
        if ($symbol -isnot [string] -or [string]::IsNullOrEmpty($symbol) -or $symbol.Length -gt 128) {
            Stop-Research 'A research decision has an invalid symbol.'
        }
        $identity += $symbol
    }
    return ($identity | ConvertTo-Json -Compress)
}

function Compare-HistoryRows($Left, $Right, [string] $Kind) {
    $timeField = if ($Kind -ceq 'decisions') { 'bar_end' } else { 'observed_at' }
    $timeCompare = ([double] (Get-Field $Left $timeField)).CompareTo([double] (Get-Field $Right $timeField))
    if ($timeCompare -ne 0) { return $timeCompare }
    $versionCompare = [StringComparer]::Ordinal.Compare($Left.version, $Right.version)
    if ($versionCompare -ne 0 -or $Kind -cne 'decisions') { return $versionCompare }
    return [StringComparer]::Ordinal.Compare($Left.symbol, $Right.symbol)
}

function Get-CompleteResearchCapture([scriptblock] $Transport) {
    $snapshot = & $Transport ($script:ApiPath + '/snapshot')
    Assert-Envelope $snapshot
    if ($null -eq (Get-Field $snapshot 'astra') -or $null -eq (Get-Field $snapshot 'reference')) {
        Stop-Research 'The research snapshot is missing a virtual portfolio.'
    }
    $counts = Get-Field $snapshot 'counts'
    $decisionCount = Get-RowCount (Get-Field $counts 'decisions')
    $equityCount = Get-RowCount (Get-Field $counts 'equity')
    if (($decisionCount + $equityCount) -gt $script:MaxRows) {
        Stop-Research 'The research capture exceeds the safety history limit.'
    }
    $history = [ordered] @{}
    foreach ($kind in @('decisions', 'equity')) {
        $expected = Get-RowCount (Get-Field $counts $kind)
        $rows = New-Object 'System.Collections.Generic.List[object]'
        $identities = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
        $cursors = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
        $cursor = $null
        $previousRow = $null
        do {
            $path = $script:ApiPath + '/history?snapshot=' + $snapshot.snapshot_id + '&kind=' + $kind + '&limit=' + $script:PageSize
            if ($null -ne $cursor) { $path += '&cursor=' + [Uri]::EscapeDataString($cursor) }
            $page = & $Transport $path
            Assert-Envelope $page
            if ($page.snapshot_id -cne $snapshot.snapshot_id -or
                [double] $page.captured_at -ne [double] $snapshot.captured_at -or
                [double] $page.expires_at -ne [double] $snapshot.expires_at -or
                (Get-Field $page 'kind') -cne $kind -or
                (Get-RowCount (Get-Field $page 'total')) -ne $expected) {
                Stop-Research 'The history page does not belong to the immutable snapshot.'
            }
            $pageRows = Get-Field $page 'rows'
            if ($pageRows -isnot [System.Array]) { Stop-Research 'The history page rows must be a JSON array.' }
            if ($pageRows.Count -gt $script:PageSize -or ($rows.Count + $pageRows.Count) -gt $expected) {
                Stop-Research 'The history page contains too many rows.'
            }
            foreach ($row in $pageRows) {
                $identity = Get-RowIdentity $row $kind
                if (-not $identities.Add($identity)) { Stop-Research 'Duplicate history rows were returned; the capture was rejected.' }
                if ($null -ne $previousRow -and (Compare-HistoryRows $previousRow $row $kind) -ge 0) {
                    Stop-Research 'History rows are not in the declared chronological order.'
                }
                $rows.Add($row)
                $previousRow = $row
            }
            $next = Get-Field $page 'next_cursor'
            if ($null -ne $next) {
                if ($next -isnot [string] -or [string]::IsNullOrEmpty($next) -or $next.Length -gt 2048 -or
                    $pageRows.Count -eq 0 -or $rows.Count -ge $expected -or -not $cursors.Add($next)) {
                    Stop-Research 'The history cursor did not make valid forward progress.'
                }
            }
            $cursor = $next
        } while ($null -ne $cursor)
        if ($rows.Count -ne $expected) { Stop-Research 'The history ended early; no partial capture will be saved.' }
        $history[$kind] = $rows.ToArray()
    }
    Assert-Envelope $snapshot
    return [ordered] @{
        schema_version = 1; source_origin = $script:Origin
        retrieved_at = [DateTime]::UtcNow.ToString('o')
        snapshot = $snapshot; history = $history
    }
}

function Invoke-ResearchSelfTest {
    $now = Get-UnixNow
    $fixture = [pscustomobject] @{
        ok = $true; schema_version = 1; mode = 'paper_only'
        snapshot_id = ('a' * 32); captured_at = $now; expires_at = ($now + 600)
        astra = [pscustomobject] @{ version = 'test_v3'; cash = 1000 }
        reference = [pscustomobject] @{ cash = 1000 }
        counts = [pscustomobject] @{ decisions = 2; equity = 1 }
    }
    $rows = @(
        [pscustomobject] @{ version = 'test_v3'; bar_end = 100; symbol = 'BTC-USDT' },
        [pscustomobject] @{ version = 'test_v3'; bar_end = 101; symbol = 'ETH-USDT' }
    )
    $state = [pscustomobject] @{ fault = ''; calls = 0 }
    $apiPath = $script:ApiPath
    $transport = {
        param($path)
        $state.calls++
        if ($path -ceq ($apiPath + '/snapshot')) { return $fixture }
        $kind = if ($path -match 'kind=decisions') { 'decisions' } else { 'equity' }
        $second = $path -match 'cursor=next-one'
        $page = [pscustomobject] @{
            ok = $true; schema_version = 1; mode = 'paper_only'
            snapshot_id = $fixture.snapshot_id; captured_at = $fixture.captured_at; expires_at = $fixture.expires_at
            kind = $kind; total = $(if ($kind -ceq 'decisions') { 2 } else { 1 })
            rows = @($(if ($kind -ceq 'decisions') { $rows[[int] $second] } else { [pscustomobject] @{ version = 'test_v3'; observed_at = 101; equity = 1000 } }))
            next_cursor = $(if ($kind -ceq 'decisions' -and -not $second) { 'next-one' } else { $null })
        }
        if ($kind -ceq 'decisions') {
            switch ($state.fault) {
                'scope' { $page.mode = 'real_money' }
                'identity' { $page.snapshot_id = 'b' * 32 }
                'captured' { $page.captured_at++ }
                'total' { $page.total++ }
                'early' { $page.next_cursor = $null }
                'duplicate' { $page.rows = @($rows[0]) }
                'cursor' { $page.next_cursor = 'next-one' }
                'empty' { $page.rows = @() }
                'expired' { $page.expires_at = $now - 1 }
                'missing' { $page.PSObject.Properties.Remove('rows') }
                'order' { if ($second) { $page.rows = @([pscustomobject] @{ version = 'test_v3'; bar_end = 99; symbol = 'ETH-USDT' }) } }
                'notarray' { $page.rows = $rows[0] }
            }
        }
        return $page
    }.GetNewClosure()
    $capture = Get-CompleteResearchCapture $transport
    if ($capture.history.decisions.Count -ne 2 -or $capture.history.equity.Count -ne 1 -or $state.calls -ne 4) {
        Stop-Research 'Self-test failed: valid pagination.'
    }
    $passed = 1
    foreach ($fault in @('scope', 'identity', 'captured', 'total', 'early', 'duplicate', 'cursor', 'empty', 'expired', 'missing', 'order', 'notarray')) {
        $state.fault = $fault
        $rejected = $false
        try { $null = Get-CompleteResearchCapture $transport } catch { $rejected = $true }
        if (-not $rejected) { Stop-Research ('Self-test failed: ' + $fault + '.') }
        $passed++
    }
    $rejected = $false
    try { $null = Resolve-PrivatePath (Join-Path $script:RepoRoot 'credential.dpapi') } catch { $rejected = $true }
    if (-not $rejected) { Stop-Research 'Self-test failed: repository credential protection.' }
    $passed++
    if ((Get-TokenDigest 'abc') -cne 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad') {
        Stop-Research 'Self-test failed: token digest.'
    }
    $passed++
    Add-Type -AssemblyName System.Security
    $plain = $script:Utf8.GetBytes('offline DPAPI self-test; not an enrolled credential')
    $encrypted = [Security.Cryptography.ProtectedData]::Protect($plain, $null, [Security.Cryptography.DataProtectionScope]::CurrentUser)
    $decrypted = [Security.Cryptography.ProtectedData]::Unprotect($encrypted, $null, [Security.Cryptography.DataProtectionScope]::CurrentUser)
    if ([Convert]::ToBase64String($plain) -cne [Convert]::ToBase64String($decrypted)) { Stop-Research 'Self-test failed: DPAPI round trip.' }
    $encrypted[[math]::Floor($encrypted.Length / 2)] = $encrypted[[math]::Floor($encrypted.Length / 2)] -bxor 1
    $rejected = $false
    try { $null = [Security.Cryptography.ProtectedData]::Unprotect($encrypted, $null, [Security.Cryptography.DataProtectionScope]::CurrentUser) }
    catch { $rejected = $true }
    if (-not $rejected) { Stop-Research 'Self-test failed: DPAPI tamper rejection.' }
    $passed++
    [Array]::Clear($plain, 0, $plain.Length)
    [Array]::Clear($decrypted, 0, $decrypted.Length)
    $passed++
    $closureToken = 'offline-placeholder-not-a-credential'
    $closureBudget = [pscustomobject] @{ bytes = 0; started_at = (Get-UnixNow) }
    $requestClosure = { param($path) Invoke-ResearchRequest $path $closureToken $closureBudget }.GetNewClosure()
    $rejected = $false
    try { $null = & $requestClosure '/not-authorized' }
    catch { $rejected = $_.Exception.Data.Contains('AstraSafe') -and $_.Exception.Message -eq 'The client refused an endpoint outside the research API.' }
    if (-not $rejected) { Stop-Research 'Self-test failed: transport closure and endpoint restriction.' }
    $passed++
    $maxRequests = 2 + [math]::Ceiling($script:MaxRows / $script:PageSize)
    if ($maxRequests -gt 120) { Stop-Research 'Self-test failed: maximum snapshot exceeds the server request budget.' }
    $passed++
    $temporaryDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ('astra-client-test-' + [guid]::NewGuid().ToString('N'))
    $temporaryFile = Join-Path $temporaryDirectory 'nonsecret-fixture.json'
    try {
        $null = Write-PrivateAtomicFile $temporaryFile '{"test":1}'
        $acl = Get-Acl -LiteralPath $temporaryFile
        if (-not $acl.AreAccessRulesProtected -or $acl.Access.Count -ne 1) { Stop-Research 'Self-test failed: private file permissions.' }
        $passed++
        $rejected = $false
        try { $null = Write-PrivateAtomicFile $temporaryFile '{"test":2}' } catch { $rejected = $true }
        if (-not $rejected -or [System.IO.File]::ReadAllText($temporaryFile) -cne '{"test":1}') {
            Stop-Research 'Self-test failed: no-overwrite protection.'
        }
        $passed++
        $null = Write-PrivateAtomicFile $temporaryFile '{"test":2}' $true
        $backups = [System.IO.Directory]::GetFiles($temporaryDirectory, 'nonsecret-fixture.json.previous-*')
        if ($backups.Count -ne 1 -or [System.IO.File]::ReadAllText($backups[0]) -cne '{"test":1}' -or
            [System.IO.File]::ReadAllText($temporaryFile) -cne '{"test":2}') {
            Stop-Research 'Self-test failed: atomic replacement and preserved previous file.'
        }
        $passed++
    } finally {
        if ([System.IO.Directory]::Exists($temporaryDirectory)) {
            foreach ($fixtureFile in [System.IO.Directory]::GetFiles($temporaryDirectory)) { [System.IO.File]::Delete($fixtureFile) }
            [System.IO.Directory]::Delete($temporaryDirectory, $false)
        }
    }
    return [ordered] @{ ok = $true; action = 'SelfTest'; passed = $passed; network_requests = 0; credential_files_written = 0 }
}

try {
    if ($Rotate -and $Action -cne 'Enroll') { Stop-Research '-Rotate is supported only for deliberate credential enrollment.' }
    if ([string]::IsNullOrWhiteSpace($CredentialPath)) { $CredentialPath = Join-Path $script:DefaultAccessRoot 'credential.dpapi' }
    if ([string]::IsNullOrWhiteSpace($OutputDirectory)) { $OutputDirectory = Join-Path $script:DefaultAccessRoot 'captures' }
    switch ($Action) {
        'SelfTest' { $result = Invoke-ResearchSelfTest }
        'Enroll' { $result = New-ResearchCredential $CredentialPath ([bool] $Rotate) }
        'Read' {
            $destination = Resolve-PrivatePath $OutputDirectory
            $token = Read-ResearchToken $CredentialPath
            try {
                $budget = [pscustomobject] @{ bytes = [long] 0; started_at = (Get-UnixNow) }
                $transport = { param($path) Invoke-ResearchRequest $path $token $budget }.GetNewClosure()
                $capture = Get-CompleteResearchCapture $transport
                $json = $capture | ConvertTo-Json -Depth 64 -Compress
                if ($script:Utf8.GetByteCount($json) -gt $script:MaxTotalBytes) { Stop-Research 'The combined capture exceeds the safety size limit.' }
                $filename = 'astra-research-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffffffZ') + '-' + $capture.snapshot.snapshot_id + '.json'
                $saved = Write-PrivateAtomicFile (Join-Path $destination $filename) $json
                $result = [ordered] @{
                    ok = $true; action = 'Read'; capture_path = $saved
                    snapshot_id = $capture.snapshot.snapshot_id; captured_at = $capture.snapshot.captured_at
                    counts = $capture.snapshot.counts; response_bytes = $budget.bytes
                }
            } finally {
                $token = $null
                $transport = $null
            }
        }
    }
    Write-Output ($result | ConvertTo-Json -Depth 8 -Compress)
} catch {
    $safeMessage = 'Research access failed. No complete capture was reported and no credential was printed.'
    if ($Action -ceq 'SelfTest') { $safeMessage = 'Offline self-test error: ' + $_.Exception.Message + ' at line ' + $_.InvocationInfo.ScriptLineNumber }
    if ($_.Exception.Data.Contains('AstraSafe')) { $safeMessage = $_.Exception.Message }
    [Console]::Error.WriteLine(([ordered] @{ ok = $false; action = $Action; error = $safeMessage } | ConvertTo-Json -Compress))
    exit 1
}
