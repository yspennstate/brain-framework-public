# snapshot_modes.ps1 - re-capture how each agent is CURRENTLY running into the registry's "mode" field, so
# relaunch.ps1 -Faithful keeps reproducing the real setup. Ground truth = the live command line: a '-p' means
# gentle (headless one-shot), no '-p' means interactive (persistent TUI). Run this after you change which
# agents are interactive vs headless. Read/write both use explicit -Encoding UTF8 (never bare Get-Content),
# so a checkpoint with non-ASCII round-trips clean; the BOM is kept on purpose (PS readers need it).
$ErrorActionPreference = 'Continue'
$reg = if ($env:FLEET_REGISTRY_DIR) { $env:FLEET_REGISTRY_DIR } else { "$env:USERPROFILE\.claude\fleet\registry" }
$map = @{}
Get-CimInstance Win32_Process -Filter "name='claude.exe' OR name='codex.exe'" | ForEach-Object {
    $cl = "$($_.CommandLine)"
    if ($cl -match '--resume\s+([0-9a-fA-F]{8}-[0-9a-fA-F-]{27,})') {
        $map[$matches[1]] = if ($cl -match '(^|\s)-p(\s|$)') { 'gentle' } else { 'interactive' }
    }
}
$n = 0
Get-ChildItem "$reg\*.json" -ErrorAction SilentlyContinue | ForEach-Object {
    try { $o = Get-Content $_.FullName -Raw -Encoding UTF8 | ConvertFrom-Json } catch { return }
    if ($o.id -and $map.ContainsKey($o.id) -and $o.mode -ne $map[$o.id]) {
        $o.mode = $map[$o.id]
        $o | ConvertTo-Json | Set-Content -LiteralPath $_.FullName -Encoding UTF8
        "  $($o.id.Substring(0,8))  -> $($o.mode)"
        $n++
    }
}
"snapshot: updated $n registry mode(s) from $($map.Count) live agent(s)"
