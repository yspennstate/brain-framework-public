"""
Wire the authored craft graph to the harvested tool surface.

Without this the brain is two disconnected things: a book about filmmaking and
a list of ffmpeg options. With it, "how do I stop this looking computer
generated" can answer with motion blur, grain and a defocus AND name the exact
filter that does each one on this machine.

The mapping is CURATED, not fuzzy-matched. A keyword match between "grain" and
"grain extract" would fill the graph with plausible-looking rubbish, and a
wrong edge in a reference is worse than a missing one.
"""
import os
import sys

sys.path.insert(0, os.environ.get("FILMBRAIN_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fb_store import Store

# craft concept -> the things on this machine that actually do it.
# recipe is the one-liner worth remembering; it goes on the edge as a note.
LINKS = [
    ("craft.grain", "ff.filter.noise",
     "noise=alls=4:allf=t+u  - temporal grain, strength 4; this is what stopped the "
     "Bastogne upscales reading as plastic"),
    ("craft.grain", "ff.filter.gradfun", "gradfun - debands a gradient before grain goes on"),
    ("craft.banding", "ff.filter.gradfun", "gradfun=strength=1.2:radius=16"),
    ("craft.upscaling", "ff.filter.scale",
     "scale=1920:1080:flags=lanczos then unsharp=5:5:0.6 then a little grain"),
    ("craft.upscaling", "ff.filter.unsharp", "unsharp=5:5:0.6:5:5:0.0 after a lanczos upscale"),
    ("craft.ken_burns", "ff.filter.zoompan",
     "zoompan=z='min(zoom+0.0006,1.10)':d=250:s=1920x1080 - keep it under 3 per cent of "
     "frame width a second or it reads as a slideshow effect"),
    ("craft.easing", "ff.filter.zoompan", "drive z with a smoothstep on 'on/d', never a linear ramp"),
    ("craft.dissolve", "ff.filter.xfade", "xfade=transition=fade:duration=0.6:offset=N"),
    ("craft.transition", "ff.filter.xfade", "xfade has 50-odd named transitions; almost all of them are wrong"),
    ("craft.color_management", "ff.filter.colorspace",
     "colorspace=all=bt709:iall=bt601-6-625:fast=0 - and TAG the output, do not just convert"),
    ("craft.color_management", "ff.filter.zscale", "zscale=t=linear for a linear-light intermediate"),
    ("craft.lut", "ff.filter.lut3d", "lut3d=file=look.cube"),
    ("craft.contrast_curve", "ff.filter.curves", "curves=preset=medium_contrast, or all='0/0.03 0.5/0.5 1/0.97'"),
    ("craft.lift_gamma_gain", "ff.filter.colorbalance", "colorbalance=rs=-0.05:bs=0.08 for cool shadows"),
    ("craft.lift_gamma_gain", "ff.filter.eq", "eq=contrast=1.05:saturation=0.95:gamma=1.02"),
    ("craft.desaturation", "ff.filter.hue", "hue=s=0.6"),
    ("craft.film_emulation", "ff.filter.curves", "an S-curve plus a lifted black point is most of it"),
    ("craft.shallow_focus", "ff.filter.boxblur", "a crude stand-in; prefer a real defocus in the render"),
    ("craft.bokeh", "ff.filter.gblur", "gaussian is NOT bokeh - no aperture shape, no highlight bloom"),
    ("craft.keying", "ff.filter.chromakey", "chromakey=0x00FF00:0.10:0.05 then despill"),
    ("craft.keying", "ff.filter.despill", "despill=type=green:mix=0.5 - the step people forget"),
    ("craft.compositing", "ff.filter.overlay", "overlay=x=:y=:format=auto - watch premultiplication"),
    ("craft.subtitles", "ff.filter.subtitles", "subtitles=f.srt:force_style='Fontsize=22,Outline=0'"),
    ("craft.title_design", "ff.filter.drawtext", "drawtext with a box at low alpha beats a drop shadow"),
    ("craft.safe_area", "ff.filter.drawbox", "drawbox to check the 90 per cent action-safe box in a proof"),
    ("craft.contact_sheet", "ff.filter.tile", "tile=6x5 after a thumbnail select - one image, every shot"),
    ("craft.frame_interpolation", "ff.filter.minterpolate",
     "minterpolate=fps=60:mi_mode=mci - warps on occlusion, never use it on text"),
    ("craft.motion_blur", "ff.filter.tmix", "tmix=frames=3 fakes it after the fact; do it in the render instead"),
    ("craft.proxy_workflow", "ff.filter.scale", "scale=-2:540 with a fast preset for edit proxies"),
    ("craft.loudness_standard", "ff.filter.loudnorm",
     "loudnorm=I=-16:TP=-1.5:LRA=11, two-pass for accuracy; -14 for YouTube, -19 for our audiobooks"),
    ("craft.eq_for_voice", "ff.filter.highpass", "highpass=f=80:poles=2 under any voice"),
    ("craft.eq_for_voice", "ff.filter.equalizer", "equalizer=f=300:w=1:g=-2 if the voice is boxy"),
    ("craft.eq_for_voice", "ff.filter.deesser", "deesser=i=0.4 only if the esses actually hurt"),
    ("craft.compression_audio", "ff.filter.acompressor",
     "acompressor=threshold=-20dB:ratio=2.5:attack=12:release=180 for narration"),
    ("craft.reverb", "ff.filter.aecho",
     "aecho=0.9:0.85:130|220:0.06|0.035 - too small to hear as reverb, big enough to stop it "
     "sounding synthetic"),
    ("craft.reverb", "ff.filter.afir", "afir with a real impulse response if a specific room is wanted"),
    ("craft.pitch_shifting", "ff.filter.rubberband",
     "rubberband=pitch=0.891:formant=preserved  - 2 semitones down keeping the speaker's size"),
    ("craft.pitch_shifting", "ff.filter.asetrate",
     "asetrate then atempo - moves formants WITH the pitch, so the speaker grows"),
    ("craft.formant", "ff.filter.rubberband", "formant=preserved vs formant=shifted is the whole choice"),
    ("craft.sync", "ff.filter.adelay", "adelay=45|45 - late is forgiven, early is caught at 15 ms"),
    ("craft.ambience", "ff.filter.amix", "amix=inputs=2:weights=1 0.08 for a bed under narration"),
    ("craft.tts_too_clean", "ff.filter.anoisesrc", "a brown noise floor at -60 dBFS under the whole take"),
    ("craft.codec_choice", "ff.encoder.libx264", "-c:v libx264 -preset slow -crf 18 -pix_fmt yuv420p"),
    ("craft.codec_choice", "ff.encoder.libx265", "same quality at a lower bitrate where the player allows"),
    ("craft.bitrate", "ff.encoder.libx264", "CRF for a fixed quality, two-pass only for a size target"),
    ("craft.generation_loss", "ff.encoder.prores_ks", "an intermediate that survives being re-encoded"),

    # blender
    ("craft.motion_blur", "bl.settings.motion_blur", "EEVEE and Cycles both have it and both default it OFF"),
    ("craft.depth_of_field", "bl.settings.depth_of_field", "a real defocus with a bladed aperture, not a blur"),
    ("craft.shutter_angle", "bl.settings.motion_blur", "shutter 0.5 is the 180-degree convention"),
    ("craft.color_management", "bl.settings.color_management", "the view transform decides everything downstream"),
    ("craft.rendering", "bl.settings.cycles", "Cycles for light that behaves; EEVEE when time matters more"),
    ("craft.rendering", "bl.settings.eevee", "real-time raster; fake the bounce with light probes"),
    ("craft.camera", "bl.settings.camera", "focal length and sensor size together decide the field of view"),
    ("craft.lighting", "bl.settings.light", "area lights, made large, are the soft key"),
    ("craft.compositing", "bl.type.compositornodeglare", "bloom and streaks belong in the comp, not the render"),
    ("craft.tracking", "bl.settings.render", "the solver wants parallax and sharp high-contrast features"),
    ("craft.simulation", "bl.settings.render", "cache at low resolution until the motion is right"),
]


def main():
    s = Store()
    added = missing = 0
    for craft, tool, note in LINKS:
        c, t = s.get(craft), s.get(tool)
        if not c:
            print("no craft node:", craft)
            missing += 1
            continue
        if not t:
            # the harvest may name it slightly differently; try a search
            cand = s.search(tool.rsplit(".", 1)[-1], limit=3, kind="param")
            if cand:
                t = cand[0]
                tool = t["id"]
            else:
                print("no tool node:", tool)
                missing += 1
                continue
        s.add_edge(craft, "implemented_by", tool, note=note, source="curated", commit=False)
        s.add_edge(tool, "applies_to", craft, note=note, source="curated", commit=False)
        added += 2
    s.commit()
    print("linked", added, "edges,", missing, "unresolved")
    print(s.stats()["edges"], "edges total")


if __name__ == "__main__":
    main()
