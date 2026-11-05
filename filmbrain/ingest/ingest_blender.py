"""
Turn the Blender API dump into film-brain nodes and edges.

The point is not to mirror the API for its own sake. It is that when we later
ask the brain "how do I get a soft rim light in Cycles" or "what modifier
bends a plane along a curve", the answer should name a real class that exists
in the Blender on this machine, with its real properties.
"""
import os
import json, sys

sys.path.insert(0, os.environ.get("FILMBRAIN_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fb_store import Store, slug

DUMP = os.path.join(os.environ.get("FILMBRAIN_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "store", "blender_api.json")

# which craft anchors each API family belongs under
FAMILY = {
    "shader_nodes":      ("craft.materials_shading", "shader node"),
    "compositor_nodes":  ("craft.compositing", "compositor node"),
    "geometry_nodes":    ("craft.procedural_geometry", "geometry node"),
    "texture_nodes":     ("craft.texturing", "texture node"),
    "function_nodes":    ("craft.procedural_geometry", "function node"),
    "modifiers":         ("craft.modelling", "modifier"),
    "constraints":       ("craft.rigging", "constraint"),
    "sequences":         ("craft.editing", "sequencer strip"),
    "gpencil_modifiers": ("craft.2d_animation", "grease pencil modifier"),
    "shader_fx":         ("craft.2d_animation", "grease pencil effect"),
    "brushes":           ("craft.digital_painting", "brush"),
}

# operator modules mapped to the part of the craft they serve
OP_MODULE_CRAFT = {
    "anim": "craft.animation", "armature": "craft.rigging", "camera": "craft.camera",
    "clip": "craft.motion_tracking", "curve": "craft.modelling", "fluid": "craft.simulation",
    "gpencil": "craft.2d_animation", "grease_pencil": "craft.2d_animation",
    "graph": "craft.animation_curves", "image": "craft.texturing",
    "mesh": "craft.modelling", "nla": "craft.animation", "node": "craft.compositing",
    "object": "craft.scene_layout", "paint": "craft.digital_painting",
    "particle": "craft.simulation", "pose": "craft.rigging", "ptcache": "craft.simulation",
    "render": "craft.rendering", "rigidbody": "craft.simulation",
    "scene": "craft.scene_layout", "sculpt": "craft.modelling",
    "sequencer": "craft.editing", "sound": "craft.sound",
    "surface": "craft.modelling", "transform": "craft.scene_layout",
    "uv": "craft.texturing", "view3d": "craft.viewport", "world": "craft.lighting",
    "material": "craft.materials_shading", "texture": "craft.texturing",
    "cycles": "craft.rendering", "font": "craft.typography", "text": "craft.typography",
    "mask": "craft.compositing", "marker": "craft.editing", "action": "craft.animation",
}


def main():
    s = Store()
    d = json.load(open(DUMP, encoding="utf-8"))
    ver = d.get("version", "?")

    s.add_node("tool.blender", "tool", "Blender",
               title="Blender " + ver + " - the one tool here that can do all of it",
               body="Modelling, shading, Cycles and EEVEE rendering, grease-pencil 2D "
                    "animation, a node compositor, motion tracking, a video sequencer and "
                    "a Python API that drives every one of them from the command line. "
                    "Free, GPL, and already installed. Headless renders run with "
                    "--background --factory-startup and take a script; that is how we "
                    "will drive it, never through the GUI.",
               source="harvest:blender", tags=["tool", "3d", "2d", "compositor", "nle"])

    anchors = {
        "craft.materials_shading": "materials and shading",
        "craft.compositing": "compositing",
        "craft.procedural_geometry": "procedural geometry",
        "craft.texturing": "texturing",
        "craft.modelling": "modelling",
        "craft.rigging": "rigging",
        "craft.editing": "editing",
        "craft.2d_animation": "2D animation",
        "craft.digital_painting": "digital painting",
        "craft.animation": "animation",
        "craft.animation_curves": "animation curves",
        "craft.camera": "camera",
        "craft.motion_tracking": "motion tracking",
        "craft.simulation": "simulation",
        "craft.rendering": "rendering",
        "craft.scene_layout": "scene layout",
        "craft.sound": "sound",
        "craft.viewport": "viewport",
        "craft.lighting": "lighting",
        "craft.typography": "typography",
    }
    for aid, lbl in anchors.items():
        if not s.get(aid):
            s.add_node(aid, "craft", lbl, title=lbl, source="authored", commit=False)
    s.commit()

    n = e = 0
    for fam, rows in d.items():
        if fam not in FAMILY or not isinstance(rows, list):
            continue
        anchor, label = FAMILY[fam]
        for row in rows:
            cls = row.get("name")
            if not cls:
                continue
            nid = "bl.type." + slug(cls)
            body = (row.get("label") or "") + ". " + (row.get("doc") or "")
            s.add_node(nid, "param", cls, title="Blender " + label + " " + (row.get("label") or cls),
                       body=body.strip(), source="harvest:blender",
                       tags=["blender", label.replace(" ", "_")], commit=False)
            n += 1
            s.add_edge(nid, "part_of", "tool.blender", source="harvest:blender", commit=False)
            s.add_edge(nid, "applies_to", anchor, source="harvest:blender", commit=False)
            e += 2
            for p in (row.get("props") or [])[:24]:
                pid = nid + ".p." + slug(p["id"])
                s.add_node(pid, "param", p["id"],
                           title=(row.get("label") or cls) + " . " + (p.get("name") or p["id"]),
                           body=(p.get("desc") or "") + "  [" + p.get("type", "") + "]",
                           source="harvest:blender", tags=["blender", "property"], commit=False)
                s.add_edge(pid, "option_of", nid, source="harvest:blender", commit=False)
                n += 1
                e += 1
        s.commit()

    # operators
    for row in d.get("operators", []):
        mod, name = row["module"], row["name"]
        nid = "bl.op." + slug(mod + "_" + name)
        s.add_node(nid, "param", "bpy.ops." + mod + "." + name,
                   title="Blender operator " + mod + "." + name,
                   body=row.get("doc") or "", source="harvest:blender",
                   tags=["blender", "operator", mod], commit=False)
        s.add_edge(nid, "part_of", "tool.blender", source="harvest:blender", commit=False)
        n += 1
        e += 1
        craft = OP_MODULE_CRAFT.get(mod)
        if craft:
            s.add_edge(nid, "applies_to", craft, source="harvest:blender", commit=False)
            e += 1
    s.commit()

    # render / format settings - the ones that decide what a frame looks like
    for grp, props in (d.get("settings") or {}).items():
        gid = "bl.settings." + slug(grp)
        s.add_node(gid, "param", grp, title="Blender settings group " + grp,
                   source="harvest:blender", tags=["blender", "settings"], commit=False)
        s.add_edge(gid, "part_of", "tool.blender", source="harvest:blender", commit=False)
        n += 1
        e += 1
        for p in props:
            pid = gid + ".p." + slug(p["id"])
            s.add_node(pid, "param", p["id"], title=grp + " . " + (p.get("name") or p["id"]),
                       body=(p.get("desc") or "") + "  [" + p.get("type", "") + "]",
                       source="harvest:blender", tags=["blender", "settings", grp], commit=False)
            s.add_edge(pid, "option_of", gid, source="harvest:blender", commit=False)
            n += 1
            e += 1
        s.commit()

    print("blender:", n, "nodes", e, "edges")
    print(s.stats())


if __name__ == "__main__":
    main()
