"""Shared helpers for building the Glock 17 exterior model in Blender.

Scope: this is a visual, exterior-only display model. Internal mechanisms (fire-control
group, firing pin, locking block, frame rails, magazine internals) are deliberately not
modelled; parts are solid shells shaped from public outside dimensions.

Coordinates
-----------
All mesh data is in millimetres. Every part is parented to the root empty "Glock17",
which is scaled by 0.001, so the scene itself is in metres (the pistol is 0.202 m long).

Helpers take *model* coordinates, the same ones used in ../index.html:

    x        towards the muzzle; x = 0 is the rear face of the slide
    up       up; up = 0 is the bottom edge of the slide
    right    the pistol's right side (ejection-port side)

and map them to Blender axes as  X = x,  Y = -right,  Z = up.
So in Blender's Front view (numpad 1) you look at the right side with the muzzle pointing right.
"""

import math

import bmesh
import bpy
from mathutils import Matrix, Vector

ROOT_NAME = "Glock17"
CUTTERS = "Cutters"


def v(x, up, right=0.0):
    """Model coordinates (mm) -> Blender local vector (mm)."""
    return Vector((x, -right, up))


# --------------------------------------------------------------------------- scene structure

def collection(name, parent=None):
    col = bpy.data.collections.get(name)
    if col is None:
        col = bpy.data.collections.new(name)
        (parent or bpy.context.scene.collection).children.link(col)
    return col


def root():
    obj = bpy.data.objects.get(ROOT_NAME)
    if obj is None:
        obj = bpy.data.objects.new(ROOT_NAME, None)
        obj.empty_display_type = "PLAIN_AXES"
        obj.empty_display_size = 40
        obj.scale = (0.001, 0.001, 0.001)
        bpy.context.scene.collection.objects.link(obj)
    return obj


def cutter_collection():
    col = collection(CUTTERS)
    col.hide_render = True
    col.hide_viewport = False
    return col


def hide_cutters():
    """Hide the Cutters collection with its eye icon (saved in the file): one click in the
    outliner shows every cutter again. Booleans keep working while it is hidden."""
    lc = bpy.context.view_layer.layer_collection.children.get(CUTTERS)
    if lc is not None:
        lc.hide_viewport = True


def link(obj, col_name="Glock17 Parts", parent=None):
    """Link obj into a collection and parent it to the root empty (or `parent`)."""
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    collection(col_name).objects.link(obj)
    obj.parent = parent or root()
    obj.matrix_parent_inverse = Matrix.Identity(4)
    return obj


# --------------------------------------------------------------------------- materials

FALLBACK_COLORS = {
    "Slide": (0.035, 0.037, 0.040, 1),
    "SlideInner": (0.012, 0.012, 0.014, 1),
    "Barrel": (0.06, 0.062, 0.066, 1),
    "Frame": (0.022, 0.023, 0.025, 1),
    "Grip": (0.022, 0.023, 0.025, 1),
    "Polymer": (0.016, 0.017, 0.018, 1),
    "Magazine": (0.012, 0.013, 0.014, 1),
    "Steel": (0.35, 0.36, 0.38, 1),
    "Cavity": (0.003, 0.003, 0.003, 1),
    "Paint": (0.85, 0.83, 0.78, 1),
    "Engraving": (0.30, 0.31, 0.33, 1),
}
MATERIAL_NAMES = tuple(FALLBACK_COLORS)


def mat(name):
    """Get a material by name; creates a simple placeholder if look.py has not defined it yet."""
    m = bpy.data.materials.get(name)
    if m is None:
        m = bpy.data.materials.new(name)
        rgba = FALLBACK_COLORS.get(name, (0.5, 0.5, 0.5, 1))
        m.diffuse_color = rgba
        m.use_nodes = True
        bsdf = m.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            bsdf.inputs["Base Color"].default_value = rgba
            bsdf.inputs["Roughness"].default_value = 0.45
            if name in ("Slide", "Barrel", "Steel", "SlideInner"):
                bsdf.inputs["Metallic"].default_value = 0.85
    return m


