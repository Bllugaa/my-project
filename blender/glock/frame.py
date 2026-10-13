"""Frame group: frame/dust cover, rail, trigger guard, grip, magazine, trigger, controls, pins.

Contract: build() creates the objects with the glock.common helpers, links/parents them
to the root empty (common.link), assigns materials by name (common.assign) and returns None.

Port of buildFrame() in ../../index.html (same model coordinates in mm: x towards the
muzzle, up, right). Exterior-only display shells: nothing here fits or functions together,
hidden parts simply overlap inside the frame.

Beyond the three.js version:
  * curved parts are built as "pillow" extrusions (see pillow()): the side outline stays
    exactly as in index.html at the mid-plane, the edges are rounded with real quarter
    circles, and tight bends or thin sections (beavertail tang) automatically get a
    smaller rounding so the offset never folds over;
  * the grip has 3 mm rounded edges, slightly crowned front/back straps, a 0.8 mm palm
    swell on both sides (max width 30.6 mm), flush with the frame side under the slide and
    flaring out below, a tapered beavertail tang, Gen 4 finger grooves, smooth pockets for
    the magazine catch and shallow thumb rests; its side faces are finely triangulated so
    the swell is smooth. Its object space stays in model mm with the origin at the
    model origin (look.py textures it in Object coordinates);
  * every part ends with common.finish (bevel -> smooth by angle -> weighted normals).
"""

import math

import bmesh
import bpy
import numpy as np
from mathutils import Vector, geometry

from . import common as C
from .common import Path2D, bump, v

TRIGGER_DX = -3.5        # places the trigger face 72 mm in front of the backstrap
GRIP_HW = 14.5           # grip half width without the palm swell
GRIP_SWELL = 0.8         # palm swell per side -> max grip width 30.6 mm
GRIP_R = 3.0             # rounding of the grip edges
MAG_RELEASE = (39.4, -26.5, 49.0, -19.5)    # magazine catch outline (x0, up0, x1, up1)
MAG_POCKET = (38.2, -27.7, 50.2, -18.3)     # its pocket in the grip, both sides
PIN_TRIGGER = (72.0, -5.0)                  # trigger pin (x, up), pivot of the slide stop

PARTS = {
    "Frame": ("Rahmen", "Polymer-Griffstück mit Staubschutz vor dem Abzugsbügel"),
    "Rail": ("Zubehörschiene", "Montageschiene für Lampe oder Laser, eine Quernut"),
    "RailPlate": ("Rahmenplatte", "Glatte Metallplatte unter der Zubehörschiene"),
    "TriggerGuard": ("Abzugsbügel", "Eckiger Bügel mit geriffelter Front"),
    "Grip": ("Griff", "Gen-4-Oberfläche mit Fingermulden, Biberschwanz und Daumenauflage"),
    "Magazine": ("Magazin", "Bodenplatte und Front des 17-Schuss-Magazins"),
    "Trigger": ("Abzug", "Safe-Action-Abzug mit Sicherungszunge"),
    "MagRelease": ("Magazinhalter", "Vergrößerte, umsteckbare Taste"),
    "SlideStop": ("Schlittenfanghebel", "Hält den Schlitten nach dem letzten Schuss offen"),
    "Takedown": ("Zerlegehebel", "Zum Abnehmen des Schlittens, beidseitig geriffelt"),
    "Pins": ("Haltestifte", "Drei Stifte halten Abzugsgruppe und Verriegelung"),
}


# --------------------------------------------------------------------------- small math helpers

def _ss(e0, e1, x):
    """smoothstep from e0 to e1 (e0 > e1 gives a falling step)."""
    t = (x - e0) / (e1 - e0)
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    return t * t * (3 - 2 * t)


def _ccw(pts):
    a = 0.0
    for i, (x0, y0) in enumerate(pts):
        x1, y1 = pts[(i + 1) % len(pts)]
        a += x0 * y1 - x1 * y0
    return list(pts) if a > 0 else list(reversed(pts))


