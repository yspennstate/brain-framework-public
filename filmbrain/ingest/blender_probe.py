"""
Runs INSIDE blender --background. Dumps the real API surface of the install
we have to json: operators, node trees (shader / geometry / compositor),
modifiers, constraints, render engines and their settings, sequencer effects.

Nothing here is remembered or guessed - it is introspected from bpy at run time,
so the graph matches the Blender on this machine.
"""
import json, sys

import bpy
import os

# Discovery is not execution: this module does its work at import, so a help request
# must exit before any of it. The ratchet dreaming/test_help_flag_is_inert.py holds the line.
if __name__ == "__main__" and ("-h" in sys.argv[1:] or "--help" in sys.argv[1:]):
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)



def safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


out = {"version": ".".join(str(x) for x in bpy.app.version)}

# --- operators: bpy.ops.<module>.<name>, with their docstrings ---
ops = []
for mod_name in dir(bpy.ops):
    if mod_name.startswith("_"):
        continue
    mod = getattr(bpy.ops, mod_name)
    for op_name in dir(mod):
        if op_name.startswith("_"):
            continue
        op = getattr(mod, op_name)
        doc = safe(lambda: (op.__doc__ or "").strip().replace("\n", " "), "") or ""
        ops.append({"module": mod_name, "name": op_name, "doc": doc[:400]})
out["operators"] = ops

# --- rna classes: every node type, modifier, constraint, and their properties ---
def rna_family(base_name, want_props=True, cap_props=40):
    base = getattr(bpy.types, base_name, None)
    if base is None:
        return []
    rows = []
    for cls_name in dir(bpy.types):
        cls = getattr(bpy.types, cls_name, None)
        if cls is None or cls is base:
            continue
        try:
            if not issubclass(cls, base):
                continue
        except TypeError:
            continue
        row = {"name": cls_name,
               "label": safe(lambda: cls.bl_label, "") or "",
               "doc": (safe(lambda: cls.__doc__, "") or "").strip().replace("\n", " ")[:300]}
        if want_props:
            props = []
            rna = safe(lambda: cls.bl_rna)
            if rna is not None:
                for p in list(rna.properties)[:cap_props]:
                    if p.identifier in ("rna_type",):
                        continue
                    props.append({"id": p.identifier,
                                  "name": p.name,
                                  "type": p.type,
                                  "desc": (p.description or "")[:200]})
            row["props"] = props
        rows.append(row)
    return rows


out["shader_nodes"] = rna_family("ShaderNode")
out["compositor_nodes"] = rna_family("CompositorNode")
out["geometry_nodes"] = rna_family("GeometryNode")
out["texture_nodes"] = rna_family("TextureNode")
out["function_nodes"] = rna_family("FunctionNode")
out["modifiers"] = rna_family("Modifier")
out["constraints"] = rna_family("Constraint")
out["sequences"] = rna_family("Strip") or rna_family("Sequence")
out["gpencil_modifiers"] = rna_family("GpencilModifier")
out["shader_fx"] = rna_family("ShaderFx")
out["brushes"] = rna_family("Brush", want_props=False)

# --- render engine settings, the ones that actually change a frame ---
engines = []
for eng in safe(lambda: [e for e in bpy.types.RenderEngine.__subclasses__()], []) or []:
    engines.append({"name": eng.__name__, "label": safe(lambda: eng.bl_label, "") or ""})
out["engines"] = engines

for grp, cls_name in (("cycles", "CyclesRenderSettings"),
                      ("eevee", "SceneEEVEE"),
                      ("render", "RenderSettings"),
                      ("image_settings", "ImageFormatSettings"),
                      ("ffmpeg", "FFmpegSettings"),
                      ("color_management", "ColorManagedViewSettings"),
                      ("view_layer", "ViewLayer"),
                      ("grease_pencil", "GreasePencil"),
                      ("camera", "Camera"),
                      ("light", "Light"),
                      ("world", "World"),
                      ("depth_of_field", "CameraDOFSettings"),
                      ("motion_blur", "SceneEEVEE")):
    cls = getattr(bpy.types, cls_name, None)
    if cls is None:
        continue
    props = []
    rna = safe(lambda: cls.bl_rna)
    if rna is not None:
        for p in rna.properties:
            if p.identifier == "rna_type":
                continue
            props.append({"id": p.identifier, "name": p.name, "type": p.type,
                          "desc": (p.description or "")[:250]})
    out.setdefault("settings", {})[grp] = props

path = sys.argv[-1]
with open(path, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False)
print("WROTE", path, "operators", len(ops))
