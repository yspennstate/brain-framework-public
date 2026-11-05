# Atomic filesystem primitives used by run_dream.ps1.
# Keep this source ASCII: it is loaded by the unattended Windows PowerShell runner.

function Open-DreamLease {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)

    $stream = $null
    try {
        # FileShare.None is the kernel-enforced compare-and-set. DeleteOnClose makes
        # the marker disappear even when the process is killed or the machine crashes;
        # an old pre-lease marker is harmless because an unlocked file can be reopened.
        $stream = [System.IO.FileStream]::new(
            $Path,
            [System.IO.FileMode]::OpenOrCreate,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::None,
            4096,
            [System.IO.FileOptions]::DeleteOnClose
        )
    } catch [System.IO.IOException] {
        $nativeCode = $_.Exception.HResult -band 0xffff
        if ($nativeCode -in @(32, 33)) { return $null }
        throw
    }

    try {
        $started = [DateTime]::UtcNow.ToString('o', [Globalization.CultureInfo]::InvariantCulture)
        $payload = "pid=$PID`r`nstarted_utc=$started`r`n"
        $bytes = [Text.Encoding]::ASCII.GetBytes($payload)
        $stream.SetLength(0)
        $stream.Position = 0
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
        return $stream
    } catch {
        if ($null -ne $stream) { $stream.Dispose() }
        throw
    }
}

function Get-DreamNativeErrorCode {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][Exception]$Exception)

    $native = $Exception
    while ($null -ne $native.InnerException) { $native = $native.InnerException }
    return $native.HResult -band 0xffff
}

function Write-AtomicUtf8NoBomLines {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        # AllowEmptyString: marker/report arrays legitimately carry "" spacer lines;
        # without it, binding dies ("empty string") and every gate-block marker is lost.
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][AllowEmptyString()][string[]]$Lines,
        [scriptblock]$BeforePublish
    )

    $fullPath = [IO.Path]::GetFullPath($Path)
    $directory = [IO.Path]::GetDirectoryName($fullPath)
    if (-not [IO.Directory]::Exists($directory)) {
        throw "Atomic publication directory does not exist: $directory"
    }

    $leaf = [IO.Path]::GetFileName($fullPath)
    $temporary = Join-Path $directory ('.' + $leaf + '.' + $PID + '.' + [Guid]::NewGuid().ToString('N') + '.tmp')
    $backup = Join-Path $directory ('.' + $leaf + '.' + $PID + '.' + [Guid]::NewGuid().ToString('N') + '.bak')
    $stream = $null
    $published = $false
    try {
        $text = if ($Lines.Count -eq 0) {
            ''
        } else {
            [string]::Join([Environment]::NewLine, [string[]]$Lines) + [Environment]::NewLine
        }
        $encoding = [Text.UTF8Encoding]::new($false, $true)
        $bytes = $encoding.GetBytes($text)
        $stream = [IO.FileStream]::new(
            $temporary,
            [IO.FileMode]::CreateNew,
            [IO.FileAccess]::Write,
            [IO.FileShare]::None,
            4096,
            [IO.FileOptions]::WriteThrough
        )
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
        $stream.Dispose()
        $stream = $null

        if ($null -ne $BeforePublish) { & $BeforePublish $temporary $fullPath }

        # Keep one fsynced stage across a bounded 496 ms retry window. Readers can
        # briefly deny Windows replace/delete access; only access/sharing/lock
        # conflicts are transient. Every other native code fails immediately.
        $retryDelaysMs = @(16, 32, 64, 128, 256)
        for ($attempt = 0; $attempt -le $retryDelaysMs.Count; $attempt++) {
            try {
                if ([IO.File]::Exists($fullPath)) {
                    # Windows PowerShell's .NET runtime rejects a null backup path
                    # for File.Replace. The same-directory backup keeps the swap
                    # atomic and preserves a complete prior target across a crash.
                    [IO.File]::Replace($temporary, $fullPath, $backup, $true)
                } else {
                    [IO.File]::Move($temporary, $fullPath)
                }
                $published = $true
                break
            } catch {
                $nativeCode = Get-DreamNativeErrorCode -Exception $_.Exception
                $retryable = $nativeCode -in @(5, 32, 33)
                if (-not $retryable -or $attempt -eq $retryDelaysMs.Count) { throw }
                Start-Sleep -Milliseconds $retryDelaysMs[$attempt]
            }
        }
    } finally {
        if ($null -ne $stream) { $stream.Dispose() }
        if (-not $published -and [IO.File]::Exists($temporary)) {
            [IO.File]::Delete($temporary)
        }
        if ([IO.File]::Exists($backup)) {
            try { [IO.File]::Delete($backup) } catch {}
        }
    }
}
