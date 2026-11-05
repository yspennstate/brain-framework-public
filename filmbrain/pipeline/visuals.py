#!/usr/bin/env python
"""
Visual makers for beatfilm. Each returns a callable(path, seconds, film) that
writes a video of exactly that many seconds.

They obey the craft rules the brain already holds: every move eases at both
ends and stays under about three per cent of frame width a second; charts build
in the order of the argument rather than appearing complete; text is large,
high contrast and on screen long enough to be read twice; nothing is left on
screen that is no longer being talked about.
"""
import math, os, subprocess, sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

# Discovery is not execution. This module is a library, but it does real work at
# import (a matplotlib backend, fonts) and its makers write video, so a help request
# run against the file must exit before any of it. Ratchet: dreaming/test_help_flag_is_inert.py.
if __name__ == "__main__" and ("-h" in sys.argv[1:] or "--help" in sys.argv[1:]):
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)

CNW = (0x08000000 if os.name == "nt" else 0)

# Bump this whenever a visual maker's CODE changes. The cache stamps a beat by
# its data spec, so without a code version a renderer fix does not invalidate
# anything and the film silently rebuilds from the broken frames. Found the
# hard way: the card fix below would have been cached away.
#   1 - first version
#   2 - card: explicit axes limits and transAxes (text was not drawing at all,
#       and the rule spanned the full frame); image_move gains fit=True
VERSION = 5

INK = "#f4f1ea"
GROUND = "#14161a"
DIM = "#8b93a1"
ACCENT = "#e0873f"
COOL = "#5b9bd5"
GRID = "#2a2f38"


def _writer(path, w, h, fps):
    return subprocess.Popen(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "{}x{}".format(w, h),
         "-r", str(fps), "-i", "-", "-an",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
         "-pix_fmt", "yuv420p", path],
        stdin=subprocess.PIPE, creationflags=CNW,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _fold(text, width):
    """Break a title onto lines by hand. matplotlib's wrap=True computes its
    width from the axes and on a full-bleed axes it comes out as nothing, which
    drops the text entirely and silently."""
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return "\n".join(lines)


def ease(t):
    """smoothstep - a linear move is the loudest tell of computer animation."""
    t = min(max(t, 0.0), 1.0)
    return t * t * (3 - 2 * t)


def fig_to_rgb(fig, w, h):
    fig.canvas.draw()
    buf = np.asarray(fig.canvas.buffer_rgba())[:, :, :3]
    if buf.shape[0] != h or buf.shape[1] != w:
        from PIL import Image
        buf = np.asarray(Image.fromarray(buf).resize((w, h)))
    return np.ascontiguousarray(buf)


def new_fig(w, h, dpi=100):
    fig = plt.figure(figsize=(w / dpi, h / dpi), dpi=dpi, facecolor=GROUND)
    return fig


# ---------------------------------------------------------------- title card
def card(title, subtitle=None, rule=True):
    def make(path, seconds, film):
        w, h, fps = film.w, film.h, film.fps
        n = max(int(seconds * fps), 1)
        p = _writer(path, w, h, fps)
        fig = new_fig(w, h)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_facecolor(GROUND)
        # Fix the limits. Without these the axes autoscales to whatever is
        # plotted, so "0.5" is not the middle of the frame - it is the middle of
        # the data - and a rule drawn from 0.34 to 0.66 fills the whole width.
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis("off")
        # transAxes, and NO wrap=True: with an axes this size matplotlib's wrap
        # width computed to nothing and the text silently did not draw at all.
        t1 = ax.text(0.5, 0.56, _fold(title, 34), ha="center", va="center", color=INK,
                     fontsize=52, family="Georgia", transform=ax.transAxes,
                     linespacing=1.35)
        t2 = None
        if subtitle:
            t2 = ax.text(0.5, 0.40, _fold(subtitle, 52), ha="center", va="center", color=DIM,
                         fontsize=23, family="Georgia", transform=ax.transAxes,
                         linespacing=1.4)
        # The rule goes BELOW whatever the title actually occupies. At a fixed
        # height it landed between the two lines of any title that wrapped and
        # struck through it. Measure the drawn extent and place it from that.
        rule_y = 0.485
        if rule:
            fig.canvas.draw()
            try:
                bb = t1.get_window_extent(renderer=fig.canvas.get_renderer())
                bb_ax = bb.transformed(ax.transAxes.inverted())
                rule_y = bb_ax.y0 - 0.035
                if t2 is not None:
                    bb2 = t2.get_window_extent(renderer=fig.canvas.get_renderer())
                    top2 = bb2.transformed(ax.transAxes.inverted()).y1
                    rule_y = max(min(rule_y, bb_ax.y0 - 0.02), top2 + 0.03)
            except Exception:
                rule_y = 0.485
        ln = None
        if rule:
            ln, = ax.plot([0.5, 0.5], [rule_y, rule_y], color=ACCENT, lw=2.5,
                          transform=ax.transAxes)
        for i in range(n):
            u = i / max(n - 1, 1)
            a = ease(min(u * 4, 1.0)) * (1 - ease(max((u - 0.86) / 0.14, 0.0)))
            t1.set_alpha(a)
            if t2 is not None:
                t2.set_alpha(a * 0.95)
            if ln is not None:
                half = 0.16 * ease(min(u * 2.2, 1.0))
                ln.set_data([0.5 - half, 0.5 + half], [rule_y, rule_y])
                ln.set_alpha(a)
            p.stdin.write(fig_to_rgb(fig, w, h).tobytes())
        p.stdin.close()
        p.wait()
        plt.close(fig)
    make.spec = ("card", VERSION, title, subtitle, rule)
    return make


