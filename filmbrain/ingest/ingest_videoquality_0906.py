"""
Ingest the 6 September 2026 video-and-voice quality work into the film brain, through the store's own API,
under the conventions the ffmpeg and Blender harvests use (tool surface as `param` nodes hung off `tool.*`,
engines and voices as their own kinds, measurements as observations that point at the run that made them).

What goes in, and where it comes from (nothing here is invented; every node names its source):

  harvest:comfyui   cf.node.<Name>, cf.node.<Name>.in.<input>, enum choices     videoquality_20260906/metrics/comfy_nodes.json
  harvest:manim     mn.<Class>, mn.<Class>.p.<param>, mn.config.<key>            videoquality_20260906/metrics/manim_api.json
  harvest:edge_tts  voice.msft.<ShortName> (322 Microsoft neural voices)         videoquality_20260906/metrics/edge_voices.json
  measured          vq.clip.<name> / vq.take.<set>.<key> + obs                   frames/*_metrics.json, voice/scan_*.json, voice/metrics_*.json, voice/asr_results.json
  survey            tool.<slug> / engine.<slug> + ref.<slug>                     tools/videoquality/MANIFEST.json + vq_authored.SURVEY
  authored          metric.vq.*, principle.vq.*, failure.vq.*, craft.vq.*        vq_authored.py (rubric, techniques, findings, failure modes)

Idempotent: add_node updates in place; edges are INSERT OR REPLACE. Nothing is deleted.
    python ingest_videoquality_0906.py [--dry]
"""
import os, sys, json, glob, re, time, hashlib