def _resample(pts, max_len):
    out = []
    n = len(pts)
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        out.append(a)
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        k = int(length // max_len)
        for j in range(1, k + 1):
            t = j / (k + 1)
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return out


def _normals(P, window=1.0):
    """Inward unit normals of a CCW polygon, averaged over +-window mm of arc.

    Straight runs and smooth curves get their exact normal; kinks are spread over a few
    vertices, so the inset outline (the border of the side faces) gets rounded joins.
    """
    edge = np.roll(P, -1, axis=0) - P
    length = np.linalg.norm(edge, axis=1)
    en = np.stack([-edge[:, 1], edge[:, 0]], axis=1) / length[:, None]
    arc = np.concatenate([[0.0], np.cumsum(length)[:-1]])
    mid = arc + length / 2
    d = np.abs(arc[:, None] - mid[None, :])
    d = np.minimum(d, length.sum() - d)
    w = np.exp(-(d / window) ** 2) * length[None, :] + 1e-9
    nrm = w @ en
    return nrm / np.linalg.norm(nrm, axis=1)[:, None]


def _inset_limits(P, m, radius, steps=16, ratio=0.8, slope=0.25):
    """Per-vertex maximum inset so the offset outline never folds over.

    A vertex may move inwards by s only while it stays s away from the whole outline;
    that catches tight convex bends (radius < s) as well as thin parts such as the
    beavertail tang, where the offsets from opposite sides would cross. Moderate
    kinks (up to ~50 degrees) pass. The result is made Lipschitz along the outline (slope)
    so the rounding changes gradually.
    """
    n = len(P)
    fail = np.full(n, np.inf)
    edge = np.roll(P, -1, axis=0) - P
    for k in range(1, steps + 1):
        s = radius * 1.25 * k / steps
        Q = P + s * m
        bad = _dist(Q, P) < 0.9 * s
        # an offset edge that shrinks to under 10 % (or flips) marks both its ends
        flip = np.sum((np.roll(Q, -1, axis=0) - Q) * edge, axis=1) < 0.1 * np.sum(edge * edge, axis=1)
        bad |= flip | np.roll(flip, 1)
        fail[bad & np.isinf(fail)] = s
    lim0 = np.minimum(radius, ratio * fail)
    seg = np.linalg.norm(np.roll(P, -1, axis=0) - P, axis=1)
    arc = np.concatenate([[0.0], np.cumsum(seg)[:-1]])
    da = np.abs(arc[:, None] - arc[None, :])
    da = np.minimum(da, seg.sum() - da)
    return (lim0[None, :] + slope * da).min(axis=1)


def _inside(pts, poly):
    x, y = pts[:, 0][:, None], pts[:, 1][:, None]
    x1, y1 = poly[:, 0][None, :], poly[:, 1][None, :]
    x2, y2 = np.roll(poly[:, 0], -1)[None, :], np.roll(poly[:, 1], -1)[None, :]
    with np.errstate(divide="ignore", invalid="ignore"):
        xc = (x2 - x1) * (y - y1) / (y2 - y1) + x1
    hit = ((y1 > y) != (y2 > y)) & (x < xc)
    return hit.sum(axis=1) % 2 == 1


def _dist(pts, poly):
    a = poly
    ab = np.roll(poly, -1, axis=0) - a
    ap = pts[:, None, :] - a[None, :, :]
    t = np.clip(np.sum(ap * ab[None], axis=2) / np.sum(ab * ab, axis=1)[None, :], 0, 1)
    c = a[None] + t[..., None] * ab[None]
    return np.linalg.norm(pts[:, None, :] - c, axis=2).min(axis=1)


# --------------------------------------------------------------------------- mesh builders

def pillow(name, pts, width, radius, segments=4, centre=0.0, half_width=None, offset=None,
           grid=None, crown=0.0, max_len=0.6):
    """Extrude a side outline [(x, up), ...] across the width with rounded (quarter-circle) edges.

    Like THREE.ExtrudeGeometry with bevelOffset = -bevel: at the mid-plane the outline is
    exactly `pts`, the side faces are inset by `radius`. Tight convex bends and thin
    sections get a smaller inset automatically, so the rounding never folds over.

    half_width(x, up) -> half width (default width/2), lets the width vary smoothly.
    crown: the edge faces (straps) curve back by this much from the mid-plane to where the
    rounding starts, so they are slightly convex across the width; the mid-plane outline
    is unchanged.
    offset(x, up, t) -> extra outward offset of the side faces; t is a smooth dome over the
    side face (0 on its border, 1 at its highest point: a membrane under even pressure, so
    it has no creases). Needs `grid` (spacing in mm) so the side faces get inner vertices.
    """
    pts = _ccw(_resample(pts, max_len))
    P = np.array(pts, dtype=float)
    n = len(P)
    m = _normals(P)
    hw_fn = half_width or (lambda x, up: width / 2)
    hw_min = min(hw_fn(x, up) for x, up in pts)
    R = min(radius, hw_min * 0.95)
    total = _inset_limits(P, m, R + crown)       # inset budget for crown + rounding
    lim = total * R / (R + crown)
    cr = total * crown / (R + crown)
    crown_steps = 4 if crown > 0 else 1

    # stations across the edge: (inset, right) per outline vertex, mid-plane first
    stations = []
    for j in range(1, crown_steps + 1):
        u = j / crown_steps
        stations.append((cr * u * u, lambda hw, u=u: (hw - R) * u))
    for k in range(1, segments + 1):
        th = 0.5 * math.pi * k / segments
        stations.append((cr + lim * (1 - math.cos(th)), lambda hw, th=th: hw - R + R * math.sin(th)))

    bm = bmesh.new()
    mid = [bm.verts.new(v(x, up, centre)) for x, up in P]
    rings = {}
    for side in (1, -1):
        rings[side] = [mid]
        for inset, right in stations:
            q = P + inset[:, None] * m
            rings[side].append([bm.verts.new(v(x, up, centre + side * right(hw_fn(x, up)))) for x, up in q])
    cap_xy = q
    for side in (1, -1):
        rs = rings[side]
        for i in range(n):
            j = (i + 1) % n
            for k in range(len(rs) - 1):
                a, b = rs[k], rs[k + 1]
                bm.faces.new((a[i], a[j], b[j], b[i]))
        if grid:
            _grid_cap(bm, rs[-1], cap_xy, side, centre, grid, hw_fn, offset)
        else:
            bm.faces.new(rs[-1])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    return bpy.data.objects.new(name, me)


def _grid_cap(bm, border, border_xy, side, centre, spacing, hw_fn, offset):
    """Fill a side face with a constrained Delaunay triangulation of a staggered point grid."""
    B = np.asarray(border_xy, dtype=float)
    n = len(B)
    lo, hi = B.min(axis=0), B.max(axis=0)
    dy = spacing * math.sqrt(3) / 2
    pts = []
    for row, y in enumerate(np.arange(lo[1] + dy / 2, hi[1], dy)):
        xs = np.arange(lo[0] + (spacing / 2 if row % 2 else 0), hi[0], spacing)
        pts.extend((x, y) for x in xs)
    G = np.array(pts)
    G = G[_inside(G, B)]
    G = G[_dist(G, B) > 0.55 * spacing]
    coords = [Vector(p) for p in B] + [Vector(p) for p in G]
    out_v, _e, out_f, orig_v, _oe, _of = geometry.delaunay_2d_cdt(coords, [], [list(range(n))], 1, 1e-6, True)
    if len(out_v) != len(coords):
        print(f"frame.pillow: side-face triangulation changed the vertex count ({len(coords)} -> {len(out_v)})")
    xy = np.array([(co.x, co.y) for co in out_v])
    border_of = [next((i for i in orig_v[o] if i < n), None) for o in range(len(out_v))]
    fixed = np.array([b is not None for b in border_of])
    t = _membrane(xy, np.array(out_f), fixed) if offset else np.zeros(len(xy))
    verts = []
    for o, (x, up) in enumerate(xy):
        if fixed[o]:
            verts.append(border[border_of[o]])
            continue
        r = hw_fn(x, up) + (offset(x, up, t[o]) if offset else 0.0)
        verts.append(bm.verts.new(v(x, up, centre + side * r)))
    for f in out_f:
        bm.faces.new([verts[i] for i in f])


def _membrane(xy, tris, fixed, iterations=800):
    """Solve -Laplace(u) = 1 with u = 0 on `fixed` vertices (cotangent weights, CG); max -> 1."""
    a, b, c = xy[tris[:, 0]], xy[tris[:, 1]], xy[tris[:, 2]]
    I, J, W = [], [], []
    for (i, j, k), (p, q, r) in (((0, 1, 2), (a, b, c)), ((1, 2, 0), (b, c, a)), ((2, 0, 1), (c, a, b))):
        e1, e2 = p - r, q - r                    # angle at r faces the edge p-q
        cross = np.abs(e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0])
        I.append(tris[:, i])
        J.append(tris[:, j])
        W.append(0.5 * np.sum(e1 * e2, axis=1) / np.maximum(cross, 1e-12))
    I, J, W = np.concatenate(I), np.concatenate(J), np.maximum(np.concatenate(W), 0.0)
    N = len(xy)
    area = np.abs((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])) / 6
    rhs = np.bincount(tris.ravel(), weights=np.repeat(area, 3), minlength=N)
    free = ~fixed

    def apply(u):
        d = W * (u[I] - u[J])
        out = np.bincount(I, weights=d, minlength=N) - np.bincount(J, weights=d, minlength=N)
        return out * free

    rhs = rhs * free
    u = np.zeros(N)
    r = rhs.copy()
    p = r.copy()
    rr = r @ r
    for _ in range(iterations):
        ap = apply(p)
        alpha = rr / (p @ ap)
        u += alpha * p
        r -= alpha * ap
        rr_new = r @ r
        if rr_new < 1e-20 * (rhs @ rhs):
            break
        p = r + (rr_new / rr) * p
        rr = rr_new
    return u / u.max()