# ------------------------------------------------------------ image with move
def image_move(img_path, zoom=1.08, pan=(0.0, 0.0), caption=None, fit=False):
    """A still given slow motion. Under three per cent of frame width a second,
    eased at both ends - the difference between elegant and a slideshow.

    fit=False fills the frame and crops, which is right for a photograph.
    fit=True letterboxes so the WHOLE image is visible, which is what a chart,
    a screenshot or a contact sheet needs - filling the frame with one of those
    throws away the information you put it on screen to show."""
    def make(path, seconds, film):
        from PIL import Image
        w, h, fps = film.w, film.h, film.fps
        n = max(int(seconds * fps), 1)
        im = Image.open(img_path).convert("RGB")
        iw, ih = im.size
        if fit:
            # letterbox onto the ground colour, then treat the result as the plate
            s = min(w / iw, h / ih) * 0.94
            fitted = im.resize((max(int(iw * s), 1), max(int(ih * s), 1)), Image.LANCZOS)
            plate = Image.new("RGB", (w, h), (20, 22, 26))
            plate.paste(fitted, ((w - fitted.size[0]) // 2, (h - fitted.size[1]) // 2))
            im = plate
            iw, ih = im.size
        scale = max(w / iw, h / ih) * zoom
        big = im.resize((int(iw * scale), int(ih * scale)), Image.LANCZOS)
        bw, bh = big.size
        p = _writer(path, w, h, fps)
        for i in range(n):
            u = ease(i / max(n - 1, 1))
            z = 1.0 + (zoom - 1.0) * u
            cw, ch = int(w / z), int(h / z)
            cx = int((bw - cw) / 2 + pan[0] * (bw - cw) / 2 * u)
            cy = int((bh - ch) / 2 + pan[1] * (bh - ch) / 2 * u)
            cx = max(0, min(cx, bw - cw))
            cy = max(0, min(cy, bh - ch))
            fr = big.crop((cx, cy, cx + cw, cy + ch)).resize((w, h), Image.LANCZOS)
            a = np.asarray(fr, dtype=np.uint8)
            if caption:
                a = _caption(a, caption, w, h)
            p.stdin.write(np.ascontiguousarray(a).tobytes())
        p.stdin.close()
        p.wait()
    make.spec = ("image_move", VERSION, img_path, zoom, pan, caption, fit)
    return make


def _caption(arr, text, w, h):
    from PIL import Image, ImageDraw, ImageFont
    im = Image.fromarray(arr)
    d = ImageDraw.Draw(im, "RGBA")
    try:
        f = ImageFont.truetype("georgia.ttf", 30)
    except Exception:
        f = ImageFont.load_default()
    # a scrim, not a drop shadow - a shadow muddies, a scrim reads clean
    d.rectangle([0, h - 120, w, h], fill=(10, 12, 16, 190))
    d.text((70, h - 82), text, font=f, fill=(244, 241, 234, 255))
    return np.asarray(im)


# ------------------------------------------------------------------- a chart
def chart_build(series, xlabel="", ylabel="", title="", annotate=None,
                human_band=None, order=None):
    """Builds in the order of the argument: axes, then each series in turn,
    then the annotation that says what to notice. A chart that appears complete
    asks the viewer to search it while you are still talking."""
    def make(path, seconds, film):
        w, h, fps = film.w, film.h, film.fps
        n = max(int(seconds * fps), 1)
        keys = order or list(series.keys())
        p = _writer(path, w, h, fps)
        fig = new_fig(w, h)
        ax = fig.add_axes([0.11, 0.16, 0.83, 0.72])
        ax.set_facecolor(GROUND)
        for s in ax.spines.values():
            s.set_color(GRID)
        ax.tick_params(colors=DIM, labelsize=15)
        ax.grid(True, color=GRID, lw=0.8)
        ax.set_xlabel(xlabel, color=DIM, fontsize=17)
        ax.set_ylabel(ylabel, color=DIM, fontsize=17)
        if title:
            ax.set_title(title, color=INK, fontsize=25, pad=18, family="Georgia")

        allx = [x for k in keys for x in series[k]["x"]]
        ally = [y for k in keys for y in series[k]["y"]]
        if human_band:
            ally += list(human_band)
        ax.set_xlim(min(allx) - 0.5, max(allx) + 0.5)
        pad = (max(ally) - min(ally)) * 0.15 + 1e-9
        ax.set_ylim(min(ally) - pad, max(ally) + pad)

        band = None
        if human_band:
            band = ax.axhspan(human_band[0], human_band[1], color=COOL, alpha=0.0, lw=0)
            ax.text(max(allx) + 0.35, sum(human_band) / 2, "human", color=COOL,
                    fontsize=14, va="center", ha="right", alpha=0.0)

        lines, labels = [], []
        for k in keys:
            s = series[k]
            ln, = ax.plot([], [], marker="o", ms=7, lw=2.6,
                          color=s.get("color", ACCENT), label=k)
            lines.append(ln)
            # label on the line, never a legend the eye must shuttle to
            labels.append(ax.text(s["x"][-1], s["y"][-1], "  " + k,
                                  color=s.get("color", ACCENT), fontsize=16,
                                  va="center", alpha=0.0))
        note = None
        if annotate:
            note = ax.annotate(annotate["text"], xy=annotate["xy"],
                               xytext=annotate.get("xytext", (0.55, 0.25)),
                               textcoords="axes fraction", color=INK, fontsize=17,
                               alpha=0.0,
                               arrowprops=dict(arrowstyle="->", color=INK, lw=1.6, alpha=0.0))

        seg = 1.0 / (len(keys) + 1.6)
        for i in range(n):
            u = i / max(n - 1, 1)
            if band is not None:
                band.set_alpha(0.10 * ease(min(u / (seg * 0.6), 1.0)))
                for t in ax.texts:
                    if t.get_text() == "human":
                        t.set_alpha(0.7 * ease(min(u / (seg * 0.6), 1.0)))
            for j, k in enumerate(keys):
                lo = seg * (j + 0.35)
                a = ease((u - lo) / seg) if u > lo else 0.0
                s = series[k]
                m = max(int(round(a * len(s["x"]))), 0)
                lines[j].set_data(s["x"][:m], s["y"][:m])
                labels[j].set_alpha(1.0 if m >= len(s["x"]) else 0.0)
            if note is not None:
                a = ease((u - (1 - seg * 1.3)) / (seg * 0.9)) if u > (1 - seg * 1.3) else 0.0
                note.set_alpha(a)
                note.arrow_patch.set_alpha(a)
            p.stdin.write(fig_to_rgb(fig, w, h).tobytes())
        p.stdin.close()
        p.wait()
        plt.close(fig)
    make.spec = ("chart_build", VERSION, str(series)[:400], xlabel, ylabel, title,
                 str(annotate), str(human_band))
    return make


# ------------------------------------------------------------------- a clip
def clip(src, start=0.0, treat=True):
    """A piece of existing footage, cut to the beat's length and given the
    upscale treatment - lanczos, a little unsharp, and grain, which is what
    stops a small render reading as plastic."""
    def make(path, seconds, film):
        vf = "scale={}:{}:flags=lanczos".format(film.w, film.h)
        if treat:
            vf += ",unsharp=5:5:0.6:5:5:0.0,eq=contrast=1.04,noise=alls=4:allf=t+u"
        vf += ",fps={}".format(film.fps)
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        "-stream_loop", "-1", "-ss", str(start), "-i", src,
                        "-t", "{:.3f}".format(seconds), "-vf", vf, "-an",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
                        "-pix_fmt", "yuv420p", path],
                       creationflags=CNW, capture_output=True)
    make.spec = ("clip", VERSION, src, start, treat)
    return make


