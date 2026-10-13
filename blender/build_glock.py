"""Build the Glock 17 display model in Blender and save glock17.blend.

Run with Blender:
    blender --background --python blender/build_glock.py -- [options]
or with the `bpy` module from PyPI (Python 3.13, `pip install bpy`):
    python blender/build_glock.py [options]

Options
    --out PATH          where to save the .blend (default: blender/glock17.blend)
    --no-save           build only, do not save
    --only a,b          build only these part modules (slide, frame); look is always applied
    --render LIST       cameras to render: comma list of names without "Cam_" prefix, "all" or "none"
    --samples N         Cycles samples for --render (default 128)
    --res WxH           render resolution (default 1600x1000)
    --render-dir PATH   output folder for renders (default: blender/renders)
"""

import argparse
import importlib
import os
import sys
import time
import traceback

import bpy
from mathutils import Matrix, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from glock import common  # noqa: E402

PART_MODULES = ("slide", "frame")


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=os.path.join(HERE, "glock17.blend"))
    p.add_argument("--no-save", action="store_true")
    p.add_argument("--only", default=",".join(PART_MODULES))
    p.add_argument("--render", default="none")
    p.add_argument("--samples", type=int, default=128)
    p.add_argument("--res", default="1600x1000")
    p.add_argument("--render-dir", default=os.path.join(HERE, "renders"))
    return p.parse_args(argv)


def load(name):
    """Import glock.<name>, reloading it so repeated runs inside one Blender session pick up edits."""
    mod = importlib.import_module(f"glock.{name}")
    return importlib.reload(mod)


def build(only=PART_MODULES):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1.0
    scene.unit_settings.length_unit = "MILLIMETERS"
    common.root()

    look = None
    try:
        look = load("look")
        look.build_materials()
    except Exception:
        print("look.build_materials failed, using placeholder materials:")
        traceback.print_exc()
        look = None

    for name in only:
        t = time.time()
        load(name).build()
        print(f"built {name} in {time.time() - t:.1f}s")

    if look is not None:
        try:
            look.build_scene()
        except Exception:
            print("look.build_scene failed:")
            traceback.print_exc()
            look = None
    if look is None:
        fallback_scene()

    tidy_for_editing()
    bpy.context.view_layer.update()
    parts = [o for o in bpy.data.objects if o.type == "MESH" and not o.hide_render]
    lo, hi = common.evaluated_bounds(parts)
    size = (hi - lo) * 1000
    print(f"model bounds (mm): length {size.x:.1f}  width {size.y:.1f}  height {size.z:.1f}")
    return parts


# parts that get their origin moved to their own centre (or pivot), so rotating them in
# Blender behaves; only parts without children and with noise-only object-space textures
# (Grip's texture zones and the sights' parented inlays depend on the model origin)
PIVOTS = {"Trigger": (72.0, -5.0, 0.0),             # trigger pin (frame.PIN_TRIGGER)
           "SlideStop": None, "MagRelease": None, "Magazine": None}

README = """\
Glock 17 Gen 4 - Anzeigemodell (nur Aussenform, nicht funktionsfaehig)
======================================================================

Diese Datei wird erzeugt von blender/build_glock.py (Teile in blender/glock/*.py):
    python blender/build_glock.py [--out PFAD] [--render all] [--samples 128] [--res 1600x1000]
Aenderungen am besten im Skript machen und neu bauen; die Datei wird dabei ueberschrieben.

Einheiten und Achsen
    Alle Netze sind in Millimetern modelliert und am Empty "Glock17" (Massstab 0.001)
    angehaengt; die Szene selbst ist in Metern (die Pistole ist 0,202 m lang).
    Blender X = zur Muendung, Y = zur linken Seite der Pistole, Z = oben.
    Ansicht vorne (Numpad 1) zeigt die rechte Seite (Auswurffenster), Muendung rechts.

Sammlungen
    Glock17 Parts   sichtbare Teile (Name/Beschreibung als Custom Properties im N-Panel)
    Cutters         Boolean-Operanden (live, also editierbar). Die Sammlung ist mit dem
                    Augen-Symbol ausgeblendet: ein Klick darauf zeigt alle als Drahtgitter.
                    "Barrel_add_hood" wird addiert (Union), alle anderen werden abgezogen.
    Studio          runde Hohlkehle "Backdrop" (nicht anklickbar, von aussen durchsichtig)
                    und die Flaechenlichter Light_* (Light Linking: die Pistolenlichter
                    beleuchten nur "Glock17 Parts")
    Cameras         Cam_* und die Fokus-Empties Focus_*

Kameras und Bilder
    Jede Kamera hat ein eigenes Bild in der Zeitleiste (1-8, Marker an die Kamera gebunden):
    1 Detail_Grip, 2 Detail_Port, 3 Detail_Rear, 4 Hero, 5 Left, 6 Muzzle, 7 Right, 8 Top.
    Die Lichtstaerken sind pro Bild mit konstanten Keyframes gesetzt (Faktoren in
    look.SHOT_LIGHTS) - eine Lichtaenderung im UI also per Keyframe (I) uebernehmen.
    Rendern: Cycles; Bild waehlen und F12.

Materialien
    Rahmenfarbe im Material "Frame", Node "Rahmenfarbe" (gilt auch fuer den Griff).
    Kantenverschleiss im Material "Slide", Node "Kantenverschleiss".
"""