def join(name, objs, slots=None):
    """Merge objects into one mesh; slots[i] is the material index for objs[i]'s faces."""
    bm = bmesh.new()
    for k, o in enumerate(objs):
        n0 = len(bm.faces)
        bm.from_mesh(o.data)
        bm.faces.ensure_lookup_table()
        idx = slots[k] if slots else 0
        for i in range(n0, len(bm.faces)):
            bm.faces[i].material_index = idx
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    for o in objs:
        data = o.data
        bpy.data.objects.remove(o)
        if data.users == 0:
            bpy.data.meshes.remove(data)
    return bpy.data.objects.new(name, me)


def _move_before_bevel(obj):
    """Move obj's last modifier in front of its Bevel (for booleans added after finish())."""
    names = [m.name for m in obj.modifiers]
    if "Bevel" in names:
        obj.modifiers.move(len(names) - 1, names.index("Bevel"))


def rot_rect(cx, cu, half_a, half_b, angle):
    """Rectangle in the side plane, centred on (cx, cu), half sizes along/across `angle`."""
    ca, sa = math.cos(angle), math.sin(angle)
    return [(cx + a * ca - b * sa, cu + a * sa + b * ca)
            for a, b in ((-half_a, -half_b), (half_a, -half_b), (half_a, half_b), (-half_a, half_b))]


