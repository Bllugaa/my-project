"""Materials, lighting, backdrop, cameras and render settings (Cycles product shot).

Contract: build_materials() defines every material in common.MATERIAL_NAMES (called before
the parts are built); build_scene() adds world, lights, backdrop and the Cam_* cameras and
sets render settings (called after the parts are built).

Materials (all procedural, object-space textures in model mm)
    Slide        dark nitrided steel, large/small roughness variation, restrained convex
                 edge wear (inside-AO edge mask, strength in the node "Kantenverschleiss")
    SlideInner   same family, darker and rougher (ejection port, cut faces)
    Barrel       matte nitride close to the slide's finish (slightly smoother)
    Frame, Grip  black polymer with micro texture; the base colour is the RGB node
                 "Rahmenfarbe" in Frame. Grip's "Rahmenfarbe" node is driven by it, so
                 changing the colour in Frame recolours frame and grip together.
                 Grip adds the Gen 4 texture: 1.0 mm square pyramids, 0.25 mm high (per-pyramid
                 height jitter), as a triplanar bump below up = -12.5 mm, with smooth margins
                 around the magwell, the magazine catch pockets and the rounded strap corners.
    Polymer, Magazine, Steel (pins: darker, satin), Cavity, Paint (matte, no emission),
    Engraving (letter floors; the letter walls use SlideInner)

Studio (collection "Studio")
    Backdrop     round seamless cyclorama (floor sweeping up into a wall all around, so
                 every camera direction has a seamless background). It is a Curve object
                 revolved by a Screw modifier: it renders as a mesh but is not a MESH
                 object, so build_glock's model-bounds print only measures the pistol.
    Light_*      area lights, aimed by mirror geometry for the main views: Light_Top
                 (overhead softbox, also floor shadow), Light_Key, Light_Fill,
                 Light_Strip_Left/Right and Light_Chamfer (slide top / chamfer / rim
                 highlights), Light_Front (muzzle face), Light_Floor_Card (gradient on the
                 right flank), Light_Kicker (back rim) and Light_Backdrop. All are invisible
                 to the camera but show up in reflections. Light linking: the gun lights
                 only light the pistol (collection "Glock17 Parts"), Light_Backdrop only
                 the backdrop, Light_Top both. Strength is set as softbox radiance (custom
                 property "Leuchtdichte"), power = radiance * pi * area.
                 The Backdrop material shows darker in reflections (value node
                 "Helligkeit in Spiegelungen", like black flags on a real set) so the
                 black pistol stays black against the mid-grey background.

Cameras (collection "Cameras"): Cam_Hero, Cam_Right, Cam_Left, Cam_Top, Cam_Detail_Rear,
Cam_Detail_Port, Cam_Detail_Grip, Cam_Muzzle. Each is framed automatically on the built
geometry (16:10). Close-ups use subtle depth of field focused on an empty "Focus_*".

Shots: each camera has its own frame (1-8, cameras sorted by name) with a timeline marker
bound to it; the light powers are keyed per frame (constant keys, factors in SHOT_LIGHTS), so
scrubbing the timeline switches camera and lighting together. Frame 4 is Cam_Hero.
"""

import math
import warnings

import bpy
import numpy as np
from mathutils import Matrix, Vector

from . import common

# ----------------------------------------------------------------------------- constants

MM = 0.001                 # object space is mm, world space is m (Bump distances are world)

# model bounds in metres (Blender axes: X = x, Y = -right, Z = up)
BOUNDS_LO = Vector((-0.016, -0.0166, -0.1035))
BOUNDS_HI = Vector((0.186, 0.0155, 0.0345))
CENTRE = (BOUNDS_LO + BOUNDS_HI) / 2

FLOAT_GAP = 0.034          # pistol floats this far above the floor (m)
FLOOR_Z = BOUNDS_LO.z - FLOAT_GAP

FRAME_COLOUR = (0.0155, 0.0163, 0.0180, 1.0)   # linear RGB of the black polymer

SENSOR_W = 36.0            # full-frame horizontal sensor (mm)
ASPECT = 1.6               # 16:10

PARTS_COLLECTION = "Glock17 Parts"

# Per-shot light levels (factor on the light's base power). Saved in the .blend as constant
# keyframes: every Cam_* camera has its own frame (timeline marker bound to the camera), so
# scrubbing frames 1-8 switches camera and lighting together. Lights not listed keep 1.0.
SHOT_LIGHTS = {
    "Cam_Top": {"Light_Strip_Right": 0.25, "Light_Chamfer": 0.5, "Light_Backdrop": 1.5},
    "Cam_Detail_Rear": {"Light_Kicker": 0.3},
    "Cam_Muzzle": {"Light_Kicker": 0.15},   # the rim light only draws a blurred streak on the backstrap here
    "Cam_Right": {"Light_Backdrop": 1.35},
    "Cam_Left": {"Light_Backdrop": 1.35},
}


# ----------------------------------------------------------------------------- node helpers

def _sock(sockets, key):
    """Socket by identifier first (needed for the Mix node's typed sockets), then by name/index."""
    if isinstance(key, str):
        for s in sockets:
            if s.identifier == key:
                return s
    return sockets[key]


