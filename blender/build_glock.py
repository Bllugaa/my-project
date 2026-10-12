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

    bpy.context.view_layer.update()
    parts = [o for o in bpy.data.objects if o.type == "MESH" and not o.hide_render]
    lo, hi = common.evaluated_bounds(parts)
    size = (hi - lo) * 1000
    print(f"model bounds (mm): length {size.x:.1f}  width {size.y:.1f}  height {size.z:.1f}")
    return parts


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
    for cam in cams:
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