def part(obj, key, *materials):
    C.link(obj)
    # C.assign clears the material slots, which also resets every face's material index;
    # keep the per-face slots set by join(..., slots=...) (e.g. the magazine body)
    idx = [p.material_index for p in obj.data.polygons]
    C.assign(obj, *materials)
    obj.data.polygons.foreach_set("material_index", idx)
    obj.data.update()
    C.part_info(obj, *PARTS[key])
    return obj


def surface_right(obj, x, up, side):
    """|right| where a ray from outside the given side hits obj's evaluated surface at (x, up)."""
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    ok, loc, _n, _i = obj.ray_cast(v(x, up, side * 60.0), Vector((0, side, 0)), depsgraph=dg)
    return abs(loc.y) if ok else None


# --------------------------------------------------------------------------- the grip

def grip_half_width(x, up):
    """Half width of the grip: from up = -8 upwards just inside the frame side (11.9 vs 12.0),
    so the frame side runs on flush and the grip only widens below (the flare sits low, mostly
    in the textured zone, so it does not catch the overhead light as a band); narrower on
    the tang."""
    top = _ss(-16, -8, up)
    tang = _ss(-4, -15, x) * _ss(-24, -8, up)
    return GRIP_HW - 2.6 * top - 1.6 * tang


def grip_offset(x, up, t):
    """Palm swell (convex sides) and shallow thumb rests; t is the side face's dome (0 at the border)."""
    swell = GRIP_SWELL * _ss(0, 1, t) * (1 - 0.85 * _ss(-16, -8, up))
    thumb = 0.4 * math.exp(-((x - 36) / 6.5) ** 2 - ((up + 8.5) / 3.2) ** 2) * _ss(0, 0.25, t)
    return swell - thumb


