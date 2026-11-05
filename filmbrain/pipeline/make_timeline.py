#!/usr/bin/env python
"""
Write an MLT timeline that Kdenlive and Shotcut can open.

Why this matters more than it looks: an ffmpeg concat produces a finished
object nobody can adjust. An MLT XML produces a TIMELINE - every clip on its
own track at its own in and out, with the narration underneath - which a human
opens and fixes. That is the difference between a generated film and a
generated first cut, and the first cut is the useful thing.

  make_timeline.py out.kdenlive --clips shots/*.mp4 --audio narration.wav
  make_timeline.py out.kdenlive --plan plan.json

plan.json is the richer form:
  {"fps": 25, "width": 1920, "height": 1080,
   "audio": "narration.wav",
   "clips": [{"file": "s01.mp4", "in": 0, "out": 4.2, "start": 0.0},
             {"file": "s02.mp4", "in": 1.0, "out": 6.5, "start": 4.2}]}
"""
import argparse, glob, json, os, subprocess, sys
import xml.etree.ElementTree as ET

CNW = (0x08000000 if os.name == "nt" else 0)


def probe(path):
    """Duration and size, from ffprobe - never guessed."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,r_frame_rate:format=duration",
             "-of", "json", path],
            capture_output=True, text=True, creationflags=CNW, timeout=60)
        d = json.loads(r.stdout or "{}")
        st = (d.get("streams") or [{}])[0]
        dur = float((d.get("format") or {}).get("duration") or 0)
        num, _, den = (st.get("r_frame_rate") or "25/1").partition("/")
        fps = float(num) / float(den or 1)
        return {"duration": dur, "width": st.get("width"), "height": st.get("height"), "fps": fps}
    except Exception:
        return {"duration": 0.0, "width": None, "height": None, "fps": 25.0}


def tc(seconds, fps):
    """FRAME NUMBERS, not timecodes.

    MLT accepts hh:mm:ss.mmm in some places and frame counts in others, and the
    timecode form silently produced a 0.128-second render from a 23-second
    timeline - melt read three frames and stopped, with no error. Frame numbers
    are unambiguous everywhere."""
    if seconds < 0:
        seconds = 0.0
    return str(int(round(seconds * fps)))


def build(plan, out_path):
    fps = plan.get("fps", 25)
    w = plan.get("width", 1920)
    h = plan.get("height", 1080)
    clips = plan["clips"]
    audio = plan.get("audio")

    # The mlt element's `producer` attribute names what melt renders. Declaring
    # main_bin without defining one made melt render 0.128 seconds of nothing
    # from a 23-second timeline - it found no such producer and fell through.
    # Point it at the tractor, which is the thing that actually holds the tracks.
    mlt = ET.Element("mlt", {"LC_NUMERIC": "C", "version": "7.0.0",
                             "producer": "tractor0",
                             "root": os.path.dirname(os.path.abspath(out_path))})
    ET.SubElement(mlt, "profile", {
        "description": "automatic", "width": str(w), "height": str(h),
        "progressive": "1", "sample_aspect_num": "1", "sample_aspect_den": "1",
        "display_aspect_num": str(w), "display_aspect_den": str(h),
        "frame_rate_num": str(int(round(fps))), "frame_rate_den": "1",
        "colorspace": "709"})

    total = 0.0
    prods = []
    for i, c in enumerate(clips):
        info = c.get("_info") or probe(c["file"])
        cin = float(c.get("in", 0.0))
        cout = float(c.get("out", info["duration"] or 0.0))
        if cout <= cin:
            cout = cin + max(info["duration"], 1.0)
        pid = "producer{}".format(i)
        p = ET.SubElement(mlt, "producer",
                          {"id": pid, "in": tc(cin, fps),
                           "out": str(max(int(round(cout * fps)) - 1, 0))})
        ET.SubElement(p, "property", {"name": "resource"}).text = os.path.abspath(c["file"])
        # 'avformat-novalidate' tells MLT NOT to probe the file, so it never
        # learns the clip's length and renders three frames of it without any
        # error. Either probe (plain 'avformat') or state the length. We state
        # it, because we already asked ffprobe and know the answer.
        ET.SubElement(p, "property", {"name": "mlt_service"}).text = "avformat"
        ET.SubElement(p, "property", {"name": "length"}).text = str(
            max(int(round((info["duration"] or cout) * fps)), 1))
        ET.SubElement(p, "property", {"name": "audio_index"}).text = "-1"
        prods.append((pid, cin, cout))
        total += (cout - cin)

    # video playlist
    v = ET.SubElement(mlt, "playlist", {"id": "playlist_video"})
    for pid, cin, cout in prods:
        ET.SubElement(v, "entry", {"producer": pid, "in": tc(cin, fps),
                                   "out": str(max(int(round(cout * fps)) - 1, 0))})

    # audio playlist
    a = ET.SubElement(mlt, "playlist", {"id": "playlist_audio"})
    if audio and os.path.exists(audio):
        ap = ET.SubElement(mlt, "producer", {"id": "producer_audio", "in": tc(0, fps),
                                             "out": tc(total, fps)})
        ET.SubElement(ap, "property", {"name": "resource"}).text = os.path.abspath(audio)
        ET.SubElement(ap, "property", {"name": "mlt_service"}).text = "avformat-novalidate"
        ET.SubElement(ap, "property", {"name": "video_index"}).text = "-1"
        ET.SubElement(a, "entry", {"producer": "producer_audio", "in": tc(0, fps),
                                   "out": tc(total, fps)})

    tractor = ET.SubElement(mlt, "tractor", {"id": "tractor0", "in": tc(0, fps),
                                             "out": tc(total, fps)})
    ET.SubElement(tractor, "track", {"producer": "playlist_video"})
    ET.SubElement(tractor, "track", {"producer": "playlist_audio", "hide": "video"})

    tree = ET.ElementTree(mlt)
    ET.indent(tree, space="  ")
    tree.write(out_path, encoding="utf-8", xml_declaration=True)
    return total, len(prods)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--clips", nargs="*", default=[])
    ap.add_argument("--audio")
    ap.add_argument("--plan")
    ap.add_argument("--fps", type=float, default=25.0)
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    a = ap.parse_args()

    if a.plan:
        plan = json.load(open(a.plan, encoding="utf-8"))
    else:
        files = []
        for g in a.clips:
            files.extend(sorted(glob.glob(g)) if any(c in g for c in "*?[") else [g])
        if not files:
            print("no clips")
            return 2
        plan = {"fps": a.fps, "width": a.width, "height": a.height,
                "audio": a.audio,
                "clips": [{"file": f} for f in files]}

    total, n = build(plan, a.out)
    print(json.dumps({"out": a.out, "clips": n, "duration_s": round(total, 2),
                      "audio": plan.get("audio"),
                      "open_with": "kdenlive or shotcut"}))


if __name__ == "__main__":
    sys.exit(main() or 0)
