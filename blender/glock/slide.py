"""Slide group: slide, barrel, sights, extractor, cover plate, guide rod, engraving.

Contract: build() creates the objects with the glock.common helpers, links/parents them
to the root empty (common.link), assigns materials by name (common.assign) and returns None.

Ported from buildSlide(), buildBarrel(), buildSights() and buildSlideDetails() in
../../index.html (same model coordinates in mm: x towards the muzzle, up, right), then
refined with what Blender does better than the three.js version:

* every opening is a live EXACT boolean with a hidden cutter (collection "Cutters"), so
  the shapes stay editable; cut faces take the cutter's material (SlideInner, Cavity, ...)
* the left-side marking "17 Gen4 / AUSTRIA 9x19" is a real 0.2 mm deep boolean engraving,
  evaluated after the bevel so the letter edges stay crisp like a laser engraving
* the barrel (13.5 mm at the muzzle) has a polygonal bore (round with six soft lands) with
  a 1:250 mm right-hand twist, a chamfered muzzle crown and a filleted chamber shoulder
* the rear sight has drafted sides and a rounded U-notch; the white U outline and the
  front-sight dot are separate Paint inlays set into the sight faces

Exterior-only display model: no breech, firing pin, rails, locking block or spring
internals. Parts are solid shells; nothing is made to fit or function together.
"""

import math
import os

import bmesh
import bpy
from mathutils import Matrix

from . import common
from .common import v

# ----------------------------------------------------------------------------- dimensions (mm)

SLIDE_L = 186.0
SLIDE_W = 25.5
SLIDE_H = 28.0
HALF_W = SLIDE_W / 2
BORE_UP = 18.5        # barrel axis above the slide's bottom edge
ROD_UP = 6.4          # recoil-spring guide rod axis
TWIST_MM = 250.0      # polygonal rifling: one right-hand turn in 250 mm

BARREL_X0 = 71.9      # rear of the chamber section (hidden under the slide)
BARREL_X1 = 185.9     # muzzle face, 0.1 mm behind the slide's front face -> 114 mm barrel

ENGRAVE_DEPTH = 0.2

# Clean bold grotesque for the slide marking; condensed in x to look like the narrow
# factory lettering. Falls back to Blender's built-in font when none is installed.
FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
)
FONT_CONDENSE = 0.86


# ----------------------------------------------------------------------------- local helpers
# (not in common.py; kept here so other modules are unaffected)

def _bm_object(name, bm):
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    return bpy.data.objects.new(name, me)


def _loft(name, rings, triangulate=False):
    """Closed mesh through equally long vertex rings (lists of Vectors), capped at both ends."""
    bm = bmesh.new()
    vs = [[bm.verts.new(p) for p in ring] for ring in rings]
    n = len(vs[0])
    bm.faces.new(vs[0])
    bm.faces.new(list(reversed(vs[-1])))
    sides = []
    for a, b in zip(vs, vs[1:]):
        for i in range(n):
            j = (i + 1) % n
            sides.append(bm.faces.new((a[i], a[j], b[j], b[i])))
    if triangulate:
        bmesh.ops.triangulate(bm, faces=sides)
    return _bm_object(name, bm)


def _lathe_x(name, profile, up, right=0.0, segments=64):
    """Solid of revolution about an axis parallel to x; profile = [(x, radius), ...], radius > 0."""
    rings = []
    for x, r in profile:
        rings.append([v(x, up + r * math.sin(2 * math.pi * i / segments),
                        right + r * math.cos(2 * math.pi * i / segments)) for i in range(segments)])
    return _loft(name, rings)