class Graph:
    """Tiny builder for shader node trees: values or sockets can be passed to any input."""

    def __init__(self, tree):
        self.tree = tree
        self.nodes = tree.nodes
        self.links = tree.links

    def set(self, node, key, val):
        sock = _sock(node.inputs, key)
        if isinstance(val, bpy.types.NodeSocket):
            self.links.new(val, sock)
        elif val is not None:
            sock.default_value = val

    def new(self, kind, x, y, label=None, name=None, ins=None, **props):
        node = self.nodes.new(kind)
        node.location = (x, y)
        if label:
            node.label = label
        if name:
            node.name = name
        for k, val in props.items():
            setattr(node, k, val)
        for k, val in (ins or {}).items():
            self.set(node, k, val)
        return node

    def math(self, op, a, b=None, c=None, x=0, y=0, clamp=False, label=None):
        n = self.new("ShaderNodeMath", x, y, label, operation=op, use_clamp=clamp)
        for i, val in enumerate((a, b, c)):
            if val is not None:
                self.set(n, i, val)
        return n.outputs[0]

    def map_range(self, val, f0, f1, t0, t1, x=0, y=0, smooth=False, clamp=True, label=None):
        n = self.new("ShaderNodeMapRange", x, y, label, clamp=clamp,
                     interpolation_type="SMOOTHSTEP" if smooth else "LINEAR")
        for key, v_ in (("Value", val), ("From Min", f0), ("From Max", f1), ("To Min", t0), ("To Max", t1)):
            self.set(n, key, v_)
        return n.outputs["Result"]

    def mix_color(self, fac, a, b, x=0, y=0, label=None):
        n = self.new("ShaderNodeMix", x, y, label, data_type="RGBA", blend_type="MIX", clamp_factor=True)
        self.set(n, "Factor_Float", fac)
        self.set(n, "A_Color", a)
        self.set(n, "B_Color", b)
        return _sock(n.outputs, "Result_Color")

    def mix_float(self, fac, a, b, x=0, y=0, label=None):
        n = self.new("ShaderNodeMix", x, y, label, data_type="FLOAT", clamp_factor=True)
        self.set(n, "Factor_Float", fac)
        self.set(n, "A_Float", a)
        self.set(n, "B_Float", b)
        return _sock(n.outputs, "Result_Float")

    def noise(self, vec, scale, detail=2.0, rough=0.5, x=0, y=0, label=None, distortion=0.0):
        n = self.new("ShaderNodeTexNoise", x, y, label, noise_dimensions="3D",
                     ins={"Vector": vec, "Scale": scale, "Detail": detail, "Roughness": rough,
                          "Distortion": distortion})
        return n.outputs["Factor"]

    def value(self, val, x, y, label, name=None):
        n = self.new("ShaderNodeValue", x, y, label, name=name or label)
        n.outputs[0].default_value = val
        return n.outputs[0]

    def rgb(self, rgba, x, y, label, name=None):
        n = self.new("ShaderNodeRGB", x, y, label, name=name or label)
        n.outputs[0].default_value = rgba
        return n


def _material(name, viewport_rgba, metallic=0.0, roughness=0.5):
    """Get or create material `name` with an empty node tree plus an output node."""
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        m.use_nodes = True
    if m.node_tree.animation_data:
        m.node_tree.animation_data_clear()
    m.node_tree.nodes.clear()
    m.diffuse_color = viewport_rgba          # solid-mode viewport colour
    m.metallic = metallic
    m.roughness = roughness
    g = Graph(m.node_tree)
    out = g.new("ShaderNodeOutputMaterial", 1100, 0)
    bsdf = g.new("ShaderNodeBsdfPrincipled", 750, 0)
    g.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return m, g, bsdf


def _frame_note(g, text, x, y, w=420, h=60):
    f = g.new("NodeFrame", x, y, label=text)
    f.width, f.height = w, h
    f.label_size = 16
    return f


def _roughness_variation(g, coord, base, var, x=-700, y=-300, fine_scale=1.4, fine_amp=0.6):
    """base roughness +- var: large blotches (~40 mm) and a fine grain (~1/fine_scale mm,
    amplitude fine_amp * var)."""
    big = g.noise(coord, 0.025, 2.0, 0.5, x - 400, y, "Rauheit gross")
    fine = g.noise(coord, fine_scale, 3.0, 0.55, x - 400, y - 220, "Rauheit fein")
    a = g.map_range(big, 0.3, 0.7, -var, var, x - 200, y)
    b = g.map_range(fine, 0.3, 0.7, -fine_amp * var, fine_amp * var, x - 200, y - 220)
    base_v = g.value(base, x - 200, y + 160, "Rauheit")
    return g.math("ADD", g.math("ADD", base_v, a, x=x, y=y), b, x=x + 160, y=y, clamp=True)


def _micro_bump(g, coord, height_mm, scale, x=200, y=-600, extra_height=None, label="Mikrostruktur"):
    """Bump from fine noise; height in object mm (extra_height: further height in mm to add)."""
    n = g.noise(coord, scale, 3.0, 0.6, x - 600, y, label)
    h = g.math("MULTIPLY", n, height_mm, x=x - 400, y=y)
    if extra_height is not None:
        h = g.math("ADD", h, extra_height, x=x - 220, y=y)
    bump = g.new("ShaderNodeBump", x, y, "Bump (Hoehe in mm)",
                 ins={"Strength": 1.0, "Distance": MM, "Height": h})
    return bump.outputs["Normal"]


# ----------------------------------------------------------------------------- materials