# ---------------------------------------------------------------- a diagram
def diagram(items, title="", pad=0.06):
    """Boxes, arrows and labels that appear in the order of the argument.

    items is a list of dicts, each drawn in sequence over the beat:
      {"kind":"box",   "at":(x,y), "w":0.22, "h":0.12, "text":"the book",
       "color":ACCENT}
      {"kind":"arrow", "frm":(x,y), "to":(x,y), "text":"hedges"}
      {"kind":"label", "at":(x,y), "text":"...", "size":22}
      {"kind":"note",  "at":(x,y), "text":"...", "color":COOL}

    Coordinates are frame fractions, 0 to 1, origin bottom left. Every element
    fades in over its own slice of the beat and then stays - which is the
    progressive reveal the craft file argues for, and the reason a diagram
    should never simply appear complete.
    """
    def make(path, seconds, film):
        w, h, fps = film.w, film.h, film.fps
        n = max(int(seconds * fps), 1)
        p = _writer(path, w, h, fps)
        fig = new_fig(w, h)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_facecolor(GROUND)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis("off")

        if title:
            ax.text(0.5, 0.93, title, ha="center", va="center", color=INK,
                    fontsize=30, family="Georgia", transform=ax.transAxes)

        artists = []
        for it in items:
            k = it.get("kind", "box")
            col = it.get("color", ACCENT)
            group = []
            if k == "box":
                x, y = it["at"]
                bw, bh = it.get("w", 0.22), it.get("h", 0.12)
                from matplotlib.patches import FancyBboxPatch
                r = FancyBboxPatch((x - bw / 2, y - bh / 2), bw, bh,
                                   boxstyle="round,pad=0.012,rounding_size=0.02",
                                   linewidth=2.0, edgecolor=col, facecolor=GROUND,
                                   transform=ax.transAxes, alpha=0.0)
                ax.add_patch(r)
                group.append(r)
                if it.get("text"):
                    t = ax.text(x, y, _fold(it["text"], it.get("wrap", 18)), ha="center",
                                va="center", color=INK, fontsize=it.get("size", 21),
                                family="Georgia", transform=ax.transAxes, alpha=0.0,
                                linespacing=1.3)
                    group.append(t)
            elif k == "arrow":
                fx, fy = it["frm"]
                tx, ty = it["to"]
                a = ax.annotate("", xy=(tx, ty), xytext=(fx, fy),
                                xycoords=ax.transAxes, textcoords=ax.transAxes,
                                arrowprops=dict(arrowstyle="-|>", color=col, lw=2.2,
                                                shrinkA=6, shrinkB=6, alpha=0.0))
                group.append(a)
                if it.get("text"):
                    # offset PERPENDICULAR to the arrow, or a vertical arrow
                    # runs straight through the middle of its own label
                    dx, dy = tx - fx, ty - fy
                    vertical = abs(dy) > abs(dx)
                    ox = 0.015 + 0.010 * len(it["text"]) / 2 if vertical else 0.0
                    oy = 0.0 if vertical else 0.026
                    t = ax.text((fx + tx) / 2 - ox, (fy + ty) / 2 + oy, it["text"],
                                ha="right" if vertical else "center", va="center", color=col,
                                fontsize=it.get("size", 17), family="Georgia",
                                transform=ax.transAxes, alpha=0.0)
                    group.append(t)
            else:
                x, y = it["at"]
                t = ax.text(x, y, _fold(it["text"], it.get("wrap", 40)), ha=it.get("ha", "center"),
                            va="center", color=col if k == "note" else INK,
                            fontsize=it.get("size", 20), family="Georgia",
                            transform=ax.transAxes, alpha=0.0, linespacing=1.35)
                group.append(t)
            artists.append(group)

        seg = 1.0 / (len(artists) + 1.2) if artists else 1.0
        for i in range(n):
            u = i / max(n - 1, 1)
            for j, group in enumerate(artists):
                lo = seg * (j + 0.25)
                a = ease((u - lo) / (seg * 0.85)) if u > lo else 0.0
                for art in group:
                    # An Annotation's set_alpha only touches its TEXT, and the
                    # text here is empty - so it succeeds, the except never
                    # runs, and the arrow stays invisible while its label
                    # appears. Set the arrow patch explicitly, always.
                    if getattr(art, "arrow_patch", None) is not None:
                        art.arrow_patch.set_alpha(a)
                    try:
                        art.set_alpha(a)
                    except Exception:
                        pass
            p.stdin.write(fig_to_rgb(fig, w, h).tobytes())
        p.stdin.close()
        p.wait()
        plt.close(fig)
    make.spec = ("diagram", VERSION, str(items)[:600], title)
    return make