def _twisted_prism(name, x0, x1, radius, sides, up, right=0.0, turn_mm=TWIST_MM, x_ref=0.0,
                   phase=math.pi / 2, step=1.5, lobe=0.0, lobes=6):
    """Prism along x whose section turns clockwise (seen from the rear) by one full turn every
    `turn_mm` -> right-hand polygonal rifling.

    lobe = 0: regular polygon with `sides` corners. lobe > 0: a finely sampled round section
    with radius + lobe * cos(lobes * angle), i.e. a polygonal bore with soft, low lands.
    """
    n = max(2, int(math.ceil((x1 - x0) / step)) + 1)
    rings = []
    for k in range(n):
        x = x0 + (x1 - x0) * k / (n - 1)
        a0 = phase - 2 * math.pi * (x - x_ref) / turn_mm
        ring = []
        for i in range(sides):
            a = 2 * math.pi * i / sides
            r = radius + lobe * math.cos(lobes * a)
            ring.append(v(x, up + r * math.sin(a0 + a), right + r * math.cos(a0 + a)))
        rings.append(ring)
    return _loft(name, rings, triangulate=True)


def _join(name, objs):
    """Merge several unlinked helper objects into one mesh object (keeps per-face material index)."""
    verts, faces, mats = [], [], []
    for o in objs:
        base = len(verts)
        verts.extend(tuple(vt.co) for vt in o.data.vertices)
        for p in o.data.polygons:
            faces.append([base + i for i in p.vertices])
            mats.append(p.material_index)
        me_old = o.data
        bpy.data.objects.remove(o)
        bpy.data.meshes.remove(me_old)
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.update()
    me.polygons.foreach_set("material_index", mats)
    return bpy.data.objects.new(name, me)


def _slot(cx, cu, length, width, angle, seg=8):
    """Side-view stadium (milled groove) centred at (cx, cu), long axis vertical, then rotated
    by `angle` like THREE's rotateZ (negative angle leans the top towards the muzzle)."""
    r = width / 2
    h = length / 2 - r
    pts = []
    for i in range(seg + 1):                       # top end, right -> left
        a = math.pi * i / seg
        pts.append((r * math.cos(a), h + r * math.sin(a)))
    for i in range(seg + 1):                       # bottom end, left -> right
        a = math.pi + math.pi * i / seg
        pts.append((r * math.cos(a), -h + r * math.sin(a)))
    c, s = math.cos(angle), math.sin(angle)
    return [(cx + x * c - y * s, cu + x * s + y * c) for x, y in pts]


def _font():
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return bpy.data.fonts.load(path, check_existing=True), True
            except RuntimeError:
                pass
    return bpy.data.fonts.load("<builtin>", check_existing=True), False


