#!/usr/bin/env python
"""
A small film engine built on the one rule that has held: record first, cut the
picture to the words.

A film is a list of BEATS. Each beat carries its narration text, a pause after
it, and a visual - a still, a chart, a card, or a clip. The engine:

  1. synthesises every beat and MEASURES what actually came out,
  2. gives each visual exactly that measured duration plus the pause,
  3. renders the visuals to those durations,
  4. assembles picture and sound.

Words and picture cannot drift, because nothing is ever told how long anything
takes - it is measured. A rewritten sentence moves its own visuals with it.

Everything is cached by a stamp over the text and the visual spec, so an edit
to one beat rebuilds one beat.
"""
import hashlib, json, os, shutil, subprocess, sys, tempfile
import numpy as np
import soundfile as sf

CNW = (0x08000000 if os.name == "nt" else 0)
HERE = os.path.dirname(os.path.abspath(__file__))


def stamp(*parts):
    h = hashlib.sha1()
    for p in parts:
        h.update(json.dumps(p, sort_keys=True, ensure_ascii=False).encode())
    return h.hexdigest()[:16]


def run(cmd, **kw):
    return subprocess.run(cmd, creationflags=CNW, capture_output=True, text=True,
                          errors="replace", **kw)


class Film:
    def __init__(self, out_dir, width=1920, height=1080, fps=30,
                 narrator=None, cache=None):
        self.out = out_dir
        self.w, self.h, self.fps = width, height, fps
        self.narrator = narrator or (lambda text, path: None)
        self.cache = cache or os.path.join(out_dir, "_cache")
        os.makedirs(self.out, exist_ok=True)
        os.makedirs(self.cache, exist_ok=True)
        self.beats = []

    def beat(self, text, visual, pause=0.55, note=None):
        """visual is a callable(path, seconds, film) that writes a video file."""
        self.beats.append({"text": text.strip(), "visual": visual,
                           "pause": pause, "note": note})
        return self

    # ---- step 1 and 2: narrate, then MEASURE ----
    def narrate(self):
        for i, b in enumerate(self.beats):
            key = stamp(b["text"], "voice")
            wav = os.path.join(self.cache, "beat_%03d_%s.wav" % (i, key))
            if not os.path.exists(wav) or os.path.getsize(wav) < 8000:
                self.narrator(b["text"], wav)
            if not os.path.exists(wav):
                raise RuntimeError("narration failed for beat %d" % i)
            x, sr = sf.read(wav, always_2d=False)
            if x.ndim > 1:
                x = x.mean(axis=1)
            b["wav"] = wav
            b["sr"] = sr
            b["speech_s"] = len(x) / sr          # MEASURED, never assumed
            b["total_s"] = b["speech_s"] + b["pause"]
            print("  beat {:>2}  {:>6.2f}s speech + {:.2f} pause   {}".format(
                i, b["speech_s"], b["pause"], b["text"][:56]), flush=True)
        return self

    # ---- step 3: visuals cut to the measured seconds ----
    def render_visuals(self):
        for i, b in enumerate(self.beats):
            key = stamp(b["text"], b["pause"], getattr(b["visual"], "spec", str(b["visual"])),
                        round(b["total_s"], 3), self.w, self.h, self.fps)
            mp4 = os.path.join(self.cache, "vis_%03d_%s.mp4" % (i, key))
            if not os.path.exists(mp4) or os.path.getsize(mp4) < 5000:
                b["visual"](mp4, b["total_s"], self)
            if not os.path.exists(mp4):
                raise RuntimeError("visual failed for beat %d" % i)
            b["mp4"] = mp4
            print("  beat {:>2}  visual ok  {:.2f}s".format(i, b["total_s"]), flush=True)
        return self

    # ---- step 4: assemble ----
    def assemble(self, name="film.mp4", music=None, floor_db=-62.0):
        # audio: each beat's speech followed by its pause, with a room floor so
        # the gaps are never digitally silent
        sr = self.beats[0]["sr"]
        rng = np.random.default_rng(3)
        parts = []
        for b in self.beats:
            x, s = sf.read(b["wav"], always_2d=False)
            if x.ndim > 1:
                x = x.mean(axis=1)
            parts.append(x.astype(np.float32))
            parts.append(np.zeros(int(b["pause"] * sr), dtype=np.float32))
        audio = np.concatenate(parts)
        if floor_db is not None:
            w = rng.normal(0, 1, len(audio))
            bn = np.cumsum(w)
            bn -= np.convolve(bn, np.ones(2048) / 2048, mode="same")
            bn /= (np.max(np.abs(bn)) or 1.0)
            audio = audio + (bn * (10 ** (floor_db / 20.0))).astype(np.float32)
        atrack = os.path.join(self.cache, "_voice.wav")
        sf.write(atrack, audio, sr)

        lst = os.path.join(self.cache, "_concat.txt")
        with open(lst, "w", encoding="utf-8") as f:
            for b in self.beats:
                f.write("file '" + b["mp4"].replace("\\", "/") + "'\n")
        silent = os.path.join(self.cache, "_picture.mp4")
        r = run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat",
                 "-safe", "0", "-i", lst, "-c", "copy", silent])
        if r.returncode != 0:
            run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat",
                 "-safe", "0", "-i", lst, "-c:v", "libx264", "-crf", "18",
                 "-pix_fmt", "yuv420p", silent])

        final = os.path.join(self.out, name)
        af = "loudnorm=I=-16:TP=-1.5:LRA=11"
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
               "-i", silent, "-i", atrack]
        if music and os.path.exists(music):
            cmd += ["-stream_loop", "-1", "-i", music,
                    "-filter_complex",
                    "[1:a]" + af + "[v];[2:a]volume=0.06,afade=t=in:d=2[m];"
                    "[v][m]amix=inputs=2:duration=first[a]",
                    "-map", "0:v", "-map", "[a]"]
        else:
            cmd += ["-filter_complex", "[1:a]" + af + "[a]", "-map", "0:v", "-map", "[a]"]
        cmd += ["-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", "-shortest", final]
        r = run(cmd)
        if r.returncode != 0:
            print(r.stderr[-800:])
            raise RuntimeError("assemble failed")
        total = sum(b["total_s"] for b in self.beats)
        print("FBMETRIC film_seconds={:.2f}".format(total))
        print("FBMETRIC film_beats={}".format(len(self.beats)))
        print("wrote", final, "{:.1f}s".format(total))
        return final