def assign(obj, *names):
    """Replace obj's material slots with the given material names (slot order = names order)."""
    obj.data.materials.clear()
    for n in names:
        obj.data.materials.append(mat(n))
    return obj


# --------------------------------------------------------------------------- 2D paths

class Path2D:
    """Minimal 2D path like THREE.Shape: moveTo/lineTo/quadTo, sampled to a point list.

    Coordinates are (a, b) pairs; what a/b mean depends on the extrusion helper
    (side profiles use (x, up), cross-sections use (right, up)).
    """

    def __init__(self, start=None, curve_segments=18):
        self.pts = []
        self.seg = curve_segments
        if start is not None:
            self.move_to(*start)

    def move_to(self, a, b):
        self.pts = [(float(a), float(b))]
        return self

    def line_to(self, a, b):
        self.pts.append((float(a), float(b)))
        return self

    def quad_to(self, ca, cb, a, b, segments=None):
        a0, b0 = self.pts[-1]
        n = segments or self.seg
        for i in range(1, n + 1):
            t = i / n
            u = 1 - t
            self.pts.append((u * u * a0 + 2 * u * t * ca + t * t * a,
                             u * u * b0 + 2 * u * t * cb + t * t * b))
        return self

    def strap(self, a, b, outward_sign, offset, n=48):
        """Straight edge from point a to b, pushed outward (towards sign of first coord) by offset(t)."""
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        nx, ny = dy / length, -dx / length
        if (nx > 0) != (outward_sign > 0):
            nx, ny = -nx, -ny
        for i in range(1, n + 1):
            t = i / n
            o = offset(t)
            self.pts.append((a[0] + dx * t + nx * o, a[1] + dy * t + ny * o))
        return self

    def points(self):
        out = []
        for p in self.pts:
            if not out or (abs(p[0] - out[-1][0]) > 1e-6 or abs(p[1] - out[-1][1]) > 1e-6):
                out.append(p)
        if len(out) > 2 and abs(out[0][0] - out[-1][0]) < 1e-6 and abs(out[0][1] - out[-1][1]) < 1e-6:
            out.pop()
        return out


def round_rect(a0, b0, a1, b1, r, segments=6):
    p = Path2D((a0 + r, b0), segments)
    p.line_to(a1 - r, b0).quad_to(a1, b0, a1, b0 + r)
    p.line_to(a1, b1 - r).quad_to(a1, b1, a1 - r, b1)
    p.line_to(a0 + r, b1).quad_to(a0, b1, a0, b1 - r)
    p.line_to(a0, b0 + r).quad_to(a0, b0, a0 + r, b0)
    return p.points()


def bump(t, centre, width):
    return math.exp(-(((t - centre) / width) ** 2))


# --------------------------------------------------------------------------- mesh builders