def grip_outline():
    """Beavertail, backstrap with palm swell, front strap with Gen 4 finger grooves."""
    gr = Path2D((-10.5, -1.5), curve_segments=24)
    gr.line_to(34, -1.5).quad_to(51.5, -1.5, 51.5, -15)
    gr.line_to(51.5, -30).quad_to(51.5, -38.5, 47, -41)
    # Gen 4 finger grooves: two rounded ridges (about 3 mm proud) between three grooves
    gr.strap((47, -41), (27, -94), +1,
             lambda t: (2.4 * bump(t, 0.345, 0.05) + 2.4 * bump(t, 0.655, 0.05)
                        - 0.8 * (bump(t, 0.17, 0.08) + bump(t, 0.5, 0.08) + bump(t, 0.83, 0.08))
                        + 0.6 * math.sin(math.pi * t)), n=120)
    gr.quad_to(26, -96.5, 22.5, -96.4)
    gr.line_to(-11.8, -93.6).quad_to(-15.7, -93.4, -15.5, -90.5)
    gr.strap((-15.5, -90.5), (0, -24), -1, lambda t: 2.4 * math.sin(math.pi * t) ** 1.4, n=72)
    # beavertail: the backstrap sweeps into the tang's underside in one concave curve; the
    # tang tapers from about 10 mm at the root to 5-6 mm at a rounded tip
    gr.quad_to(2.0, -14.5, -5, -11.2).quad_to(-11, -9.0, -14.6, -7.0)
    gr.quad_to(-16.6, -5.6, -15.8, -3.2).quad_to(-15.0, -1.5, -12, -1.5)
    return gr.points()


# --------------------------------------------------------------------------- build