def _nitride(name, colour, rough, rough_var, wear, micro_mm, viewport):
    """Nitrided (Tenifer / nDLC) steel: metallic, near-black, satin."""
    m, g, bsdf = _material(name, viewport, 1.0, rough)
    tc = g.new("ShaderNodeTexCoord", -1800, 0)
    coord = tc.outputs["Object"]
    col = g.rgb(colour, -400, 300, "Grundfarbe").outputs[0]
    # finer, weaker grain than on the polymer: faces that sit on the shoulder of a bright
    # highlight (e.g. the slide's rear face against Light_Kicker) otherwise turn blotchy
    r = _roughness_variation(g, coord, rough, rough_var, fine_scale=3.0, fine_amp=0.3)

    if wear > 0:
        # convex-edge mask: AO traced *inside* the solid finds the adjacent face only near
        # convex edges (concave corners stay clean, as on a real gun)
        ao = g.new("ShaderNodeAmbientOcclusion", -1200, 700, "Kanten (AO innen)",
                   inside=True, only_local=True, samples=8, ins={"Distance": 0.9 * MM})
        edge = g.map_range(ao.outputs["AO"], 0.82, 0.45, 0.0, 1.0, -1000, 700, smooth=True)
        breakup = g.noise(coord, 0.55, 4.0, 0.65, -1200, 450, "Verschleiss-Verteilung")
        breakup = g.map_range(breakup, 0.42, 0.68, 0.15, 1.0, -1000, 450)
        amount = g.value(wear, -1000, 900, "Kantenverschleiss", name="Kantenverschleiss")
        mask = g.math("MULTIPLY", g.math("MULTIPLY", edge, breakup, x=-800, y=650), amount,
                      x=-640, y=650, clamp=True, label="Verschleiss-Maske")
        worn = tuple(min(1.0, c * 2.2 + 0.03) for c in colour[:3]) + (1.0,)
        col = g.mix_color(mask, col, worn, -200, 400, "Blanke Kanten")
        r = g.mix_float(mask, r, max(0.18, rough - 0.2), -200, 150, "Glatte Kanten")

    g.set(bsdf, "Base Color", col)
    g.set(bsdf, "Metallic", 1.0)
    g.set(bsdf, "Roughness", r)
    g.set(bsdf, "Normal", _micro_bump(g, coord, micro_mm, 6.0))
    _frame_note(g, f"{name}: nitrierter Stahl (Objektkoordinaten in mm)", -1800, 1050)
    return m


def _polymer_core(name, colour_node_name, colour, rough, viewport):
    m, g, bsdf = _material(name, viewport, 0.0, rough)
    tc = g.new("ShaderNodeTexCoord", -1800, 0)
    rgb = g.rgb(colour, -400, 350, colour_node_name, name=colour_node_name)
    rgb.use_custom_color = True
    rgb.color = (0.55, 0.42, 0.2)
    g.set(bsdf, "Base Color", rgb.outputs[0])
    g.set(bsdf, "Metallic", 0.0)
    g.set(bsdf, "IOR", 1.5)
    g.set(bsdf, "Specular IOR Level", 0.5)
    return m, g, bsdf, tc, rgb


def _frame_material():
    m, g, bsdf, tc, rgb = _polymer_core("Frame", "Rahmenfarbe", FRAME_COLOUR, 0.52, FRAME_COLOUR)
    coord = tc.outputs["Object"]
    g.set(bsdf, "Roughness", _roughness_variation(g, coord, 0.52, 0.035))
    g.set(bsdf, "Normal", _micro_bump(g, coord, 0.006, 4.0))
    _frame_note(g, "Rahmenfarbe hier aendern - gilt fuer Rahmen und Griff", -560, 520, 480)
    return m


def _pyramids(g, a, b, pitch, height, x, y):
    """Height field of square pyramids on the (a, b) plane: peak `height`, 0 on the grid lines."""
    def tri(s, yy):
        f = g.math("FRACT", g.math("DIVIDE", s, pitch, x=x, y=yy), x=x + 150, y=yy)
        return g.math("ABSOLUTE", g.math("SUBTRACT", f, 0.5, x=x + 300, y=yy), x=x + 450, y=yy)
    m = g.math("MAXIMUM", tri(a, y), tri(b, y - 140), x=x + 600, y=y)
    return g.math("MULTIPLY_ADD", m, -2.0 * height, height, x=x + 750, y=y)


def _cell_jitter(g, a, b, pitch, seed, x, y):
    """Per-pyramid height factor 0.85..1.15 (white noise on the grid cell of the (a, b) plane),
    so the moulded texture is not a CG-perfect grid."""
    fa = g.math("FLOOR", g.math("DIVIDE", a, pitch, x=x, y=y), x=x + 150, y=y)
    fb = g.math("FLOOR", g.math("DIVIDE", b, pitch, x=x, y=y - 120), x=x + 150, y=y - 120)
    comb = g.new("ShaderNodeCombineXYZ", x + 300, y, ins={0: fa, 1: fb, 2: float(seed)})
    wn = g.new("ShaderNodeTexWhiteNoise", x + 450, y, noise_dimensions="3D", ins={"Vector": comb.outputs[0]})
    return g.map_range(wn.outputs["Value"], 0.0, 1.0, 0.85, 1.15, x + 600, y)


