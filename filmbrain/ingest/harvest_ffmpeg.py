"""
Harvest ffmpeg's own capability tables into the film brain.

Everything here is read out of the binary on this machine, so the graph
describes the ffmpeg we actually have (8.1.2 gyan full build), not a
remembered one. Filters, their options, encoders, muxers, pixel formats
and colour properties all become nodes; options hang off their filter.
"""
import os, re, subprocess, sys

sys.path.insert(0, os.environ.get("FILMBRAIN_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fb_store import Store, slug

FF = "ffmpeg"
CREATE_NO_WINDOW = (0x08000000 if os.name == "nt" else 0)


def run(args):
    try:
        p = subprocess.run([FF, "-hide_banner"] + args, capture_output=True, text=True,
                           timeout=120, creationflags=CREATE_NO_WINDOW,
                           errors="replace")
        return (p.stdout or "") + "\n" + (p.stderr or "")
    except Exception as e:
        return ""


def harvest_filters(s):
    txt = run(["-filters"])
    rows = []
    for line in txt.splitlines():
        m = re.match(r"^\s*([TSC.]{1,3})\s+(\S+)\s+(\S+)\s+(.*)$", line)
        if not m:
            continue
        flags, name, io, desc = m.groups()
        if name in ("Filters:", "="):
            continue
        rows.append((flags, name, io, desc.strip()))
    n_nodes = n_edges = 0
    for flags, name, io, desc in rows:
        nid = "ff.filter." + slug(name)
        kindtag = "video" if io.startswith("V") else ("audio" if io.startswith("A") else "other")
        s.add_node(nid, "param", name, title="ffmpeg filter " + name,
                   body=desc + "  [io " + io + ", flags " + flags + "]",
                   source="harvest:ffmpeg", tags=["ffmpeg", "filter", kindtag],
                   meta={"io": io, "flags": flags}, commit=False)
        n_nodes += 1
        s.add_edge(nid, "part_of", "tool.ffmpeg", source="harvest:ffmpeg", commit=False)
        s.add_edge(nid, "is_a", "craft.ffmpeg_filter_" + kindtag, source="harvest:ffmpeg", commit=False)
        n_edges += 2
        if "T" in flags:
            s.add_edge(nid, "applies_to", "craft.timeline_editing", note="timeline-editable",
                       source="harvest:ffmpeg", commit=False)
            n_edges += 1
        if "S" in flags:
            s.add_edge(nid, "applies_to", "craft.parallel_processing", note="slice threading",
                       source="harvest:ffmpeg", commit=False)
            n_edges += 1
    s.commit()

    # options, filter by filter - this is where the real detail is
    for _, name, io, _ in rows:
        txt = run(["-h", "filter=" + name])
        fid = "ff.filter." + slug(name)
        cur = None
        for line in txt.splitlines():
            m = re.match(r"^\s{2,}-?([A-Za-z0-9_]+)\s+<(\w+)>\s+(\S*)\s*(.*)$", line)
            if m:
                opt, typ, fl, desc = m.groups()
                oid = fid + ".opt." + slug(opt)
                s.add_node(oid, "param", opt, title=name + " option " + opt,
                           body=(desc or "").strip() + "  [type " + typ + "]",
                           source="harvest:ffmpeg", tags=["ffmpeg", "option", name],
                           meta={"type": typ, "flags": fl}, commit=False)
                s.add_edge(oid, "option_of", fid, source="harvest:ffmpeg", commit=False)
                n_nodes += 1
                n_edges += 1
                cur = oid
                continue
            m2 = re.match(r"^\s{5,}(\w[\w .-]*)\s+(\d+)\s+.*$", line)
            if m2 and cur:
                val = m2.group(1).strip()
                vid = cur + ".val." + slug(val)
                s.add_node(vid, "param", val, title="value " + val,
                           source="harvest:ffmpeg", tags=["ffmpeg", "value"], commit=False)
                s.add_edge(vid, "option_of", cur, source="harvest:ffmpeg", commit=False)
                n_nodes += 1
                n_edges += 1
        s.commit()
    return n_nodes, n_edges


def harvest_simple(s, flag, kind, prefix, tag, pat=r"^\s*([A-Za-z0-9.]+)\s+(\S+)\s+(.*)$"):
    txt = run([flag])
    n = e = 0
    for line in txt.splitlines():
        m = re.match(pat, line)
        if not m:
            continue
        flags, name, desc = m.groups()
        if not re.match(r"^[A-Za-z0-9_]", name) or name in ("=", "--"):
            continue
        nid = prefix + slug(name)
        s.add_node(nid, kind, name, title=tag + " " + name,
                   body=desc.strip() + "  [" + flags + "]",
                   source="harvest:ffmpeg", tags=["ffmpeg", tag], commit=False)
        s.add_edge(nid, "part_of", "tool.ffmpeg", source="harvest:ffmpeg", commit=False)
        n += 1
        e += 1
    s.commit()
    return n, e


def harvest_pixfmts(s):
    txt = run(["-pix_fmts"])
    n = e = 0
    for line in txt.splitlines():
        m = re.match(r"^([IOHPB.]{5})\s+(\S+)\s+(\d+)\s+(\d+)", line)
        if not m:
            continue
        flags, name, comps, bits = m.groups()
        nid = "ff.pixfmt." + slug(name)
        s.add_node(nid, "format", name, title="pixel format " + name,
                   body=comps + " components, " + bits + " bits per pixel  [" + flags + "]",
                   source="harvest:ffmpeg", tags=["ffmpeg", "pixfmt"],
                   meta={"components": int(comps), "bits": int(bits)}, commit=False)
        s.add_edge(nid, "part_of", "tool.ffmpeg", source="harvest:ffmpeg", commit=False)
        n += 1
        e += 1
        if int(bits) >= 30:
            s.add_edge(nid, "applies_to", "craft.color_grading",
                       note="deep bit depth survives grading", source="harvest:ffmpeg", commit=False)
            e += 1
    s.commit()
    return n, e


def main():
    s = Store()
    # anchors the harvest hangs from
    s.add_node("tool.ffmpeg", "tool", "ffmpeg",
               title="ffmpeg - the transport layer of every pipeline here",
               body="Version 8.1.2-full (gyan). Every engine we build writes frames into "
                    "ffmpeg's stdin rather than to disk; that single choice is what makes "
                    "the map and chalk engines fast enough to be worth having.",
               source="harvest:ffmpeg", tags=["tool", "encode", "filter"])
    for k, lbl in (("video", "video filters"), ("audio", "audio filters"), ("other", "other filters")):
        s.add_node("craft.ffmpeg_filter_" + k, "craft", lbl,
                   title="ffmpeg " + lbl, source="harvest:ffmpeg", tags=["ffmpeg"])
    for anchor, lbl in (("craft.timeline_editing", "timeline editing"),
                        ("craft.parallel_processing", "parallel processing"),
                        ("craft.color_grading", "colour grading")):
        if not s.get(anchor):
            s.add_node(anchor, "craft", lbl, title=lbl, source="authored")

    tot_n = tot_e = 0
    for fn, label in ((harvest_filters, "filters"), (harvest_pixfmts, "pixel formats")):
        n, e = fn(s)
        print(label, n, "nodes", e, "edges", flush=True)
        tot_n += n
        tot_e += e
    for flag, kind, prefix, tag in (
            ("-encoders", "format", "ff.encoder.", "encoder"),
            ("-decoders", "format", "ff.decoder.", "decoder"),
            ("-muxers", "format", "ff.muxer.", "muxer"),
            ("-demuxers", "format", "ff.demuxer.", "demuxer"),
            ("-bsfs", "param", "ff.bsf.", "bitstream filter"),
            ("-protocols", "param", "ff.protocol.", "protocol"),
    ):
        pat = (r"^\s*(\S{1,8})\s+(\S+)\s+(.*)$" if flag not in ("-bsfs", "-protocols")
               else r"^\s*()(\S+)\s*()$")
        n, e = harvest_simple(s, flag, kind, prefix, tag, pat)
        print(flag, n, "nodes", e, "edges", flush=True)
        tot_n += n
        tot_e += e
    print("TOTAL harvest", tot_n, "nodes", tot_e, "edges")
    print(s.stats())


if __name__ == "__main__":
    # Discovery is not execution: this script writes, so a help request must exit
    # before any work. The ratchet dreaming/test_help_flag_is_inert.py holds the line.
    if "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
        print(__doc__ or os.path.basename(__file__))
        raise SystemExit(0)
    main()
