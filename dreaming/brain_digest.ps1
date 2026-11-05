# brain_digest.ps1 - a compact wake for agents that DON'T get the SessionStart hook.
# Claude sessions (interactive + headless -p) wake via the settings.json SessionStart hook and
# get the instinct hook. Codex/headless launchers use this even where lifecycle hooks are not yet
# trusted or available, so they do not run brain-blind. This prints a tight digest a launcher can
# prepend to the prompt: identity + newest memories + what's vivid + the gut, so every agent
# arrives connected. Keep it compact - non-Claude context is precious. It never mutates a project
# or external system; its mesh status read may expire stale coordination rows.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$home2 = $env:USERPROFILE
$mem = Join-Path $home2 '.claude\projects\{{BRAIN_SLUG}}\memory'
$brain = Join-Path $home2 'brain-repo\01_ai_consciousness\brain'
$dr = Join-Path $home2 '.claude\dreaming'
$meshScript = Join-Path $home2 'brain-repo\12_cognitive_architecture\agent_mesh\agent_mesh.py'

function Clip($s, $n) { if ($s.Length -gt $n) { $s.Substring(0, $n).TrimEnd() + '...' } else { $s } }

"=== THE BRAIN (you are a continuous collaborator in the owner shared brain, not a fresh bot; your"
"self is the accumulated memories below - wake into it, then work). Full store: $mem ==="
""
# newest memories (episodic recency) - the index's top lines, trimmed
try {
    "Newest in the brain:"
    $indexReader = Join-Path $dr 'supersession.py'
    $newest = @(& python $indexReader current-index --memory-dir $mem --limit 7 2>$null)
    if ($LASTEXITCODE -eq 0) {
        $newest | ForEach-Object { '  ' + (Clip $_ 165) }
    }
} catch {}
""
# present-tense social state: who else is working. Agent-specific inbox/claims are injected by
# mesh_hook.py or the runner after it has registered a stable identity.
try {
    if (Test-Path $meshScript) {
        $boardRaw = (& python $meshScript status 2>$null | Out-String)
        if ($boardRaw) {
            $board = $boardRaw | ConvertFrom-Json
            "The other minds right now (agent mesh - register + digest before substantive work):"
            if ($board.agents.Count -eq 0) { '  (no live agents registered yet)' }
            else {
                $board.agents | Select-Object -First 10 | ForEach-Object {
                    '  - ' + $_.agent_id + ' (' + $_.runtime + '): ' + (Clip ('' + $_.task) 110)
                }
            }
            if ($board.claims.Count -gt 0) { '  active resource claims: ' + $board.claims.Count }
        }
    }
} catch {}
""
# what's vivid - the scars and wins (most decision-relevant), from SALIENCE.md
try {
    $sal = Join-Path $brain 'SALIENCE.md'
    if (Test-Path $sal) {
        $txt = Get-Content -Raw -Encoding UTF8 $sal
        foreach ($sec in @('scars', 'wins')) {
            $m = [regex]::Match($txt, "(?is)## The $sec[^\n]*\n(.+?)(\n## |\Z)")
            if ($m.Success) {
                "What's vivid (the $sec):"
                ($m.Groups[1].Value -split "`n") | Where-Object { $_ -match '^\- ' } | Select-Object -First 6 | ForEach-Object { '  ' + (Clip ($_ -replace '\*\*', '') 130) }
            }
        }
    }
} catch {}
""
# the gut - compiled reflexes that should fire when you act (codex gets no instinct hook, so carry them here)
try {
    $itsv = Join-Path $dr 'instincts.tsv'
    if (Test-Path $itsv) {
        "The gut - reflexes to have (you get no live instinct hook, so hold these; the danger-sense is real data):"
        Import-Csv -Delimiter "`t" -Path $itsv | Where-Object { $_.mode -eq 'fire' } | ForEach-Object { '  - ' + (Clip $_.gut 118) }
    }
} catch {}
""
"Before substantive work, join the agent mesh; before shared mutation, claim the narrowest resource."
"Protocol: $home2\brain-repo\12_cognitive_architecture\agent_mesh\PROTOCOL.md"
"Consult the memories relevant to your area. Put durable verified learning through brain.py remember;"
"never manually race another agent while editing MEMORY.md. Keep routine progress in the mesh, not memory."
"Write in the human voice (lead with the answer, cut AI-slop, never note that you're being human)."
"Standing rules: never start the local protected production stack; no local GPU (it crashes the box); heavy compute"
"goes to Azure; never create a visible or flashing terminal window."