# ------------------------------------------------------------- a big number
def number(value, label="", prefix="", suffix="", compare=None, decimals=1,
           count_from=0.0):
    """A number that counts up to its value, with its unit and something to
    measure it against. A number alone is not information - the craft node says
    it needs a unit and a comparison, so this makes both hard to omit."""
    def make(path, seconds, film):
        w, h, fps = film.w, film.h, film.fps
        n = max(int(seconds * fps), 1)
        p = _writer(path, w, h, fps)
        fig = new_fig(w, h)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_facecolor(GROUND)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis("off")
        big = ax.text(0.5, 0.56, "", ha="center", va="center", color=ACCENT,
                      fontsize=110, family="Georgia", transform=ax.transAxes)
        lab = ax.text(0.5, 0.40, _fold(label, 46), ha="center", va="center", color=INK,
                      fontsize=27, family="Georgia", transform=ax.transAxes, alpha=0.0,
                      linespacing=1.35)
        cmp_t = None
        if compare:
            cmp_t = ax.text(0.5, 0.29, _fold(compare, 56), ha="center", va="center", color=DIM,
                            fontsize=21, family="Georgia", transform=ax.transAxes, alpha=0.0,
                            linespacing=1.4)
        for i in range(n):
            u = i / max(n - 1, 1)
            k = ease(min(u / 0.45, 1.0))          # counts up over the first 45%
            v = count_from + (value - count_from) * k
            big.set_text("{}{:,.{}f}{}".format(prefix, v, decimals, suffix))
            big.set_alpha(ease(min(u * 6, 1.0)))
            lab.set_alpha(ease(max((u - 0.30) / 0.20, 0.0)))
            if cmp_t is not None:
                cmp_t.set_alpha(ease(max((u - 0.55) / 0.20, 0.0)))
            p.stdin.write(fig_to_rgb(fig, w, h).tobytes())
        p.stdin.close()
        p.wait()
        plt.close(fig)
    make.spec = ("number", VERSION, value, label, prefix, suffix, compare, decimals)
    return make