def _prism_mesh(name, ring_a, ring_b):
    """Closed prism between two equally long vertex rings (lists of Vectors)."""
    bm = bmesh.new()
    va = [bm.verts.new(p) for p in ring_a]
    vb = [bm.verts.new(p) for p in ring_b]
    n = len(va)
    bm.faces.new(va)
    bm.faces.new(list(reversed(vb)))
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((va[i], va[j], vb[j], vb[i]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    return bpy.data.objects.new(name, me)


def side_profile(name, pts, width, centre_right=0.0, right0=None, right1=None):
    """Extrude a side-view polygon [(x, up), ...] across the width.

    Spans right = centre_right ± width/2, or explicitly right0..right1.
    """
    if right0 is None:
        right0, right1 = centre_right - width / 2, centre_right + width / 2
    ring_a = [v(x, u, right0) for x, u in pts]
    ring_b = [v(x, u, right1) for x, u in pts]
    return _prism_mesh(name, ring_a, ring_b)


def cross_section(name, pts, x0, x1):
    """Extrude a cross-section polygon [(right, up), ...] along the barrel axis from x0 to x1."""
    ring_a = [v(x0, u, r) for r, u in pts]
    ring_b = [v(x1, u, r) for r, u in pts]
    return _prism_mesh(name, ring_a, ring_b)


def box(name, x0, x1, up0, up1, right0, right1):
    pts = [(x0, up0), (x1, up0), (x1, up1), (x0, up1)]
    return side_profile(name, pts, 0, right0=right0, right1=right1)


def cylinder_x(name, x0, x1, r, up=0.0, right=0.0, segments=48, r1=None, rotate=0.0):
    """Cylinder (or cone when r1 is given: r at x0, r1 at x1) along the barrel axis."""
    r1 = r if r1 is None else r1
    ring = lambda x, rad: [v(x, up + rad * math.sin(rotate + 2 * math.pi * i / segments),
                             right + rad * math.cos(rotate + 2 * math.pi * i / segments))
                           for i in range(segments)]
    return _prism_mesh(name, ring(x0, r), ring(x1, r1))


def cylinder_across(name, right0, right1, r, x, up, segments=32):
    """Cylinder through the width (a pin), axis along `right`."""
    ring = lambda rr: [v(x + r * math.cos(2 * math.pi * i / segments),
                         up + r * math.sin(2 * math.pi * i / segments), rr) for i in range(segments)]
    return _prism_mesh(name, ring(right0), ring(right1))


def text_mesh(name, body, size, x, up, right, depth, facing="left"):
    """Text as a closed mesh slab (for engraving cutters or raised lettering).

    The text lies in the x/up plane and reads correctly when seen from the `facing` side
    ('left' = the pistol's left, 'right' = its right). `x, up` is where the first line's
    baseline starts as the viewer sees it: on the left side the text runs towards the
    rear (-x), on the right side towards the muzzle (+x). The slab is centred on `right`
    and `depth` thick, so with `right` on the surface it cuts depth/2 deep.
    """
    curve = bpy.data.curves.new(name + "_curve", "FONT")
    curve.body = body
    curve.size = size
    curve.extrude = depth / 2
    curve.resolution_u = 4
    curve.font = bpy.data.fonts.load("<builtin>", check_existing=True)
    tmp = bpy.data.objects.new(name + "_tmp", curve)
    bpy.context.scene.collection.objects.link(tmp)
    if facing == "left":
        # viewer at +Y: text X -> -X, text Y -> +Z, text normal -> +Y
        basis = Matrix(((-1, 0, 0, 0), (0, 0, 1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))
    else:
        # viewer at -Y: text X -> +X, text Y -> +Z, text normal -> -Y
        basis = Matrix(((1, 0, 0, 0), (0, 0, -1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))
    tmp.matrix_world = Matrix.Translation(v(x, up, right)) @ basis
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(tmp.evaluated_get(dg))
    me.transform(tmp.matrix_world)
    bpy.data.objects.remove(tmp)
    bpy.data.curves.remove(curve)
    me.name = name
    return bpy.data.objects.new(name, me)


# --------------------------------------------------------------------------- modifiers

def cutter(obj, material=None):
    """Turn obj into a boolean cutter (kept live so the model stays editable).

    Cutters are not hidden one by one: build_glock hides the whole "Cutters" collection with
    its eye icon (hide_cutters), so clicking that eye shows them all again as wireframes.
    """
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    cutter_collection().objects.link(obj)
    obj.parent = root()
    obj.matrix_parent_inverse = Matrix.Identity(4)
    obj.display_type = "WIRE"
    obj.hide_render = True
    if material:
        assign(obj, material)
    return obj


def boolean(obj, operand, operation="DIFFERENCE", solver="EXACT", name=None):
    """Live boolean. `operand` is an object or a collection of cutters.

    Cut faces take the cutter's material (material_mode TRANSFER), so give a cutter a
    dark material for openings such as the bore or ejection port.
    """
    mod = obj.modifiers.new(name or f"Bool {getattr(operand, 'name', 'cut')}", "BOOLEAN")
    mod.operation = operation
    mod.solver = solver
    if isinstance(operand, bpy.types.Collection):
        mod.operand_type = "COLLECTION"
        mod.collection = operand
    else:
        mod.object = operand
    if solver == "EXACT":
        mod.material_mode = "TRANSFER"
        mod.use_self = False
        mod.use_hole_tolerant = False
    return mod


def _smooth_by_angle_group(angle_deg=32):
    """Geometry-nodes group that shades smooth except edges sharper than angle_deg."""
    name = f"Smooth by Angle {angle_deg:g}°"
    ng = bpy.data.node_groups.get(name)
    if ng:
        return ng
    ng = bpy.data.node_groups.new(name, "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    n = ng.nodes
    gin = n.new("NodeGroupInput")
    gout = n.new("NodeGroupOutput")
    edge_angle = n.new("GeometryNodeInputMeshEdgeAngle")
    cmp = n.new("FunctionNodeCompare")
    cmp.data_type = "FLOAT"
    cmp.operation = "LESS_EQUAL"
    cmp.inputs[1].default_value = math.radians(angle_deg)
    set_f = n.new("GeometryNodeSetShadeSmooth")
    set_f.domain = "FACE"
    set_e = n.new("GeometryNodeSetShadeSmooth")
    set_e.domain = "EDGE"
    l = ng.links
    l.new(edge_angle.outputs["Unsigned Angle"], cmp.inputs[0])
    l.new(gin.outputs["Geometry"], set_f.inputs["Geometry"])
    l.new(set_f.outputs["Geometry"], set_e.inputs["Geometry"])
    l.new(cmp.outputs["Result"], set_e.inputs["Shade Smooth"])
    l.new(set_e.outputs["Geometry"], gout.inputs["Geometry"])
    return ng


def finish(obj, bevel=0.4, segments=3, bevel_angle=30, smooth_angle=32, weighted=True):
    """Standard modifier tail: Bevel (by angle) -> Weld -> Smooth by Angle -> Weighted Normal.

    Call after adding booleans, so cut edges get bevelled and shaded too.
    Pass bevel=0 to skip the bevel. At least 3 bevel segments are used: with 2, the middle
    facet of a 90 degree edge turns 45 degrees, more than the smooth angle, and shows as a
    crease. The Weld collapses the zero-area faces a clamped bevel leaves on short steps.
    """
    if bevel > 0:
        b = obj.modifiers.new("Bevel", "BEVEL")
        b.width = bevel
        b.segments = max(segments, 3)
        b.limit_method = "ANGLE"
        b.angle_limit = math.radians(bevel_angle)
        b.use_clamp_overlap = True
        b.miter_outer = "MITER_ARC"
        b.profile = 0.5
        wd = obj.modifiers.new("Weld", "WELD")
        wd.merge_threshold = 0.002           # mm (object space): 2 micrometres
        wd.mode = "ALL"
    s = obj.modifiers.new("Smooth by Angle", "NODES")
    s.node_group = _smooth_by_angle_group(smooth_angle)
    if weighted:
        w = obj.modifiers.new("Weighted Normal", "WEIGHTED_NORMAL")
        w.keep_sharp = True
        w.weight = 50
        w.mode = "FACE_AREA"
    return obj


def part_info(obj, title, description):
    """Store the German part name/description as custom properties (shown in the N panel)."""
    obj["Teil"] = title
    obj["Beschreibung"] = description
    return obj


def evaluated_bounds(objs):
    """World-space bounding box (min, max Vectors, metres) of the evaluated objects."""
    dg = bpy.context.evaluated_depsgraph_get()
    lo = Vector((1e9, 1e9, 1e9))
    hi = Vector((-1e9, -1e9, -1e9))
    for o in objs:
        ev = o.evaluated_get(dg)
        if ev.type != "MESH":
            continue
        for corner in ev.bound_box:
            w = ev.matrix_world @ Vector(corner)
            lo = Vector(map(min, lo, w))
            hi = Vector(map(max, hi, w))
    return lo, hi