def _grip_material():
    m, g, bsdf, tc, rgb = _polymer_core("Grip", "Rahmenfarbe", FRAME_COLOUR, 0.55, FRAME_COLOUR)
    rgb.label = "Rahmenfarbe (folgt Material Frame)"
    coord = tc.outputs["Object"]
    pos = g.new("ShaderNodeSeparateXYZ", -1600, -700, "Position (mm)", ins={0: coord})
    nrm = g.new("ShaderNodeSeparateXYZ", -1600, -1300, "Normale (Objekt)", ins={0: tc.outputs["Normal"]})
    X, Y, Z = pos.outputs

    pitch = g.value(1.0, -1600, -400, "Raster (mm)")
    height = 0.25
    h_side = _pyramids(g, X, Z, pitch, height, -1400, -500)      # flat sides (normal +-Y)
    h_strap = _pyramids(g, Y, Z, pitch, height, -1400, -800)     # front/back strap (normal +-X)
    h_flat = _pyramids(g, X, Y, pitch, height, -1400, -1100)     # bottom/top faces (normal +-Z)
    h_side = g.math("MULTIPLY", h_side, _cell_jitter(g, X, Z, pitch, 1, -2400, -500), x=-650, y=-500)
    h_strap = g.math("MULTIPLY", h_strap, _cell_jitter(g, Y, Z, pitch, 2, -2400, -800), x=-650, y=-800)
    h_flat = g.math("MULTIPLY", h_flat, _cell_jitter(g, X, Y, pitch, 3, -2400, -1100), x=-650, y=-1100)

    def weight(s, yy):
        return g.math("POWER", g.math("ABSOLUTE", s, x=-1400, y=yy), 6.0, x=-1250, y=yy)
    wx, wy, wz = weight(nrm.outputs[0], -1300), weight(nrm.outputs[1], -1420), weight(nrm.outputs[2], -1540)
    num = g.math("ADD",
                 g.math("ADD", g.math("MULTIPLY", h_side, wy, x=-500, y=-600),
                        g.math("MULTIPLY", h_strap, wx, x=-500, y=-800), x=-350, y=-700),
                 g.math("MULTIPLY", h_flat, wz, x=-500, y=-1000), x=-200, y=-800)
    den = g.math("ADD", g.math("ADD", wx, wy, x=-1050, y=-1350), g.math("ADD", wz, 1e-4, x=-1050, y=-1500),
                 x=-900, y=-1400)
    tri = g.math("DIVIDE", num, den, x=-50, y=-900, label="Pyramiden (triplanar)")
    # rounded strap corners, where no projection dominates, stay smooth (a moulded edge)
    # instead of mixing two grids into stretched streaks
    dom = g.math("DIVIDE", g.math("MAXIMUM", g.math("MAXIMUM", wx, wy, x=-900, y=-1650), wz, x=-750, y=-1650),
                 den, x=-600, y=-1650)
    edge = g.map_range(dom, 0.6, 0.9, 0.0, 1.0, -450, -1650, smooth=True, label="Kanten ohne Struktur")

    # textured only below up = -12.5 mm, soft 0.5 mm transition ...
    zone = g.map_range(Z, -13.0, -12.5, 1.0, 0.0, -400, -1250, smooth=True, label="Texturzone")
    # ... above a smooth band about 6 mm high around the magwell (grip bottom line
    # up = -94.56 - 0.0816 x; the band also clears the front scallop) ...
    bl = g.math("MULTIPLY_ADD", X, 0.0816, Z, x=-600, y=-1850)
    band = g.map_range(bl, -88.9, -88.4, 0.0, 1.0, -450, -1850, smooth=True, label="Glatter Rand unten")
    # ... and outside a smooth pad around the magazine catch pockets (both sides)
    x0, up0, x1, up1 = 38.2, -27.7, 50.2, -18.3          # frame.MAG_POCKET
    dx = g.math("SUBTRACT", g.math("ABSOLUTE", g.math("SUBTRACT", X, (x0 + x1) / 2, x=-900, y=-2050),
                                   x=-750, y=-2050), (x1 - x0) / 2, x=-600, y=-2050)
    dz = g.math("SUBTRACT", g.math("ABSOLUTE", g.math("SUBTRACT", Z, (up0 + up1) / 2, x=-900, y=-2200),
                                   x=-750, y=-2200), (up1 - up0) / 2, x=-600, y=-2200)
    pad = g.map_range(g.math("MAXIMUM", dx, dz, x=-450, y=-2100), 0.6, 1.1, 0.0, 1.0, -300, -2100,
                      smooth=True, label="Glatt um Magazinhalter")
    zone = g.math("MULTIPLY", g.math("MULTIPLY", zone, band, x=-250, y=-1500),
                  g.math("MULTIPLY", pad, edge, x=-250, y=-1900), x=-100, y=-1500, label="Texturmaske")
    # fade the bump with camera distance: at whole-gun distance the 1 mm grid is only a few
    # pixels wide and would alias into stripes (close-ups < 0.45 m keep full strength)
    cam = g.new("ShaderNodeCameraData", -400, -2400)
    fade = g.map_range(cam.outputs["View Distance"], 0.45, 1.0, 1.0, 0.6, -200, -2400, label="Entfernung")
    h = g.math("MULTIPLY", g.math("MULTIPLY", tri, zone, x=100, y=-900), fade, x=250, y=-900)

    rough = _roughness_variation(g, coord, 0.52, 0.03)
    g.set(bsdf, "Roughness", g.mix_float(zone, rough, 0.6, 400, -300, "Textur rauer"))
    g.set(bsdf, "Normal", _micro_bump(g, coord, 0.006, 4.0, x=500, y=-900, extra_height=h))
    _frame_note(g, "Gen-4-Griffstruktur: Pyramiden 1,0 mm Raster, 0,25 mm hoch (Bump)", -1800, -250, 600)
    _drive_colour(m, rgb)
    return m


def _drive_colour(grip_mat, rgb):
    """Drive Grip's 'Rahmenfarbe' from Frame's so one colour change recolours both."""
    frame = bpy.data.materials.get("Frame")
    if frame is None:
        return
    for i in range(3):
        try:
            fc = rgb.outputs[0].driver_add("default_value", i)
        except Exception:
            return
        drv = fc.driver
        drv.type = "AVERAGE"
        var = drv.variables.new()
        var.name = "c"
        var.type = "SINGLE_PROP"
        var.targets[0].id_type = "MATERIAL"
        var.targets[0].id = frame
        var.targets[0].data_path = f'node_tree.nodes["Rahmenfarbe"].outputs[0].default_value[{i}]'


