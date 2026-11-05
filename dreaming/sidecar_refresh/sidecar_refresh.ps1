# sidecar_refresh.ps1 - dedicated windowless refresh for the semantic embedding sidecar.
# PREPARED 2026-07-30, NOT ARMED. Register only with the owner's yes (see README.md).
#
# What it does (the proven runbook, embedding_sidecar_stale_box_rebuild_2026_07_24):
#   1. Gate: skip unless embedding_coverage says missing > 40, or stale > 0, or sidecar age > 36h.
#   2. Gate: skip if a dream is running (dream.lock) or the box reads distress/throttle.
#   3. Ship current authority npz to the box as home_current.npz (INCREMENTAL reuse - minutes -> seconds).
#   4. tar the memory corpus -> box encode as aiwork (nice 12) -> retrieve staged npz.
#   5. publish_memory_embeddings.py --staged (atomic content-addressed pointer swap).
#      On the moved-corpus gate refusal, retry the whole cycle ONCE (other agents file continuously).
# Every skip and outcome is journaled to logs\sidecar_refresh_YYYYMMDD_HHMMSS.log.

$ErrorActionPreference = "Stop"
$D = "$env:USERPROFILE\.claude\dreaming"
$MEM = "$env:USERPROFILE\.claude\projects\{{BRAIN_SLUG}}\memory"
$KEY = "$env:USERPROFILE\.ssh\<remote-box>-key.pem"
$BOX = "ubuntu@<host-ip>"
$LOG = Join-Path $D ("logs\sidecar_refresh_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")
$PY = "C:\Python314\python.exe"
$env:PYTHONUTF8 = "1"

function Log($m) { $line = (Get-Date -Format "HH:mm:ss") + " " + $m; Add-Content -Path $LOG -Value $line -Encoding utf8 }

Set-Location $D
Log "sidecar_refresh start"

# --- gates -------------------------------------------------------------
if (Test-Path (Join-Path $D "dream.lock")) { Log "SKIP: dream.lock held"; exit 0 }
try {
    $state = Get-Content "$env:USERPROFILE\.claude\compute\compute_state.json" -Raw -Encoding utf8 | ConvertFrom-Json
    if ($state.status -eq "distress" -or $state.mode -eq "throttle") { Log ("SKIP: compute " + $state.status + "/" + $state.mode); exit 0 }
} catch { Log "compute state unreadable - proceeding politely" }

$cov = & $PY (Join-Path $D "embedding_coverage.py") 2>$null | Out-String
Log ("coverage: " + ($cov.Trim() -split "`n")[-1])
$needs = $false
if ($cov -match "missing=(\d+)") { if ([int]$Matches[1] -gt 40) { $needs = $true } }
if ($cov -match "stale=(\d+)")   { if ([int]$Matches[1] -gt 0)  { $needs = $true } }
if ($cov -match "age=([\d.]+)h") { if ([double]$Matches[1] -gt 36) { $needs = $true } }
if (-not $needs) { Log "SKIP: coverage healthy (missing<=40, stale=0, age<=36h)"; exit 0 }

# --- one tight cycle, retried once on the moved-corpus refusal ---------
function Invoke-Cycle {
    # ship current authority for incremental reuse
    $cur = (Get-Content (Join-Path $D ".embedding_current.json") -Raw -Encoding utf8 | ConvertFrom-Json).sidecar
    if ($cur -and (Test-Path $cur)) {
        & scp.exe -q -i $KEY -o StrictHostKeyChecking=no $cur "${BOX}:/tmp/home_current.npz"
        & ssh.exe -i $KEY -o StrictHostKeyChecking=no $BOX "sudo cp /tmp/home_current.npz /srv/aiwork/brainembed/home_current.npz; sudo chown aiwork:aiwork /srv/aiwork/brainembed/home_current.npz; rm -f /tmp/home_current.npz"
        Log ("shipped authority for reuse: " + $cur)
    } else { Log "no current authority npz - full encode" }

    $tgz = Join-Path $env:TEMP "sidecar_corpus.tgz"
    if (Test-Path $tgz) { Remove-Item $tgz -Force }
    & tar.exe -czf $tgz -C $MEM --exclude=dreams --exclude=topics --exclude=prompts "*.md"
    if ($LASTEXITCODE -ne 0) { throw "tar failed" }
    & scp.exe -q -i $KEY -o StrictHostKeyChecking=no $tgz "${BOX}:/tmp/corpus2.tgz"
    & ssh.exe -i $KEY -o StrictHostKeyChecking=no $BOX "sudo cp /tmp/corpus2.tgz /srv/aiwork/brainembed/refresh_corpus.tgz && sudo chown aiwork:aiwork /srv/aiwork/brainembed/refresh_corpus.tgz && rm -f /tmp/corpus2.tgz && sudo -u aiwork sh -c 'cd /srv/aiwork/brainembed && rm -f REFRESH.marker refresh_next.npz && sh refresh_driver.sh' && sudo -u aiwork cat /srv/aiwork/brainembed/REFRESH.marker && sudo -u aiwork cp /srv/aiwork/brainembed/refresh_next.npz /tmp/refresh_next.npz && sudo chown ubuntu /tmp/refresh_next.npz"
    if ($LASTEXITCODE -ne 0) { throw "box encode failed" }
    $staged = Join-Path $D "memory_embeddings.staged_auto.npz"
    & scp.exe -q -i $KEY -o StrictHostKeyChecking=no "${BOX}:/tmp/refresh_next.npz" $staged
    & ssh.exe -i $KEY -o StrictHostKeyChecking=no $BOX "rm -f /tmp/refresh_next.npz; sudo -u aiwork rm -f /srv/aiwork/brainembed/home_current.npz /srv/aiwork/brainembed/refresh_next.npz"
    Log "staged retrieved"
    $pub = & $PY (Join-Path $D "publish_memory_embeddings.py") --staged $staged 2>&1 | Out-String
    Log ("publish: " + $pub.Trim())
    return ($pub -notmatch "failed")
}

$ok = Invoke-Cycle
if (-not $ok) { Log "publish refused (corpus moved) - one retry"; $ok = Invoke-Cycle }
$cov2 = & $PY (Join-Path $D "embedding_coverage.py") 2>$null | Out-String
Log ("final coverage: " + ($cov2.Trim() -split "`n")[-1])
if ($ok) { Log "sidecar_refresh DONE" } else { Log "sidecar_refresh FAILED after retry - manual attention" }
