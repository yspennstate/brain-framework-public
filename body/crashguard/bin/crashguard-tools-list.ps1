# Tools list — auto-discovers all *.ps1 in bin/, extracts first-line comment as description.

. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$files = Get-ChildItem "$env:USERPROFILE\CrashGuard\bin\*.ps1" | Sort-Object Name

Write-Output ('=== CrashGuard tools (' + $files.Count + ' files) ===')
Write-Output ''
foreach ($f in $files) {
    # First non-empty non-shebang comment line
    $desc = ''
    Get-Content $f.FullName -TotalCount 5 -ErrorAction SilentlyContinue | ForEach-Object {
        if (-not $desc -and $_ -match '^#\s+(\S.+)') { $desc = $matches[1] }
    }
    $name = $f.BaseName -replace 'crashguard-',''
    Write-Output ('  ' + $name.PadRight(30) + ' ' + $desc)
}