def frame_viewports():
    """Point every 3D view at the pistol (hero angle, 0.45 m) instead of the factory 18 m view,
    with clip distances for a 0.2 m object and backface culling so the bowl does not block it."""
    cam = bpy.data.objects.get("Cam_Hero")
    rot = cam.matrix_world.to_quaternion() if cam else None
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            for sp in area.spaces:
                if sp.type != "VIEW_3D":
                    continue
                sp.clip_start = 0.001
                sp.clip_end = 100.0
                sp.shading.show_backface_culling = True
                r3 = sp.region_3d
                r3.view_location = (0.085, 0.0, -0.035)
                r3.view_distance = 0.45
                if rot is not None:
                    r3.view_rotation = rot
                r3.view_perspective = "PERSP"
    bd = bpy.data.objects.get("Backdrop")
    if bd is not None:
        bd.hide_select = True
    m = bpy.data.materials.get("Backdrop")
    if m is not None:
        m.use_backface_culling = True


def _set_pivots():
    for name, pivot in PIVOTS.items():
        o = bpy.data.objects.get(name)
        if o is None or o.type != "MESH" or o.children:
            continue
        me = o.data
        if pivot is None:
            co = [vt.co for vt in me.vertices]
            lo = Vector(map(min, *co)) if co else Vector()
            hi = Vector(map(max, *co)) if co else Vector()
            c = (lo + hi) / 2
        else:
            c = common.v(*pivot)
        me.transform(Matrix.Translation(-c))
        o.location = o.location + c


def _readme():
    t = bpy.data.texts.get("LIESMICH") or bpy.data.texts.new("LIESMICH")
    t.from_string(README)
    ws = bpy.data.workspaces.get("Scripting")
    for screen in (ws.screens if ws else []):
        for area in screen.areas:
            if area.type == "TEXT_EDITOR":
                area.spaces.active.text = t


def tidy_for_editing():
    """Things that only matter when the .blend is opened: cutters hidden by their collection,
    part pivots, a framed viewport and a LIESMICH text."""
    common.hide_cutters()
    _set_pivots()
    frame_viewports()
    _readme()


def fallback_scene():
    """Minimal light + camera so a module can be tested before look.py exists."""
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    world = bpy.data.worlds.new("World") if not scene.world else scene.world
    scene.world = world
    world.color = (0.6, 0.62, 0.65)
    light = bpy.data.objects.new("Key", bpy.data.lights.new("Key", "AREA"))
    light.data.energy = 60
    light.data.size = 0.4
    light.location = (0.15, -0.35, 0.4)
    light.rotation_euler = (0.7, 0, 0.4)
    scene.collection.objects.link(light)
    cam = bpy.data.objects.new("Cam_Hero", bpy.data.cameras.new("Cam_Hero"))
    cam.data.lens = 70
    cam.location = (0.32, -0.48, 0.16)
    scene.collection.objects.link(cam)
    target = bpy.data.objects.new("CamTarget", None)
    target.location = (0.085, 0, -0.03)
    scene.collection.objects.link(target)
    c = cam.constraints.new("TRACK_TO")
    c.target = target
    scene.camera = cam


def render(names, samples, res, out_dir):
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = samples
    scene.cycles.use_denoising = True
    w, h = (int(n) for n in res.lower().split("x"))
    scene.render.resolution_x, scene.render.resolution_y = w, h
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    os.makedirs(out_dir, exist_ok=True)
    cams = sorted((o for o in bpy.data.objects if o.type == "CAMERA" and o.name.startswith("Cam_")), key=lambda o: o.name)
    if names != ["all"]:
        cams = [c for c in cams if c.name[4:].lower() in names]
    markers = {m.camera.name: m.frame for m in scene.timeline_markers if m.camera is not None}
    for cam in cams:
        if cam.name in markers:
            scene.frame_set(markers[cam.name])   # per-shot light levels (look.SHOT_LIGHTS)
        scene.camera = cam
        scene.render.filepath = os.path.join(out_dir, cam.name[4:].lower() + ".png")
        t = time.time()
        bpy.ops.render.render(write_still=True)
        print(f"rendered {cam.name} -> {scene.render.filepath} in {time.time() - t:.1f}s")


def main():
    args = parse_args()
    only = [s for s in args.only.split(",") if s]
    build(only)
    if not args.no_save:
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.out), compress=True)
        print("saved", os.path.abspath(args.out))
    if args.render != "none":
        render([s.strip().lower() for s in args.render.split(",")], args.samples, args.res, args.render_dir)


if __name__ == "__main__":
    main()
