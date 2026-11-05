# Free movie-making toolchain. Silent installs only - no visible windows (owner standing rule).
$ErrorActionPreference = 'Continue'
$log = "$env:USERPROFILE\filmbrain\logs\install_tools.log"
function L($m){ $t=(Get-Date).ToUniversalTime().ToString('HH:mm:ss'); Add-Content -Path $log -Value "$t $m" -Encoding utf8 }

$pkgs = @(
  @{id='KDE.Kdenlive';                     why='NLE, free, scriptable via MLT'},
  @{id='Meltytech.Shotcut';                why='NLE, simple, MLT XML'},
  @{id='KDE.Krita';                        why='2D paint plus frame animation'},
  @{id='Inkscape.Inkscape';                why='vector assets, CLI export'},
  @{id='GIMP.GIMP.3';                      why='raster, script-fu batch'},
  @{id='Audacity.Audacity';                why='audio edit, spectral repair'},
  @{id='OBSProject.OBSStudio';             why='capture, virtual cam, scene compositing'},
  @{id='HandBrake.HandBrake';              why='encode presets'},
  @{id='ImageMagick.ImageMagick';          why='batch image ops'},
  @{id='Pencil2D.Pencil2D';                why='hand-drawn 2D animation'},
  @{id='OpenToonz.OpenToonz';              why='pro 2D animation, Ghibli lineage'},
  @{id='SynfigStudio.SynfigStudio';        why='vector tween animation'},
  @{id='mifi.LosslessCut';                 why='lossless trim and concat'},
  @{id='VideoLAN.VLC';                     why='review player'},
  @{id='7zip.7zip';                        why='archives'},
  @{id='BlackmagicDesign.DaVinciResolve';  why='grading plus Fusion, free tier'},
  @{id='NatronGitHub.Natron';              why='node compositor'},
  @{id='LMMS.LMMS';                        why='music bed'}
)

foreach($p in $pkgs){
  L ("INSTALL " + $p.id + "  # " + $p.why)
  $out = winget install --id $p.id -e --silent --accept-package-agreements --accept-source-agreements --disable-interactivity 2>&1 | Out-String
  $tail = ($out -split "`n" | Where-Object { $_.Trim() -ne '' } | Select-Object -Last 3) -join ' | '
  L ("  -> " + $tail)
}
L "DONE tool sweep"
