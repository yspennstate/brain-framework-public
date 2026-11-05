# Round two: correct package ids, and user-scope where the machine-scope
# installer wanted an interactive UAC prompt that no agent can answer.
$ErrorActionPreference = 'Continue'
$log = "$env:USERPROFILE\filmbrain\logs\install_tools.log"
function L($m){ $t=(Get-Date).ToUniversalTime().ToString('HH:mm:ss'); Add-Content -Path $log -Value "$t $m" -Encoding utf8 }

$pkgs = @(
  @{id='Pencil2D.Pencil';        scope=''},
  @{id='DWANGO.OpenToonz';       scope=''},
  @{id='Synfig.SynfigStudio';    scope=''},
  @{id='ch.LosslessCut';         scope='user'},
  @{id='ImageMagick.Q16-HDRI';   scope=''},
  @{id='Audacity.Audacity';      scope=''},
  @{id='HandBrake.HandBrake';    scope=''},
  @{id='OBSProject.OBSStudio';   scope=''},
  @{id='BlackmagicDesign.DaVinciResolve'; scope=''}
)

foreach($p in $pkgs){
  L ("ROUND2 " + $p.id)
  $args = @('install','--id',$p.id,'-e','--silent','--accept-package-agreements','--accept-source-agreements','--disable-interactivity')
  if($p.scope -ne ''){ $args += @('--scope',$p.scope) }
  $out = & winget @args 2>&1 | Out-String
  $tail = ($out -split "`n" | Where-Object { $_.Trim() -ne '' } | Select-Object -Last 2) -join ' | '
  L ("  -> " + $tail)
}
L "DONE round2"