def _simple(name, colour, metallic, rough, rough_var=0.0, micro_mm=0.0, micro_scale=5.0,
            spec=0.5, emission=0.0, coat=0.0):
    m, g, bsdf = _material(name, colour, metallic, rough)
    tc = g.new("ShaderNodeTexCoord", -1800, 0)
    coord = tc.outputs["Object"]
    g.set(bsdf, "Base Color", g.rgb(colour, -400, 300, "Grundfarbe").outputs[0])
    g.set(bsdf, "Metallic", metallic)
    g.set(bsdf, "Specular IOR Level", spec)
    if rough_var > 0:
        g.set(bsdf, "Roughness", _roughness_variation(g, coord, rough, rough_var))
    else:
        g.set(bsdf, "Roughness", rough)
    if micro_mm > 0:
        g.set(bsdf, "Normal", _micro_bump(g, coord, micro_mm, micro_scale))
    if emission > 0:
        g.set(bsdf, "Emission Color", colour)
        g.set(bsdf, "Emission Strength", emission)
    if coat > 0:
        g.set(bsdf, "Coat Weight", coat)
        g.set(bsdf, "Coat Roughness", 0.3)
    return m


def build_materials():
    """Define every material named in common.MATERIAL_NAMES (reused by name if present)."""
    _nitride("Slide", (0.075, 0.076, 0.080, 1), 0.42, 0.05, 0.55, 0.003, (0.035, 0.037, 0.040, 1))
    _nitride("SlideInner", (0.045, 0.046, 0.048, 1), 0.55, 0.04, 0.0, 0.004, (0.012, 0.012, 0.014, 1))
    _nitride("Barrel", (0.095, 0.096, 0.10, 1), 0.38, 0.04, 0.25, 0.002, (0.05, 0.052, 0.056, 1))
    _frame_material()
    _grip_material()
    _simple("Polymer", (0.0125, 0.013, 0.0145, 1), 0.0, 0.5, 0.03, 0.005, 4.0)
    _simple("Magazine", (0.011, 0.0115, 0.013, 1), 0.0, 0.34, 0.03, 0.002, 6.0, spec=0.6)
    _simple("Steel", (0.30, 0.305, 0.315, 1), 1.0, 0.40, 0.06, 0.002, 8.0)
    _simple("Cavity", (0.0035, 0.0035, 0.0038, 1), 0.0, 0.85, spec=0.25)
    _simple("Paint", (0.78, 0.77, 0.72, 1), 0.0, 0.55, 0.03, 0.003, 5.0)
    _simple("Engraving", (0.30, 0.302, 0.31, 1), 0.55, 0.72, 0.05, 0.004, 12.0)
    missing = [n for n in common.MATERIAL_NAMES if n not in bpy.data.materials]
    if missing:
        raise RuntimeError(f"materials not defined: {missing}")


# ----------------------------------------------------------------------------- scene helpers

def _direction(az_deg, el_deg):
    """Unit vector from the subject towards a viewer/light.

    az 0 = pistol's right side (-Y), 90 = muzzle (+X), 180 = left side (+Y), -90 = rear (-X);
    el = elevation above the horizontal.
    """
    az, el = math.radians(az_deg), math.radians(el_deg)
    return Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)))


def _look_rotation(look, roll_deg=0.0):
    """Rotation matrix whose -Z looks along `look` with +Y as close to world up as possible."""
    look = look.normalized()
    if abs(look.z) > 0.999:
        rot = Matrix.Identity(3) if look.z < 0 else Matrix.Rotation(math.pi, 3, "X")
    else:
        rot = (-look).to_track_quat("Z", "Y").to_matrix()
    return rot @ Matrix.Rotation(math.radians(roll_deg), 3, "Z")


def _link(obj, col_name):
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    common.collection(col_name).objects.link(obj)
    return obj