def _text(name, body, cap_height, front_x, baseline_up, depth, font, condense):
    """Engraving slab for the LEFT side (reads correctly seen from the left, muzzle on the left).

    Same contract as common.text_mesh(facing='left') but with a selectable font and a
    horizontal condense factor; the slab's visible front end (towards the muzzle) is placed
    exactly at front_x, its baseline at baseline_up, centred on the left side surface.
    `cap_height` is the height of a capital letter / digit in mm (font size is derived from it).
    """
    curve = bpy.data.curves.new(name + "_curve", "FONT")
    curve.body = body
    curve.size = cap_height / _cap_ratio(font)
    curve.extrude = depth / 2
    curve.resolution_u = 5
    curve.font = font
    tmp = bpy.data.objects.new(name + "_tmp", curve)
    bpy.context.scene.collection.objects.link(tmp)
    # viewer at +Y (pistol's left): text X -> -X, text Y -> +Z, text normal -> +Y
    basis = Matrix(((-1, 0, 0, 0), (0, 0, 1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))
    squeeze = Matrix.Diagonal((condense, 1, 1, 1))
    tmp.matrix_world = Matrix.Translation(v(front_x, baseline_up, -HALF_W)) @ basis @ squeeze
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(tmp.evaluated_get(dg))
    me.transform(tmp.matrix_world)
    bpy.data.objects.remove(tmp)
    bpy.data.curves.remove(curve)
    max_x = max(vt.co.x for vt in me.vertices)
    me.transform(Matrix.Translation((front_x - max_x, 0, 0)))
    me.name = name
    return bpy.data.objects.new(name, me)


_CAP_RATIO = {}


def _cap_ratio(font):
    """Cap height of `font` per unit of Blender text size (measured on an 'H')."""
    if font.name not in _CAP_RATIO:
        curve = bpy.data.curves.new("cap_probe", "FONT")
        curve.body = "H"
        curve.font = font
        tmp = bpy.data.objects.new("cap_probe", curve)
        bpy.context.scene.collection.objects.link(tmp)
        bpy.context.view_layer.update()
        me = bpy.data.meshes.new_from_object(tmp.evaluated_get(bpy.context.evaluated_depsgraph_get()))
        ys = [vt.co.y for vt in me.vertices]
        _CAP_RATIO[font.name] = (max(ys) - min(ys)) or 0.7
        bpy.data.meshes.remove(me)
        bpy.data.objects.remove(tmp)
        bpy.data.curves.remove(curve)
    return _CAP_RATIO[font.name]


def _x_span(obj):
    xs = [vt.co.x for vt in obj.data.vertices]
    return min(xs), max(xs)


def _finish(obj, bevel, segments=3, **kw):
    """common.finish plus face-strength weighting: the original (flat) faces keep their own
    normals next to the bevels, which avoids smudges on big boolean-split flat faces."""
    common.finish(obj, bevel=bevel, segments=segments, **kw)
    b = obj.modifiers.get("Bevel")
    w = obj.modifiers.get("Weighted Normal")
    if b is not None and w is not None:
        b.face_strength_mode = "FSTR_AFFECTED"
        w.use_face_influence = True
    return obj


def _after_bevel(obj, mod):
    """Move modifier `mod` to directly after the Bevel (so its cut edges stay sharp)."""
    names = [m.name for m in obj.modifiers]
    src = names.index(mod.name)
    dst = names.index("Bevel") + 1 if "Bevel" in names else src
    if src != dst:
        obj.modifiers.move(src, dst)
    return mod


def _cut(obj, operand, material, label, operation="DIFFERENCE"):
    common.cutter(operand, material)
    return common.boolean(obj, operand, operation=operation, name=label)


def _material_by_x(obj, x_split, below, above):
    """Material slot index per face: `above` for faces whose centre lies beyond x_split."""
    for p in obj.data.polygons:
        p.material_index = above if p.center.x > x_split else below


# ----------------------------------------------------------------------------- slide

def _engraving_cutter():
    font, ok = _font()
    if not ok:
        print("slide: no bold TTF found, engraving uses Blender's built-in font")
    d = ENGRAVE_DEPTH * 2       # slab centred on the surface -> cuts depth/2 deep
    front = 175.0
    # cap heights as drawn in index.html: "17" 4.4 mm, "Gen4" 2.6 mm, second line 1.8 mm
    big = _text("eng_17", "17", 4.4, front, 12.8, d, font, FONT_CONDENSE)
    gen = _text("eng_gen4", "Gen4", 2.6, _x_span(big)[0] - 1.3, 12.8, d, font, FONT_CONDENSE)
    aus = _text("eng_austria", "AUSTRIA", 1.8, front, 8.4, d, font, FONT_CONDENSE)
    cal = _text("eng_cal", "9x19", 1.8, _x_span(aus)[0] - 1.1, 8.4, d, font, FONT_CONDENSE)
    cutter = _join("Slide_cut_engraving", [big, gen, aus, cal])
    # letter floors light (Engraving), letter walls dark (SlideInner): reads as a cut, not a
    # print. The boolean (material TRANSFER) carries these per-face materials into the slide.
    common.assign(cutter, "Engraving", "SlideInner")
    cutter.data.polygons.foreach_set(
        "material_index", [0 if abs(p.normal.y) > 0.9 else 1 for p in cutter.data.polygons])
    # the text is meshed now: drop the font we loaded (and the default font the
    # temporary text curves pulled in) so the .blend does not reference font files
    for f in list(bpy.data.fonts):
        if f == font or (f.name.startswith("Bfont") and f.users == 0):
            bpy.data.fonts.remove(f, do_unlink=True)
    return cutter


def build_slide():
    # cross-section: flat top with 45 degree chamfers, slightly broken lower edges
    cs = [(-HALF_W + 0.6, 0), (HALF_W - 0.6, 0), (HALF_W, 0.6), (HALF_W, 24.6),
          (9.4, SLIDE_H), (-9.4, SLIDE_H), (-HALF_W, 24.6), (-HALF_W, 0.6)]
    slide = common.cross_section("Slide", cs, 0, SLIDE_L)
    common.assign(slide, "Slide", "SlideInner", "Cavity", "Engraving")
    common.link(slide)
    common.part_info(slide, "Schlitten", "186 × 25,5 × 28 mm, gleitet beim Schuss zurück")

    # 45 degree top chamfers at the muzzle (plane through x 183.4 on the top) and the rear (x 1.4)
    chamfers = _join("Slide_cut_chamfers", [
        common.side_profile("c_front", [(176, 35.4), (196, 15.4), (196, 35.4)], 40),
        common.side_profile("c_rear", [(-10, 16.6), (11.4, 38), (-10, 38)], 40),
    ])
    _cut(slide, chamfers, "Slide", "Top chamfers")

    # ejection port: open on the top and the right side, front edge slanted down; about
    # 34.5 mm long at the top, so the barrel hood and the chamber shoulder fill most of it
    port = common.side_profile("Slide_cut_port", [(74, 15.6), (101.5, 15.6), (108.5, 23), (108.5, 40), (74, 40)],
                               0, right0=-6.9, right1=20)
    _cut(slide, port, "SlideInner", "Ejection port")
    # dark well under the barrel: the shell's opening goes deeper than the port floor, with
    # a 2 mm wall left under the window edge, so the barrel hangs over shadow instead of
    # sitting on a lit shelf (no internal parts are modelled)
    well = common.box("Slide_cut_port_well", 75.5, 100.0, 9.0, 16.0, -6.6, HALF_W - 2.0)
    _cut(slide, well, "Cavity", "Ejection port well")

    # muzzle bore around the barrel and the guide-rod hole below it
    bores = _join("Slide_cut_bores", [
        common.cylinder_x("b_muzzle", 168, 196, 7.1, up=BORE_UP, segments=72),
        common.cylinder_x("b_rod", 172, 196, 3.0, up=ROD_UP, segments=40),
    ])
    _cut(slide, bores, "Cavity", "Muzzle + guide rod holes")

    # notch on the lower left edge that the slide stop catches
    notch = common.box("Slide_cut_stop_notch", 78.5, 86.5, -1, 3.4, -14, -HALF_W + 0.9)
    _cut(slide, notch, "SlideInner", "Slide stop notch")

    # recess around the extractor on the right side (0.6 mm deep)
    pocket = common.side_profile("Slide_cut_extractor_pocket", common.round_rect(55.2, 10.6, 75.8, 15.2, 1.2),
                                 0, right0=HALF_W - 0.6, right1=15)
    _cut(slide, pocket, "SlideInner", "Extractor pocket")

    # shallow seat for the cover plate in the rear face (plate sits 0.1 mm below the face)
    seat = common.cross_section("Slide_cut_cover_seat", common.round_rect(-6.65, 6.85, 6.65, 21.15, 1.35), -1, 0.3)
    _cut(slide, seat, "SlideInner", "Cover plate seat")

    # 9 rear cocking serrations per side, leaning with the grip (milled, rounded ends)
    grooves = []
    for side in (-1, 1):
        r_in, r_out = side * (HALF_W - 0.85), side * (HALF_W + 1.5)
        for i in range(9):
            grooves.append(common.side_profile(f"g{side}_{i}", _slot(8.2 + i * 3.25, 13.2, 20.5, 1.5, -0.14),
                                               0, right0=min(r_in, r_out), right1=max(r_in, r_out)))
    _cut(slide, _join("Slide_cut_serrations", grooves), "Slide", "Cocking serrations")

    _finish(slide, bevel=0.45, segments=3)

    # left-side marking, engraved after the bevel so the letters keep crisp edges
    eng = _engraving_cutter()
    _after_bevel(slide, _cut(slide, eng, None, "Engraving 17 Gen4 / AUSTRIA 9x19"))
    return slide


# ----------------------------------------------------------------------------- barrel

def build_barrel():
    # chamber section (14.4 mm), filleted shoulder, tube to the muzzle (13.5 mm, 114 mm overall)
    p = common.Path2D((BARREL_X0, 7.2), 5)
    p.line_to(98.0, 7.2).quad_to(99.0, 7.2, 99.6, 7.02)
    p.line_to(100.2, 6.9).quad_to(100.8, 6.75, 101.8, 6.75)
    p.line_to(BARREL_X1, 6.75)
    barrel = _lathe_x("Barrel", p.points(), BORE_UP, segments=96)
    common.assign(barrel, "Barrel", "Cavity")
    common.link(barrel)
    common.part_info(barrel, "Lauf", "114 mm, 13,5 mm an der Mündung, Polygonprofil (Sechskant, "
                                     "Drall 1:250 mm), Laufhaube im Auswurffenster")

    # rectangular hood on the chamber, its top just under the slide top (27.7 vs 28). 15 mm
    # wide, so it holds the R7.2 chamber with 0.3 mm to spare (flat, clean hood sides); its
    # left side and its bottom (15.0, below the port floor) disappear in the slide.
    # (an additive operand, so it is named "_add_" although it sits in the Cutters collection)
    hood = common.side_profile("Barrel_add_hood", common.round_rect(72, 15.0, 97, 27.7, 1.0, 4), 15.0)
    _cut(barrel, hood, "Barrel", "Hood", operation="UNION")

    # polygonal bore, about 8.9 mm with six soft lands 0.09 mm high (hexagonal profile),
    # right-hand twist 1:250 mm; blind at x 110 (dark end cap), nothing of the chamber is modelled
    bore = _twisted_prism("Barrel_cut_bore", 110, BARREL_X1 + 4, 4.47, 48, BORE_UP, x_ref=BARREL_X1,
                          lobe=0.09, lobes=6)
    common.cutter(bore)
    common.assign(bore, "Barrel", "Cavity")
    for poly in bore.data.polygons:                 # rear cap of the blind bore
        if poly.center.x < 110.01:
            poly.material_index = 1
    common.boolean(barrel, bore, name="Polygonal bore")

    # chamfered (about 45 degree) muzzle crown
    crown = common.cylinder_x("Barrel_cut_crown", BARREL_X1 - 1.6, BARREL_X1 + 0.6, 4.2, up=BORE_UP,
                              segments=96, r1=6.4)
    _cut(barrel, crown, "Barrel", "Muzzle crown")

    _finish(barrel, bevel=0.3, segments=3)
    return barrel


# ----------------------------------------------------------------------------- sights

def build_sights():
    # rear sight: rear face 9.5 mm from the slide's rear, 6.5 mm above the slide top
    rear = common.side_profile("RearSight", [(9.5, 27.5), (20.5, 27.5), (17.7, 34.5), (9.5, 34.5)], 13)
    common.assign(rear, "Polymer")
    common.link(rear)
    common.part_info(rear, "Kimme", "Hintere Visierung mit weißer U-Markierung")

    # drafted sides: 13 mm at the base, about 11.6 mm at the top
    drafts = _join("RearSight_cut_drafts", [
        common.cross_section("d_r", [(6.65, 26), (9, 26), (9, 36), (5.65, 36)], 0, 30),
        common.cross_section("d_l", [(-6.65, 26), (-5.65, 36), (-9, 36), (-9, 26)], 0, 30),
    ])
    _cut(rear, drafts, "Polymer", "Side drafts")

    # U-notch 3.4 mm wide, bottom 3.1 mm below the top, rounded bottom corners
    hw, bot, rb = 1.7, 31.4, 0.9
    u = common.Path2D((-hw, 40), 6)
    u.line_to(-hw, bot + rb).quad_to(-hw, bot, -hw + rb, bot)
    u.line_to(hw - rb, bot).quad_to(hw, bot, hw, bot + rb).line_to(hw, 40)
    notch = common.cross_section("RearSight_cut_notch", u.points(), 0, 30)
    _cut(rear, notch, "Polymer", "U notch")
    _finish(rear, bevel=0.3, segments=2)

    # white U outline, 0.7 mm wide, 0.25 mm around the notch, set into the rear face
    oh, ob, orr = hw + 0.95, bot - 0.95, rb + 0.95
    ih, ib, ir = hw + 0.25, bot - 0.25, rb + 0.25
    top = 34.2
    band = common.Path2D((-oh, top), 6)
    band.line_to(-oh, ob + orr).quad_to(-oh, ob, -oh + orr, ob)
    band.line_to(oh - orr, ob).quad_to(oh, ob, oh, ob + orr).line_to(oh, top)
    band.line_to(ih, top).line_to(ih, ib + ir).quad_to(ih, ib, ih - ir, ib)
    band.line_to(-ih + ir, ib).quad_to(-ih, ib, -ih, ib + ir).line_to(-ih, top)
    paint = common.cross_section("RearSight_Paint", band.points(), 9.47, 9.85)
    common.assign(paint, "Paint")
    common.link(paint, parent=rear)
    common.part_info(paint, "Kimme – Markierung", "Weiße U-Markierung, in die Rückseite eingelegt")
    _finish(paint, bevel=0)

    # front sight: rear face at 174.5 mm (165 mm sight radius), 6.1 mm above the slide top
    front = common.side_profile("FrontSight", [(174.5, 27.5), (180.5, 27.5), (178.6, 34.1), (174.5, 34.1)], 3.3)
    common.assign(front, "Polymer")
    common.link(front)
    common.part_info(front, "Korn", "Vordere Visierung mit weißem Punkt, Visierlinie 165 mm")
    _finish(front, bevel=0.22, segments=2)

    dot = common.cylinder_x("FrontSight_Dot", 174.47, 174.85, 1.0, up=31.6, segments=40)
    common.assign(dot, "Paint")
    common.link(dot, parent=front)
    common.part_info(dot, "Korn – Punkt", "Weißer Punkt, in die Rückseite des Korns eingelegt")
    _finish(dot, bevel=0.05, segments=1)
    return rear, front


# ----------------------------------------------------------------------------- small details

def build_details():
    # extractor: steel strip in its pocket on the right side, 0.15 mm proud of the slide
    ex = common.side_profile("Extractor", common.round_rect(55.6, 11.0, 75.4, 14.8, 0.9, 6), 0,
                             right0=HALF_W - 0.85, right1=HALF_W + 0.15)
    common.assign(ex, "Barrel")
    common.link(ex)
    common.part_info(ex, "Auszieher", "Zieht die Hülse aus dem Lager, zeigt eine geladene Kammer an")
    _finish(ex, bevel=0.15, segments=2)

    # rear cover plate in its seat, 0.1 mm below the slide's rear face
    plate = common.cross_section("CoverPlate", common.round_rect(-6.5, 7.0, 6.5, 21.0, 1.2), 0.1, 1.4)
    common.assign(plate, "Polymer")
    common.link(plate)
    common.part_info(plate, "Abdeckplatte", "Schließt die Rückseite des Schlittens")
    _finish(plate, bevel=0.2, segments=2)

    # front end of the recoil-spring guide rod, seen in the guide-rod hole (polymer cap, steel tip)
    rod = _lathe_x("GuideRod", [(176, 2.2), (183.5, 2.2), (183.5, 1.25), (184.2, 1.25), (184.4, 1.05)],
                   ROD_UP, segments=40)
    common.assign(rod, "Polymer", "Barrel")       # tip: dark nitrided steel
    _material_by_x(rod, 183.51, 0, 1)
    common.link(rod)
    common.part_info(rod, "Führungsstange", "Doppelte Schließfeder unter dem Lauf")
    _finish(rod, bevel=0.12, segments=2)
    return ex, plate, rod


def build():
    build_slide()
    build_barrel()
    build_sights()
    build_details()