sys.path.insert(0, os.environ.get("FILMBRAIN_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.expanduser(r"~/videoquality_20260906/tools"))
from fb_store import Store, slug

VQ = os.path.expanduser(r"~/videoquality_20260906")
SESSION = "claude-videoquality-0906"
DRY = "--dry" in sys.argv
COUNTS = {"nodes": 0, "edges": 0, "obs": 0, "lessons": 0}


class S:
    def __init__(self):
        self.s = None if DRY else Store()

    def node(self, nid, kind, name, title=None, body=None, source="authored", tags=None, meta=None):
        COUNTS["nodes"] += 1
        if self.s:
            m = dict(meta or {})
            m.setdefault("session", SESSION)
            m.setdefault("ingested", time.strftime("%Y-%m-%d"))
            self.s.add_node(nid, kind, name, title=title, body=body, source=source, tags=tags, meta=m, commit=False)
        return nid

    def edge(self, a, rel, b, note=None, source="authored", weight=1.0):
        COUNTS["edges"] += 1
        if self.s:
            self.s.add_edge(a, rel, b, weight=weight, note=note, source=source, commit=False)

    def obs(self, nid, metric, num=None, text=None, unit=None, run_id=None, source="measured"):
        COUNTS["obs"] += 1
        if self.s:
            self.s.add_obs(nid, metric, num=num, text=text, unit=unit, run_id=run_id, source=source, commit=False)

    def lesson(self, text, node_id=None, evidence=None, confidence="provisional"):
        COUNTS["lessons"] += 1
        if self.s:
            self.s.add_lesson(text, node_id=node_id, evidence=evidence, confidence=confidence, session=SESSION)

    def run(self, kind, tool, params=None, outcome=None, metrics=None, artifact=None, notes=None, seconds=None):
        if self.s:
            return self.s.add_run(kind, tool, params=params, outcome=outcome, metrics=metrics, artifact=artifact, notes=notes, seconds=seconds, session=SESSION)
        return "run_dry"

    def commit(self):
        if self.s:
            self.s.commit()


def jload(p):
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


def sid(s):
    return re.sub(r"[^A-Za-z0-9_.]+", "_", s).strip("_")[:100]


# ------------------------------------------------------------------ ComfyUI surface
def ingest_comfy(st):
    d = jload(os.path.join(VQ, "metrics", "comfy_nodes.json"))
    if not d:
        return
    src = "harvest:comfyui"
    st.node("tool.comfyui", "tool", "ComfyUI", body="Node-graph front end for diffusion models on this machine (ComfyUI_windows_portable, python 3.13, torch 2.13 cu130). "
            "Runs LTX-2.5 22B GGUF and Wan 2.1/2.2; custom node packs: ComfyUI-GGUF, KJNodes, ComfyUI-LTXVideo, LTX-2.5-Director, SeedVR2, Frame-Interpolation, FramePackWrapper.",
            source=src, tags=["tool", "ai", "video", "comfyui"], meta={"harvested": d["harvested"], "comfy_dir": d["comfy_dir"], "n_nodes": d["n_nodes"]})
    cats = {}
    for name, n in d["nodes"].items():
        if "error" in n:
            continue
        cat = (n.get("category") or "uncategorised")
        top = cat.split("/")[0]
        cid = "craft.comfy_cat_" + slug(top)
        if cid not in cats:
            cats[cid] = st.node(cid, "craft", "ComfyUI nodes: " + top, body="The ComfyUI node category '%s' as installed here." % top, source=src, tags=["comfyui", "category"])
            st.edge(cid, "part_of", "tool.comfyui", source=src)
        nid = "cf.node." + sid(name)
        body = "%s (%s). Category %s. Outputs: %s.%s" % (n.get("display", name), n.get("class"), cat, ", ".join(n.get("outputs") or []) or "none",
                                                        (" " + n["description"]) if n.get("description") else "")
        st.node(nid, "param", name, title=n.get("display", name), body=body, source=src, tags=["comfyui", "node", slug(top)],
                meta={"module": n.get("module"), "category": cat, "outputs": n.get("outputs"), "output_names": n.get("output_names")})
        st.edge(nid, "part_of", "tool.comfyui", source=src)
        st.edge(nid, "is_a", cid, source=src)
        for iname, inp in (n.get("inputs") or {}).items():
            pid = nid + ".in." + sid(iname)
            if inp.get("type") == "enum":
                body = "Input '%s' of %s: one of %d choices%s." % (iname, name, inp.get("n_choices", 0), (" - " + ", ".join(inp["choices"][:12]) + ("..." if inp.get("n_choices", 0) > 12 else "")) if inp.get("choices") else "")
            else:
                o = inp.get("opts") or {}
                body = "Input '%s' of %s: type %s%s%s." % (iname, name, inp.get("type"),
                                                          (", default %s" % o["default"]) if "default" in o else "",
                                                          (", range %s..%s" % (o.get("min"), o.get("max"))) if "min" in o or "max" in o else "")
                if o.get("tooltip"):
                    body += " " + str(o["tooltip"])[:300]
            st.node(pid, "param", iname, title="%s.%s" % (name, iname), body=body, source=src, tags=["comfyui", "input", inp.get("group", "required")],
                    meta={"type": inp.get("type"), "opts": inp.get("opts"), "n_choices": inp.get("n_choices")})
            st.edge(pid, "option_of", nid, source=src)
            if inp.get("type") == "enum" and inp.get("n_choices", 0) <= 24:
                for c in inp.get("choices") or []:
                    cid2 = pid + "." + sid(str(c))[:60]
                    st.node(cid2, "format", str(c), title="%s.%s = %s" % (name, iname, c), body="Choice '%s' for input %s of %s." % (c, iname, name), source=src, tags=["comfyui", "choice"])
                    st.edge(cid2, "option_of", pid, source=src)
    st.commit()


# ------------------------------------------------------------------ Manim surface
def ingest_manim(st):
    d = jload(os.path.join(VQ, "metrics", "manim_api.json"))
    if not d:
        return
    src = "harvest:manim"
    st.node("tool.manim", "tool", "Manim Community", body="Manim Community %s in ~\\lecture\\venv - the 2D mathematics animation engine of the lecture films (LaTeX-native labels, Cairo renderer; OpenGL lands on the Intel GPU here)." % d["version"],
            source=src, tags=["tool", "animation", "manim", "lecture"], meta={"version": d["version"], "harvested": d["harvested"]})
    kinds = {}
    for cname, c in d["classes"].items():
        kid = "craft.manim_" + c["kind"]
        if kid not in kinds:
            kinds[kid] = st.node(kid, "craft", "Manim %ss" % c["kind"], body="Manim %s classes as installed here." % c["kind"], source=src, tags=["manim", c["kind"]])
            st.edge(kid, "part_of", "tool.manim", source=src)
        nid = "mn." + sid(c["name"])
        st.node(nid, "param", c["name"], title="manim." + c["name"], body="%s. %s Bases: %s. Methods: %s." % (c["kind"], c.get("doc") or "", ", ".join(c.get("bases") or []), ", ".join((c.get("methods") or [])[:20])),
                source=src, tags=["manim", c["kind"]], meta={"module": c["module"], "bases": c.get("bases")})
        st.edge(nid, "part_of", "tool.manim", source=src)
        st.edge(nid, "is_a", kid, source=src)
        for b in c.get("bases") or []:
            bid = "mn." + sid(b)
            if bid in [("mn." + sid(x["name"])) for x in d["classes"].values()]:
                st.edge(nid, "derived_from", bid, source=src)
        for p in c.get("params") or []:
            pid = nid + ".p." + sid(p["name"])
            st.node(pid, "param", p["name"], title="%s(%s)" % (c["name"], p["name"]), body="Constructor parameter %s of manim.%s%s." % (p["name"], c["name"], (", default %s" % p["default"]) if p.get("default") is not None else ""),
                    source=src, tags=["manim", "param"], meta={"default": p.get("default"), "kind": p.get("kind")})
            st.edge(pid, "option_of", nid, source=src)
    for k, v in (d.get("config") or {}).items():
        cid = "mn.config." + sid(k)
        st.node(cid, "param", k, title="manim config." + k, body="Manim config key %s, value here: %s." % (k, v), source=src, tags=["manim", "config"], meta={"value": v})
        st.edge(cid, "option_of", "tool.manim", source=src)
    st.commit()


# ------------------------------------------------------------------ Microsoft neural voices
def ingest_voices(st):
    d = jload(os.path.join(VQ, "metrics", "edge_voices.json"))
    if not d:
        return
    src = "harvest:edge_tts"
    st.node("engine.edge_tts", "engine", "Microsoft neural voices via edge-tts", body="The Microsoft neural TTS service reached through the edge-tts client; the video narration rule since 2026-09-05 (owner: Microsoft natural voices only for videos; baseline en-US-AndrewMultilingualNeural at +10%). 322 voices listed 2026-09-06.",
            source=src, tags=["engine", "tts", "microsoft"], meta={"n_voices": len(d), "listed": "2026-09-06"})
    for v in d:
        sn = v.get("ShortName")
        if not sn:
            continue
        vid = "voice.msft." + sid(sn)
        tag = v.get("VoiceTag") or {}
        pers = tag.get("VoicePersonalities") or []
        scen = tag.get("ContentCategories") or []
        st.node(vid, "voice", sn, title="%s (%s, %s)" % (sn, v.get("Locale"), v.get("Gender")),
                body="Microsoft neural voice %s, locale %s, %s.%s%s" % (sn, v.get("Locale"), v.get("Gender"),
                                                                     (" Personality: " + ", ".join(pers) + ".") if pers else "",
                                                                     (" Suggested content: " + ", ".join(scen) + ".") if scen else ""),
                source=src, tags=["voice", "microsoft", (v.get("Locale") or "").lower(), (v.get("Gender") or "").lower()] + (["multilingual"] if "Multilingual" in sn else []),
                meta={"locale": v.get("Locale"), "gender": v.get("Gender"), "personalities": pers, "content": scen, "status": v.get("Status")})
        st.edge(vid, "implemented_by", "engine.edge_tts", source=src)
        st.edge(vid, "is_a", "craft.narration", source=src)
    st.commit()


# ------------------------------------------------------------------ measured clips and takes
def ingest_measured(st):
    run = st.run("audit", "vq_frames+vq_voice_scan+narration_lab", params={"rig": "videoquality_20260906/tools"}, outcome="measured",
                 notes="video and voice quality audit 2026-09-06", artifact=VQ)
    engine_for = {"ltx": "engine.ltx25", "iv": "engine.ltx25", "f3": "tool.blender", "film": None}
    for mf in glob.glob(os.path.join(VQ, "frames", "*_metrics.json")):
        setname = os.path.basename(mf).split("_")[0]
        for r in jload(mf) or []:
            if "error" in r and not r.get("frames"):
                continue
            base = os.path.basename(r["path"].rstrip("\\/"))
            nid = "vq.clip." + sid(setname + "_" + base)
            s = r.get("summary") or {}
            t = r.get("temporal") or {}
            body = "%s: %sx%s%s, %s. Sharpness (Laplacian var) median %s, min %s; temporal mean abs diff %s, frozen frames %s/%s, flicker %s." % (
                base, r.get("w"), r.get("h"), (" %.3g fps %.1f s" % (r.get("fps", 0), r.get("duration", 0))) if r.get("fps") else "", r.get("codec") or "frames",
                s.get("sharpness_med"), s.get("sharpness_min"), t.get("mean_abs_diff"), t.get("frozen_frames"), t.get("frames"), t.get("flicker_index"))
            st.node(nid, "asset", base, title="measured clip " + base, body=body, source="measured", tags=["measured", "clip", setname],
                    meta={"path": r["path"], "sha16": r.get("sha16"), "w": r.get("w"), "h": r.get("h"), "fps": r.get("fps"), "duration": r.get("duration"), "bit_rate": r.get("bit_rate")})
            for k, v in s.items():
                if v is not None:
                    st.obs(nid, "vq." + k, num=v, run_id=run)
            for k, v in t.items():
                if v is not None:
                    st.obs(nid, "vq.temporal." + k, num=v, run_id=run)
            for f in r.get("frames") or []:
                st.obs(nid, "vq.frame.sharpness", num=f.get("sharpness"), text=str(f.get("t", f.get("file"))), run_id=run)
            eng = engine_for.get(setname)
            if eng:
                st.edge(nid, "output_of", eng, source="measured")
            st.edge(nid, "measured_by", "metric.vq.sharpness", source="measured")
            st.edge(nid, "measured_by", "metric.vq.motion_coherence", source="measured")
    sets = {"bastogne_edge": ("voice.msft.en_US_AndrewMultilingualNeural", "work.bastogne_film"), "kokoro": ("voice.bm_lewis", "work.bastogne_film"),
            "relu": ("voice.msft.en_US_AndrewMultilingualNeural", "work.relu_lecture"), "nmkc": ("voice.msft.en_US_AndrewMultilingualNeural", "work.nmkc_lecture")}
    for sc in glob.glob(os.path.join(VQ, "voice", "scan_*.json")):
        setname = os.path.basename(sc)[5:-5]
        voice, work = sets.get(setname, (None, None))
        for r in jload(sc) or []:
            if "error" in r:
                continue
            key = os.path.splitext(os.path.basename(r["file"]))[0]
            nid = "vq.take." + sid(setname + "." + key)
            body = "Narration take %s (%s): %.1f s, %s words, %s words/s over the take, tail silence %.2f s, %d pauses (max %.2f s), gap floor %s dB, peak %.3f%s." % (
                key, setname, r["dur"], r.get("words"), r.get("wps_total"), r["tail_silence"], r["pauses"], r["pause_max"], r.get("gap_floor_db"), r["peak"],
                " - TRUNCATED SUSPECT (stream broke off mid-sentence)" if r.get("truncated_suspect") else "")
            st.node(nid, "asset", key, title="take %s/%s" % (setname, key), body=body, source="measured", tags=["measured", "take", setname] + (["truncated"] if r.get("truncated_suspect") else []),
                    meta={"path": os.path.join(r.get("dir", ""), r["file"]), "sr": r.get("sr")})
            for k in ("dur", "words", "wps_total", "wps_speech", "tail_silence", "lead_silence", "pauses", "pause_max", "pause_cv", "gap_floor_db", "peak", "rms_db", "clipped_samples"):
                if r.get(k) is not None:
                    st.obs(nid, "voice." + k, num=r[k], run_id=run)
            st.obs(nid, "voice.truncated_suspect", num=1 if r.get("truncated_suspect") else 0, run_id=run)
            if voice:
                st.edge(nid, "output_of", voice, source="measured")
            if work:
                st.edge(nid, "used_in", work, source="measured")
            if r.get("truncated_suspect"):
                st.edge(nid, "example_of", "failure.vq.truncated_take", source="measured")
    for mf in glob.glob(os.path.join(VQ, "voice", "metrics_*.json")):
        d = jload(mf)
        if not isinstance(d, dict):
            continue
        key = os.path.basename(mf)[8:-5]
        nid = "vq.voicesample." + sid(key)
        prof = d.get("profile") or d.get("metrics") or {k: v for k, v in d.items() if isinstance(v, (int, float))}
        tells = d.get("tells") or d.get("robotic_tells") or []
        st.node(nid, "asset", key, title="voice sample " + key, body="Voice sample %s measured with narration_lab/measure/voice_metrics.py against the 79-reader LibriVox ranges. Tells: %s" % (key, "; ".join(map(str, tells)) if tells else "none flagged"),
                source="measured", tags=["measured", "voice", "sample"], meta={"file": mf})
        for k, v in (prof or {}).items():
            if isinstance(v, (int, float)):
                st.obs(nid, "voice." + k, num=v, run_id=run)
            elif isinstance(v, dict) and isinstance(v.get("value"), (int, float)):
                st.obs(nid, "voice." + k, num=v["value"], text=str(v.get("verdict") or v.get("status") or ""), run_id=run)
    asr = jload(os.path.join(VQ, "voice", "asr_results.json"))
    for r in asr or []:
        key = os.path.splitext(os.path.basename(r["file"]))[0]
        nid = "vq.asr." + sid(r["group"] + "." + key)
        diffs = r.get("diffs") or []
        st.node(nid, "asset", key, title="ASR check %s/%s" % (r["group"], key),
                body="faster-whisper small (CPU) heard the take and it was diffed against the script: %s script words, %s heard, mismatch proxy %s. Names in script %s, heard %s. Diffs: %s" % (
                    r.get("script_words"), r.get("heard_words"), r.get("wer_proxy"), r.get("names_in_script"), r.get("names_heard"),
                    "; ".join("%s -> %s" % (d["script"], d["heard"]) for d in diffs[:12])),
                source="measured", tags=["measured", "asr", r["group"]], meta={"file": r["file"]})
        if r.get("wer_proxy") is not None:
            st.obs(nid, "voice.asr_mismatch_proxy", num=r["wer_proxy"], run_id=run)
        st.edge(nid, "measured_by", "metric.vq.mispronunciation", source="measured")
    st.commit()


# ------------------------------------------------------------------ survey + downloads
def ingest_survey(st):
    import vq_authored as A
    man = jload(os.path.expanduser(r"~/tools/videoquality/MANIFEST.json")) or {"files": {}, "repos": {}, "snapshots": {}}
    for t in A.SURVEY:
        nid = t["id"]
        st.node(nid, t.get("kind", "tool"), t["name"], title=t["name"], body=t["body"], source="survey:2026-09-06", tags=t.get("tags", []) + ["survey"],
                meta={"url": t.get("url"), "licence": t.get("licence"), "vram": t.get("vram"), "released": t.get("released"), "status": t.get("status"), "chosen": t.get("chosen", False)})
        rid = "ref." + slug(t["name"])
        st.node(rid, "reference", t["name"] + " (source)", title=t.get("url"), body="Primary source consulted 2026-09-06: %s" % t.get("url"), source="survey:2026-09-06", tags=["reference", "url"], meta={"url": t.get("url")})
        st.edge(nid, "evidence_for", rid, source="survey:2026-09-06")
        for rel, dst in t.get("rels", []):
            st.edge(nid, rel, dst, source="survey:2026-09-06")
    for key, f in man.get("files", {}).items():
        if not f.get("sha256"):
            continue
        nid = "asset.model." + sid(os.path.basename(f["path"]))
        st.node(nid, "asset", os.path.basename(f["path"]), title=f["path"], body="%s. %.2f GB, sha256 %s, licence %s. Source %s. Landed %s in %s s." % (
            f.get("note"), f["bytes"] / 1e9, f["sha256"], f.get("licence"), f.get("source"), f.get("when"), f.get("seconds")),
            source="download:2026-09-06", tags=["asset", "model", f.get("tool", "")], meta={"sha256": f["sha256"], "bytes": f["bytes"], "licence": f.get("licence"), "path": f["path"], "source": f.get("source")})
        st.edge(nid, "part_of", A.TOOL_FOR_DOWNLOAD.get(f.get("tool"), "tool.comfyui"), source="download:2026-09-06")
    for url, r in man.get("repos", {}).items():
        nid = "asset.repo." + sid(url.rstrip("/").split("/")[-1].replace(".git", ""))
        st.node(nid, "asset", url.split("/")[-1], title=url, body="Cloned %s at commit %s to %s (licence %s)." % (url, r.get("commit"), r.get("path"), r.get("licence")),
                source="download:2026-09-06", tags=["asset", "repo", r.get("tool", "")], meta=r)
        st.edge(nid, "part_of", A.TOOL_FOR_DOWNLOAD.get(r.get("tool"), "tool.comfyui"), source="download:2026-09-06")
    for repo, r in man.get("snapshots", {}).items():
        if not r.get("ok"):
            continue
        nid = "asset.model." + sid(repo.split("/")[-1])
        st.node(nid, "asset", repo, title=r.get("path"), body="%s. %.2f GB snapshot, licence %s, weights sha256 %s." % (r.get("note"), r.get("bytes", 0) / 1e9, r.get("licence"), json.dumps(r.get("sha256"))[:300]),
                source="download:2026-09-06", tags=["asset", "model", r.get("tool", "")], meta=r)
        st.edge(nid, "part_of", A.TOOL_FOR_DOWNLOAD.get(r.get("tool"), "tool.comfyui"), source="download:2026-09-06")
    st.commit()


# ------------------------------------------------------------------ authored: rubric, principles, failures, techniques, findings
def ingest_authored(st):
    import vq_authored as A
    for n in A.NODES:
        st.node(n["id"], n["kind"], n["name"], title=n.get("title"), body=n["body"], source=n.get("source", "authored"), tags=n.get("tags"), meta=n.get("meta"))
        for rel, dst in n.get("rels", []):
            st.edge(n["id"], rel, dst, source=n.get("source", "authored"))
    for l in A.LESSONS:
        st.lesson(l["text"], node_id=l.get("node"), evidence=l.get("evidence"), confidence=l.get("confidence", "provisional"))
    st.commit()


if __name__ == "__main__":
    st = S()
    which = [a for a in sys.argv[1:] if not a.startswith("--")] or ["comfy", "manim", "voices", "authored", "survey", "measured"]
    for w in which:
        t0 = time.time()
        {"comfy": ingest_comfy, "manim": ingest_manim, "voices": ingest_voices, "measured": ingest_measured, "survey": ingest_survey, "authored": ingest_authored}[w](st)
        print(w, "done in %.0fs" % (time.time() - t0), COUNTS, flush=True)
    print("TOTAL", COUNTS)