def _model_points(region=None, max_points=60000):
    """World-space vertices (metres) of the built, visible pistol parts.

    region = ((x0, x1), (up0, up1), (right0, right1)) in model mm keeps only points inside.
    Falls back to the published model bounds when nothing is built.
    """
    col = bpy.data.collections.get(PARTS_COLLECTION)
    objs = [o for o in (col.all_objects if col else bpy.data.objects)
            if o.type == "MESH" and not o.hide_render]
    dg = bpy.context.evaluated_depsgraph_get()
    chunks = []
    for o in objs:
        ev = o.evaluated_get(dg)
        me = ev.data
        n = len(me.vertices)
        if n == 0:
            continue
        co = np.empty(n * 3, dtype=np.float64)
        me.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3)
        mw = np.array(ev.matrix_world)
        chunks.append(co @ mw[:3, :3].T + mw[:3, 3])
    pts = np.concatenate(chunks) if chunks else np.zeros((0, 3))
    if region is not None and len(pts):
        (x0, x1), (u0, u1), (r0, r1) = region
        mm = pts * 1000.0
        keep = ((mm[:, 0] >= x0) & (mm[:, 0] <= x1) & (mm[:, 2] >= u0) & (mm[:, 2] <= u1)
                & (-mm[:, 1] >= r0) & (-mm[:, 1] <= r1))
        pts = pts[keep]
    if len(pts) < 50:
        if region is None:
            lo, hi = BOUNDS_LO, BOUNDS_HI
        else:
            (x0, x1), (u0, u1), (r0, r1) = region
            lo, hi = Vector((x0, -r1, u0)) * MM, Vector((x1, -r0, u1)) * MM
        pts = np.array([(x, y, z) for x in (lo.x, hi.x) for y in (lo.y, hi.y) for z in (lo.z, hi.z)])
    if len(pts) > max_points:
        pts = pts[:: len(pts) // max_points + 1]
    return pts


def _fit_camera(pts, look, lens, roll=0.0, fill=(0.88, 0.86)):
    """Place a camera looking along `look` so the points fill `fill` of the 16:10 frame, centred."""
    rot = _look_rotation(look, roll)
    R = np.array(rot)
    k = lens / (SENSOR_W / 2)
    centre = Vector(pts.mean(axis=0))
    radius = float(np.linalg.norm(pts - np.array(centre), axis=1).max())
    loc = centre - look.normalized() * (radius * k * 1.2)
    for _ in range(30):
        c = (pts - np.array(loc)) @ R            # camera-space coordinates
        depth = -c[:, 2]
        sx = c[:, 0] / depth * k
        sy = c[:, 1] / depth * k * ASPECT
        cx, cy = (sx.max() + sx.min()) / 2, (sy.max() + sy.min()) / 2
        s = max((sx.max() - sx.min()) / 2 / fill[0], (sy.max() - sy.min()) / 2 / fill[1])
        d = float(np.median(depth))
        loc = loc + Vector(R[:, 0]) * (cx * d / k) + Vector(R[:, 1]) * (cy / ASPECT * d / k)
        loc = loc - look.normalized() * (d * s - d)
        if abs(s - 1) < 1e-4 and abs(cx) < 1e-4 and abs(cy) < 1e-4:
            break
    return loc, rot


def _camera(name, look_az, look_el, lens, region=None, roll=0.0, fill=(0.88, 0.86),
            focus=None, fstop=None, pts=None):
    """Create camera `name` framed on the model (or on `region`, model mm). focus = model mm."""
    look = -_direction(look_az, look_el)
    if pts is None:
        pts = _model_points(region)
    loc, rot = _fit_camera(pts, look, lens, roll, fill)
    cam = bpy.data.objects.get(name)
    if cam is not None:
        bpy.data.objects.remove(cam)
    data = bpy.data.cameras.new(name)
    data.lens = lens
    data.sensor_fit = "HORIZONTAL"
    data.sensor_width = SENSOR_W
    data.clip_start = 0.01
    data.clip_end = 20.0
    cam = bpy.data.objects.new(name, data)
    cam.matrix_world = Matrix.Translation(loc) @ rot.to_4x4()
    _link(cam, "Cameras")
    if focus is not None:
        f_name = name.replace("Cam_", "Focus_")
        if bpy.data.objects.get(f_name) is not None:
            bpy.data.objects.remove(bpy.data.objects[f_name])
        f = bpy.data.objects.new(f_name, None)
        f.empty_display_type = "SPHERE"
        f.empty_display_size = 0.002
        f.location = Vector((focus[0], -focus[2], focus[1])) * MM
        _link(f, "Cameras")
        data.dof.use_dof = True
        data.dof.focus_object = f
        data.dof.aperture_fstop = fstop or 16.0
        data.dof.aperture_blades = 7
        data.dof.aperture_rotation = math.radians(10)
    cam["Distanz (m)"] = round((loc - Vector(pts.mean(axis=0))).length, 3)
    return cam


def _area_light(name, size, size_y, az, el, dist, radiance, target=None, colour=(1, 1, 1),
                spread=180.0, shape="RECTANGLE", roll=0.0, receivers=None):
    """Area light aimed at `target` from direction (az, el) at `dist` metres.

    `radiance` is the softbox surface brightness (W/m^2/sr); power = radiance * pi * area,
    so resizing a softbox keeps its brightness in reflections.
    """
    target = target if target is not None else CENTRE
    area = size * size_y * (math.pi / 4 if shape in ("DISK", "ELLIPSE") else 1.0)
    energy = radiance * math.pi * area
    obj = bpy.data.objects.get(name)
    if obj is not None:
        bpy.data.objects.remove(obj)
    data = bpy.data.lights.new(name, "AREA")
    data.shape = shape
    data.size = size
    data.size_y = size_y
    data.energy = energy
    data.color = colour
    data.spread = math.radians(spread)
    obj = bpy.data.objects.new(name, data)
    loc = target + _direction(az, el) * dist
    obj.matrix_world = Matrix.Translation(loc) @ _look_rotation(target - loc, roll).to_4x4()
    obj.visible_camera = False            # softboxes show in reflections, never in frame
    obj["Leuchtdichte (W/m2/sr)"] = radiance
    _link(obj, "Studio")
    if receivers is not None:
        obj.light_linking.receiver_collection = receivers
    return obj


def _backdrop_material():
    """Neutral grey sweep. In reflections it shows darker (like black flags around a
    real product set), so the black pistol stays black while the camera sees mid grey."""
    m, g, bsdf = _material("Backdrop", (0.2, 0.2, 0.2, 1), 0.0, 0.85)
    col = g.rgb((0.16, 0.16, 0.162, 1), -700, 300, "Hintergrundfarbe", name="Hintergrundfarbe")
    flag = g.value(0.6, -700, 50, "Helligkeit in Spiegelungen", name="Helligkeit in Spiegelungen")
    path = g.new("ShaderNodeLightPath", -700, -150)
    fac = g.math("MULTIPLY", path.outputs["Is Glossy Ray"], g.math("SUBTRACT", 1.0, flag, x=-500, y=50),
                 x=-350, y=0)
    dim = g.math("SUBTRACT", 1.0, fac, x=-200, y=0)
    tinted = g.new("ShaderNodeMix", 50, 250, data_type="RGBA", blend_type="MULTIPLY", clamp_factor=True)
    g.set(tinted, "Factor_Float", 1.0)
    g.set(tinted, "A_Color", col.outputs[0])
    g.set(tinted, "B_Color", (1, 1, 1, 1))
    comb = g.new("ShaderNodeCombineXYZ", -50, 0, ins={0: dim, 1: dim, 2: dim})
    g.links.new(comb.outputs[0], _sock(tinted.inputs, "B_Color"))
    g.set(bsdf, "Base Color", _sock(tinted.outputs, "Result_Color"))
    g.set(bsdf, "Roughness", 0.85)
    g.set(bsdf, "Specular IOR Level", 0.25)
    return m


def _backdrop():
    """Round seamless cyclorama: floor -> cove -> wall, revolved around the pistol."""
    r_floor, r_cove, h_wall = 1.7, 0.7, 1.6
    pts = [(0.0, 0.0)] + [(r_floor * i / 6, 0.0) for i in range(1, 7)]
    for i in range(1, 25):
        a = math.pi / 2 * i / 24
        pts.append((r_floor + r_cove * math.sin(a), r_cove * (1 - math.cos(a))))
    pts += [(r_floor + r_cove, r_cove + (h_wall - r_cove) * i / 3) for i in range(1, 4)]
    name = "Backdrop"
    old = bpy.data.objects.get(name)
    if old is not None:
        bpy.data.objects.remove(old)
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    sp = cu.splines.new("POLY")
    sp.points.add(len(pts) - 1)
    for p, (r, z) in zip(sp.points, pts):
        p.co = (r, 0.0, z, 1.0)
    sp.use_smooth = True
    obj = bpy.data.objects.new(name, cu)
    obj.location = (CENTRE.x, CENTRE.y, FLOOR_Z)
    scr = obj.modifiers.new("Revolve", "SCREW")
    scr.axis = "Z"
    scr.angle = 2 * math.pi
    scr.steps = 64
    scr.render_steps = 192
    scr.use_merge_vertices = True
    scr.use_smooth_shade = True
    scr.use_normal_calculate = True
    scr.use_normal_flip = True
    cu.materials.append(_backdrop_material())
    obj["Teil"] = "Hintergrund"
    obj["Beschreibung"] = "Runde Hohlkehle (Kurve + Screw-Modifier), Farbe im Material 'Backdrop'"
    _link(obj, "Studio")
    return obj


def _world():
    world = bpy.data.worlds.get("Studio") or bpy.data.worlds.new("Studio")
    bpy.context.scene.world = world
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    bg = nt.nodes.new("ShaderNodeBackground")
    bg.inputs["Color"].default_value = (0.5, 0.5, 0.51, 1)
    bg.inputs["Strength"].default_value = 0.08
    out = nt.nodes.new("ShaderNodeOutputWorld")
    out.location = (300, 0)
    nt.links.new(bg.outputs[0], out.inputs["Surface"])
    world.color = (0.04, 0.04, 0.041)
    return world


def _lights():
    parts = bpy.data.collections.get(PARTS_COLLECTION)
    # receiver set for the backdrop light (not linked into the scene, only referenced)
    backdrop_only = (bpy.data.collections.get("Backdrop Light Receivers")
                     or bpy.data.collections.new("Backdrop Light Receivers"))
    for o in list(backdrop_only.objects):
        backdrop_only.objects.unlink(o)
    bd = bpy.data.objects.get("Backdrop")
    if bd:
        backdrop_only.objects.link(bd)

    slide_top = Vector((0.09, 0.0, 0.02))
    warm, cool = (1.0, 0.975, 0.95), (0.94, 0.965, 1.0)
    # Gun lights only light the pistol (light linking), so the backdrop tone is set by
    # Light_Top and Light_Backdrop. Strength is given as softbox radiance (W/m^2/sr).
    # big overhead softbox: slide top in the top view, right chamfer in the right view; it
    # also lights the floor and casts the pistol's soft catalog shadow
    _area_light("Light_Top", 0.9, 0.5, 80, 66, 0.75, 2.6, colour=warm)
    # key from front-right, high: muzzle face, front strap, controls
    _area_light("Light_Key", 0.6, 0.45, 50, 32, 0.75, 2.5, colour=warm, receivers=parts)
    # broad, dim fill from the front-left (left side, engraving)
    _area_light("Light_Fill", 0.9, 0.6, 150, 12, 0.9, 0.5, receivers=parts)
    # small strip low behind-left: a gradient highlight on the slide top in the hero shot that
    # runs off towards the rear sight (small, so the top stays black metal instead of turning
    # into a white plateau) and the back-lit top edge in the right side view
    _area_light("Light_Strip_Left", 0.12, 0.05, 208, 22, 0.55, 7.0, target=slide_top, colour=cool,
                spread=90, receivers=parts)
    # high on the right: top-edge rim in the left side view (kept high so it does not
    # put a flat sheen on the right flank in the right side view)
    _area_light("Light_Strip_Right", 1.2, 0.1, -20, 58, 0.55, 10.0, target=slide_top, colour=cool,
                spread=90, receivers=parts)
    # strip high behind-right: gradient along the top-right chamfer in the hero shot
    _area_light("Light_Chamfer", 0.5, 0.1, -62, 52, 0.5, 7.0, target=slide_top, colour=cool,
                spread=90, receivers=parts)
    # upright softbox front-left, low: lights the muzzle face in the hero and muzzle shots
    _area_light("Light_Front", 0.3, 0.45, 112, 4, 0.55, 3.0, colour=warm, receivers=parts)
    # reflector card lying low behind-right (the right flank mirrors this spot in the hero
    # shot): a soft gradient on the otherwise black slide flank
    _area_light("Light_Floor_Card", 0.35, 0.12, -36, -14, 0.3, 0.8, receivers=parts)
    # low kicker strip straight behind: rims the backstrap, beavertail and magazine in both
    # side views and separates them from the floor
    _area_light("Light_Kicker", 0.08, 0.5, -92, 2, 0.5, 20.0, colour=cool, spread=70,
                receivers=parts)
    # ceiling disk over the cyclorama opening, backdrop only: even background tone
    _area_light("Light_Backdrop", 4.8, 4.8, 0, 90, 1.75, 1.0, target=Vector((CENTRE.x, 0, FLOOR_Z)),
                shape="DISK", receivers=backdrop_only if bd else None)


def _cameras():
    pts = _model_points()
    # whole-gun shots
    _camera("Cam_Hero", 36, 16, 100, fill=(0.86, 0.84), pts=pts)
    _camera("Cam_Right", 0, 0, 135, fill=(0.9, 0.84), pts=pts)
    _camera("Cam_Left", 180, 0, 135, fill=(0.9, 0.84), pts=pts)
    _camera("Cam_Top", 0, 90, 120, roll=-18, fill=(0.9, 0.8), pts=pts)
    # close-ups (model mm regions, focus point in model mm: x, up, right). The focus points
    # sit at the depth of the main subject as seen from each camera (checked by ray casting
    # the visible surfaces), and the f-stops keep the blur on the subject under ~5 px at
    # 1200 px width: the rear sight / serrations, the barrel hood in the port, the right grip
    # panel and the muzzle face stay crisp, only far parts fall off.
    _camera("Cam_Detail_Rear", -48, 26, 100, region=((-17, 42), (-24, 35.5), (-17, 17)),
            focus=(8, 18, -8), fstop=22)
    _camera("Cam_Detail_Port", 16, 36, 100, region=((50, 124), (-1, 30), (-17, 17)),
            fill=(0.9, 0.86), focus=(84, 18, 9), fstop=32)
    _camera("Cam_Detail_Grip", 42, 6, 100, region=((-17, 54), (-99, -8), (-17, 17)),
            focus=(30, -50, 14), fstop=22)
    _camera("Cam_Muzzle", 58, 14, 100, region=((70, 187), (-43, 35.5), (-17, 17)),
            fill=(0.84, 0.82), focus=(180, 10, -6), fstop=32)
    bpy.context.scene.camera = bpy.data.objects["Cam_Hero"]


def _shots():
    """One frame per camera (1..8, sorted by name): a timeline marker bound to the camera plus
    constant keyframes on every light's power with the SHOT_LIGHTS factors."""
    scene = bpy.context.scene
    cams = sorted((o for o in bpy.data.objects if o.type == "CAMERA" and o.name.startswith("Cam_")),
                  key=lambda o: o.name)
    lights = sorted((o for o in bpy.data.objects if o.type == "LIGHT" and o.name.startswith("Light_")),
                    key=lambda o: o.name)
    scene.timeline_markers.clear()
    edit = bpy.context.preferences.edit
    old = edit.keyframe_new_interpolation_type
    edit.keyframe_new_interpolation_type = "CONSTANT"
    try:
        for o in lights:
            if o.data.animation_data:
                o.data.animation_data_clear()
        base = {o.name: o.data.energy for o in lights}
        for i, cam in enumerate(cams, 1):
            scene.timeline_markers.new(cam.name, frame=i).camera = cam
            factors = SHOT_LIGHTS.get(cam.name, {})
            for o in lights:
                o.data.energy = base[o.name] * factors.get(o.name, 1.0)
                o.data.keyframe_insert("energy", frame=i)
    finally:
        edit.keyframe_new_interpolation_type = old
    scene.frame_start, scene.frame_end = 1, max(1, len(cams))
    hero = next((i for i, c in enumerate(cams, 1) if c.name == "Cam_Hero"), 1)
    scene.frame_set(hero)
    scene.camera = cams[hero - 1] if cams else scene.camera


def shot_frame(cam_name):
    """Frame of the timeline marker bound to camera `cam_name` (None if there is none)."""
    for mk in bpy.context.scene.timeline_markers:
        if mk.camera is not None and mk.camera.name == cam_name:
            return mk.frame
    return None


def _render_settings():
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    cy = scene.cycles
    cy.device = "CPU"
    cy.samples = 128
    cy.preview_samples = 32
    cy.use_adaptive_sampling = True
    cy.adaptive_threshold = 0.01
    cy.use_denoising = True
    cy.denoiser = "OPENIMAGEDENOISE"
    cy.denoising_input_passes = "RGB_ALBEDO_NORMAL"
    cy.denoising_prefilter = "ACCURATE"
    cy.use_preview_denoising = True
    cy.max_bounces = 12
    cy.diffuse_bounces = 3
    cy.glossy_bounces = 6
    cy.transmission_bounces = 4
    cy.transparent_max_bounces = 8
    cy.volume_bounces = 0
    cy.caustics_reflective = False
    cy.caustics_refractive = False
    cy.blur_glossy = 0.5
    cy.sample_clamp_direct = 0.0
    cy.sample_clamp_indirect = 8.0
    cy.use_light_tree = True
    cy.pixel_filter_type = "BLACKMAN_HARRIS"
    cy.filter_width = 1.5
    r = scene.render
    r.resolution_x, r.resolution_y, r.resolution_percentage = 1600, 1000, 100
    r.film_transparent = False
    r.use_persistent_data = True
    r.image_settings.file_format = "PNG"
    r.image_settings.color_depth = "8"
    vs = scene.view_settings
    vs.view_transform = "AgX"
    vs.look = "AgX - Medium High Contrast"
    vs.exposure = 0.0
    vs.gamma = 1.0
    scene.display_settings.display_device = "sRGB"


def build_scene():
    """World, backdrop, lights, cameras and Cycles settings for the product shots."""
    if not all(n in bpy.data.materials for n in common.MATERIAL_NAMES):
        build_materials()
    _world()
    _backdrop()
    _lights()
    _cameras()
    _render_settings()
    _shots()