def build():
    # ---------------------------------------------------------------- frame / dust cover
    # As index.html, but the rear end reaches 0.6 mm behind the slide's rear face below the
    # slide (and stays 0.3 mm inside it above up = 0, no coplanar faces), so no slot opens
    # between slide, frame and beavertail. The step from the dust cover (up -13) down to the
    # grip section (up -5) is a diagonal (50, -13) -> (44, -5) that stays >= 1.4 mm inside the
    # grip's rounded front-top shoulder; index.html's vertical step at x = 48 left a small
    # dark pocket between the frame's underside and that shoulder.
    frame = C.side_profile("Frame", [
        (0.3, 2), (176, 2), (176, -8.5), (111, -8.5), (108, -13), (50, -13), (44, -5),
        (-0.6, -5), (-0.6, -0.3), (0.3, -0.3)], 24)
    part(frame, "Frame", "Frame")
    C.finish(frame, bevel=1.0, segments=4)

    # ---------------------------------------------------------------- accessory rail
    # cross-section with the two side grooves; its top reaches into the dust cover so the
    # frame's rounded lower edge runs into the rail without a seam
    rail = C.cross_section("Rail", [
        (-11.8, -6), (11.8, -6), (11.8, -10.6), (9.6, -11.2), (9.6, -12.4), (10.9, -13),
        (10.9, -14.6), (10.4, -15.2), (-10.4, -15.2), (-10.9, -14.6), (-10.9, -13),
        (-9.6, -12.4), (-9.6, -11.2), (-11.8, -10.6)], 111, 175.6)
    part(rail, "Rail", "Frame")
    slot = C.cutter(C.box("RailSlot", 149, 154, -16, -11.6, -14, 14), "Frame")
    C.boolean(rail, slot)
    C.finish(rail, bevel=0.35, segments=3)

    plate = C.box("RailPlate", 116, 129, -15.5, -15.1, -5.5, 5.5)
    part(plate, "RailPlate", "Steel")
    C.finish(plate, bevel=0.12, segments=2)

    # ---------------------------------------------------------------- trigger guard
    # U profile ending inside the frame; the hidden top edges are raised from up = -12 to
    # -8.5 so they meet the sloped frame bottom. The bar's underside runs into the front
    # strap with a concave fillet (tangent to the strap at up -47.5, as on the real frame);
    # the rear end is buried in the grip. The inner rear face stays >= 0.5 mm inside the
    # receding front strap down to up -35.5 (no coplanar faces, no lip).
    tg = Path2D((113.5 + 3.5 * 4 / 24, -8.5), curve_segments=16)
    tg.line_to(109.5, -36).quad_to(108.5, -42, 102, -42)
    tg.line_to(58, -42).quad_to(46.7, -42.2, 44.0, -47.5, segments=20)
    tg.line_to(40, -49).line_to(40, -35).line_to(44, -8.5).line_to(50.3, -8.5)
    tg.line_to(50.3, -31).quad_to(50.3, -35.5, 55.5, -35.5)
    tg.line_to(98.5, -35.5).quad_to(102.5, -35.5, 102.2, -31.5)
    tg.line_to(104.5 + 3.5 * 2.3 / 19.5, -8.5)
    guard = pillow("TriggerGuard", tg.points(), 16, 2.2, segments=5)
    part(guard, "TriggerGuard", "Frame")
    # six horizontal grooves across the squared front face
    face_angle = math.atan2(-4, 24)
    grooves = []
    for i in range(6):
        u = -17 - i * 2.7
        grooves.append(C.side_profile(f"GuardGroove{i}", rot_rect(113.5 + (u + 12) * (4 / 24), u, 1.0, 0.45, face_angle), 24))
    C.boolean(guard, C.cutter(join("GuardGrooves", grooves), "Frame"))
    C.finish(guard, bevel=0.2, segments=2)

    # ---------------------------------------------------------------- grip
    grip = pillow("Grip", grip_outline(), 2 * GRIP_HW, GRIP_R, segments=8,
                  half_width=grip_half_width, offset=grip_offset, grid=1.25, crown=0.6)
    part(grip, "Grip", "Grip")
    # scallop at the front of the magazine well, showing the magazine behind it
    scallop = C.cutter(C.cylinder_x("GripScallop", 21, 40, 6, up=-96.5, segments=64), "Grip")
    C.boolean(grip, scallop)
    # smooth pockets for the magazine catch, about 1 mm deep, on both sides
    pocket = C.round_rect(*MAG_POCKET, 2.4, segments=8)
    pockets = join("MagReleasePocket", [C.side_profile("MRP_l", pocket, 0, right0=-20, right1=-13.6),
                                        C.side_profile("MRP_r", pocket, 0, right0=13.6, right1=20)])
    C.boolean(grip, C.cutter(pockets, "Grip"), name="Bool mag catch pockets")
    C.finish(grip, bevel=0.5, segments=3)

    # ---------------------------------------------------------------- magazine
    mb = Path2D((-12, -92.8), curve_segments=16)
    mb.line_to(24, -95.8).quad_to(28, -96, 28, -98.5)
    mb.line_to(27.5, -101.5).quad_to(27, -103.6, 24, -103.5)
    mb.line_to(-11, -100.6).quad_to(-14.5, -100.4, -14.5, -97.5)
    mb.quad_to(-14.5, -92.9, -12, -92.8)
    floor_plate = pillow("MagFloorPlate", mb.points(), 25.5, 1.8, segments=4)
    body = pillow("MagBody", [(19, -96), (23.8, -96), (26.4, -86), (21.6, -86)], 22, 0.8, segments=3)
    magazine = join("Magazine", [floor_plate, body], slots=[0, 1])
    part(magazine, "Magazine", "Polymer", "Magazine")
    C.finish(magazine, bevel=0.3, segments=2)

    # ---------------------------------------------------------------- trigger with safety blade
    dx = TRIGGER_DX
    tr = Path2D((71.5 + dx, -11), curve_segments=20)
    tr.quad_to(65 + dx, -24, 72.5 + dx, -32.5).quad_to(75.2 + dx, -33.4, 76 + dx, -31)
    tr.quad_to(70.5 + dx, -23.5, 77.5 + dx, -11)
    blade = Path2D((75.0 + dx, -15.5), curve_segments=20)
    blade.quad_to(71.5 + dx, -22.5, 74.4 + dx, -29.2).line_to(76.4 + dx, -29.2)
    blade.quad_to(73.9 + dx, -22.5, 77.2 + dx, -15.5)
    trigger = join("Trigger", [pillow("TriggerShoe", tr.points(), 6.5, 1.2, segments=4),
                               pillow("TriggerBlade", blade.points(), 2.2, 0.5, segments=3)])
    part(trigger, "Trigger", "Polymer")
    C.finish(trigger, bevel=0.2, segments=2)

    # ---------------------------------------------------------------- controls
    # enlarged, reversible magazine catch: a horizontal button with a ribbed face on the
    # left (outer face at right = -16.2), set in a smooth pocket about 1 mm deep; the
    # catch's other end shows as a smaller, nearly flush stub in the matching right pocket
    x0, up0, x1, up1 = MAG_RELEASE
    rel_outline = C.round_rect(x0, up0, x1, up1, 1.6, segments=8)
    release = join("MagRelease", [
        pillow("MagReleaseButton", rel_outline, 16.2 - 13.4, 0.6, segments=3, centre=-(16.2 + 13.4) / 2),
        pillow("MagReleaseStub", C.round_rect(x0 + 0.9, up0 + 0.9, x1 - 0.9, up1 - 0.9, 1.0, segments=8),
               0.8, 0.3, segments=2, centre=(13.1 + 13.9) / 2),
    ])
    part(release, "MagRelease", "Polymer")
    ribs = [C.box(f"MagReleaseRib{i}", x0 + 1.875 + i * 1.75, x0 + 2.475 + i * 1.75, up0 + 1.0, up1 - 1.0, -16.6, -15.9)
            for i in range(4)]
    C.boolean(release, C.cutter(join("MagReleaseRibs", ribs), "Polymer"), name="Bool ribs")
    C.finish(release, bevel=0.15, segments=3)

    # slide stop lever: pivots on the trigger pin (round front end), thumb paddle at the
    # rear just above the front of the grip; the slide's stop notch sits one lock-back
    # travel ahead of the paddle
    ss = Path2D((72, -2.4), curve_segments=16)
    ss.line_to(57, -2.2).quad_to(52.5, -1.6, 52.5, -4.4).quad_to(53, -6.6, 57, -6.4)
    ss.line_to(69.56, -5.89)
    for i in range(1, 25):                       # pivot boss around the trigger pin
        a = math.radians(200 + 250 * i / 24)
        ss.line_to(PIN_TRIGGER[0] + 2.6 * math.cos(a), PIN_TRIGGER[1] + 2.6 * math.sin(a))
    stop = pillow("SlideStop", ss.points(), 1.9, 0.35, segments=2, centre=-12.25)
    part(stop, "SlideStop", "Slide")
    C.finish(stop, bevel=0.12, segments=3)

    # takedown lever: one tab per side, about 1 mm proud in a shallow frame recess,
    # six shallow vertical ribs (0.3 mm, they do not cut through the tab)
    tx0, tx1 = 89.5, 97.5
    recess = [C.box(f"TakedownRecess{s}", tx0 - 0.7, tx1 + 0.7, -5.2, 0.5, *sorted((s * 11.4, s * 13.0)))
              for s in (-1, 1)]
    C.boolean(frame, C.cutter(join("TakedownRecess", recess), "Frame"), name="Bool takedown recess")
    _move_before_bevel(frame)
    tabs = [C.box(f"TakedownTab{s}", tx0, tx1, -4.6, -1.1, *sorted((s * 11.2, s * 13.0))) for s in (-1, 1)]
    takedown = join("Takedown", tabs)
    part(takedown, "Takedown", "Slide")
    tab_grooves = [C.box(f"TakedownGroove{s}{i}", tx0 + 0.5 + i * 1.3, tx0 + 1.0 + i * 1.3, -5, 0,
                         *sorted((s * 12.7, s * 13.3)))
                   for s in (-1, 1) for i in range(6)]
    C.boolean(takedown, C.cutter(join("TakedownGrooves", tab_grooves), "Slide"))
    C.finish(takedown, bevel=0.12, segments=3)

    # ---------------------------------------------------------------- pins
    # the rear (trigger housing) pin ends 0.1 mm proud of the grip's curved surface
    # (highest point under the pin's end face)
    rear_r = []
    for s in (1, -1):
        hits = [surface_right(grip, 8 + 1.5 * math.cos(a), -7 + 1.5 * math.sin(a), s)
                for a in (0, 1.57, 3.14, 4.71)] + [surface_right(grip, 8, -7, s)]
        hits = [h for h in hits if h]
        rear_r.append(max(hits) + 0.1 if hits else 15.45)
    # the trigger pin passes through the slide stop's pivot boss; its left end shows on the lever
    pins = join("Pins", [
        C.cylinder_across("PinTrigger", -13.3, 12.2, 1.5, *PIN_TRIGGER),
        C.cylinder_across("PinLockingBlock", -12.2, 12.2, 1.5, 100, -5.2),
        C.cylinder_across("PinHousing", -rear_r[1], rear_r[0], 1.5, 8, -7),
    ])
    part(pins, "Pins", "Steel")
    C.finish(pins, bevel=0.2, segments=2)