# ------------------------------------------------------------------- a table
def table(rows, headers=None, title="", highlight=None):
    """A comparison table revealed a row at a time, so the narration can walk
    it. highlight is a row index drawn in the accent colour - the one thing you
    want noticed."""
    def make(path, seconds, film):
        w, h, fps = film.w, film.h, film.fps
        n = max(int(seconds * fps), 1)
        p = _writer(path, w, h, fps)
        fig = new_fig(w, h)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_facecolor(GROUND)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis("off")
        if title:
            ax.text(0.5, 0.90, title, ha="center", va="center", color=INK,
                    fontsize=30, family="Georgia", transform=ax.transAxes)
        ncol = max(len(r) for r in rows) if rows else 1
        xs = [0.12 + (0.76 * (c + 0.5) / ncol) for c in range(ncol)]
        top = 0.76
        step = min(0.09, 0.62 / max(len(rows) + (1 if headers else 0), 1))
        arts = []
        y = top
        if headers:
            hrow = []
            for c, txt in enumerate(headers[:ncol]):
                hrow.append(ax.text(xs[c], y, str(txt), ha="center", va="center",
                                    color=DIM, fontsize=20, family="Georgia",
                                    transform=ax.transAxes, alpha=0.0))
            hrow.append(ax.plot([0.10, 0.90], [y - step * 0.45, y - step * 0.45],
                                color=GRID, lw=1.4, transform=ax.transAxes)[0])
            arts.append(hrow)
            y -= step
        for r_i, row in enumerate(rows):
            group = []
            col = ACCENT if (highlight is not None and r_i == highlight) else INK
            for c, txt in enumerate(row[:ncol]):
                group.append(ax.text(xs[c], y, str(txt), ha="center", va="center",
                                     color=col, fontsize=22, family="Georgia",
                                     transform=ax.transAxes, alpha=0.0))
            arts.append(group)
            y -= step
        seg = 1.0 / (len(arts) + 1.0) if arts else 1.0
        for i in range(n):
            u = i / max(n - 1, 1)
            for j, group in enumerate(arts):
                lo = seg * (j + 0.2)
                a = ease((u - lo) / (seg * 0.8)) if u > lo else 0.0
                for art in group:
                    art.set_alpha(a)
            p.stdin.write(fig_to_rgb(fig, w, h).tobytes())
        p.stdin.close()
        p.wait()
        plt.close(fig)
    make.spec = ("table", VERSION, str(rows)[:600], str(headers), title, highlight)
    return make
