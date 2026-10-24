# build_church.py - procedural gothic church interior (Blender 5.x).
# Run inside Blender:  exec(open(PATH).read())
# Units are metres, Z is up, the nave runs along +Y towards the altar.
import bpy, bmesh, math, random
from mathutils import Vector
from mathutils import noise as mnoise

SQ3 = math.sqrt(3.0)

# ---------------------------------------------------------------- dimensions
BAY = 5.0
NBAY = 8
Y0 = -20.0
Y1 = Y0 + BAY * NBAY
NAVE = 4.6          # nave side of the arcade wall
PIER_X = 5.0        # arcade line
WALL_OUT = 5.4      # aisle side of the arcade wall
AISLE = 9.5         # inner face of the outer wall
OUTER_T = 0.8
END_T = 0.9
PIER_HALF = 0.75
ARC_SPRING = 6.6
TRIF_BOT = 10.4
TRIF_TOP = 13.0
V_SPRING = 18.0     # nave vault
V_RISE = 6.0
A_SPRING = 6.65     # aisle vaults
A_RISE = 3.6
TOP = 24.6          # walls run up past the vault apex to the roof
AISLE_TOP = 10.4    # outer walls run up to the aisle roof

CL_H, CL_SILL, CL_SPRING = 1.1, 13.7, 18.1      # clerestory windows
AW_H, AW_SILL, AW_SPRING = 0.85, 2.3, 6.0       # aisle windows
E_H, E_SILL, E_SPRING = 3.2, 5.6, 15.5          # east window
D_H, D_SPRING = 1.5, 3.6                        # west door
R_Z, R_R = 14.5, 3.4                            # west rose window

CHANCEL_Y = 11.0
CHANCEL_H = 0.45


# ---------------------------------------------------------------- 2d helpers
def pointed(t):
    """Equilateral pointed-arch profile: 0 at t=+-1, 1 at t=0."""
    t = min(1.0, abs(t))
    return math.sqrt(max(0.0, 4.0 - (t + 1.0) ** 2)) / SQ3


def arch(c, h, spring, rise=None, n=12):
    """Pointed arch from (c-h, spring) over the apex to (c+h, spring)."""
    rise = h * SQ3 if rise is None else rise
    k = rise / (h * SQ3)
    pts = []
    for i in range(n + 1):
        a = math.radians(60.0) * i / n
        pts.append((c + h - 2 * h * math.cos(a), spring + k * 2 * h * math.sin(a)))
    for i in range(n - 1, -1, -1):
        a = math.radians(60.0) * i / n
        pts.append((c - h + 2 * h * math.cos(a), spring + k * 2 * h * math.sin(a)))
    return pts


def half_arch(c, h, spring, rise=None, n=12):
    """Left half of arch(): springing to apex."""
    return arch(c, h, spring, rise, n)[: n + 1]


def clean(poly, eps=1e-4):
    out = []
    for p in poly:
        if not out or abs(p[0] - out[-1][0]) > eps or abs(p[1] - out[-1][1]) > eps:
            out.append(p)
    if len(out) > 2 and abs(out[0][0] - out[-1][0]) < eps and abs(out[0][1] - out[-1][1]) < eps:
        out.pop()
    return out


def mirror(poly, c):
    return [(2 * c - u, v) for u, v in reversed(poly)]


# ---------------------------------------------------------------- mesh builder
class MB:
    def __init__(self):
        self.bm = bmesh.new()

    def verts(self, cos):
        return [self.bm.verts.new(c) for c in cos]

    def face(self, vs):
        try:
            return self.bm.faces.new(vs)
        except ValueError:
            return None

    def mark(self):
        return len(self.bm.verts), len(self.bm.faces)

    def verts_since(self, mark):
        return list(self.bm.verts)[mark[0]:]

    def paint(self, mark, rgb):
        """Vertex-colour every face made since mark (multiplies the albedo)."""
        layer = self.bm.loops.layers.color.get('Col') or self.bm.loops.layers.color.new('Col')
        for f in list(self.bm.faces)[mark[1]:]:
            for l in f.loops:
                l[layer] = (*rgb, 1.0)

    def transform_since(self, mark, cx, cy, angle, dx=0.0, dy=0.0):
        """Rotate the verts made since mark about (cx, cy) and shift them."""
        c, s = math.cos(angle), math.sin(angle)
        for v in self.verts_since(mark):
            x, y = v.co.x - cx, v.co.y - cy
            v.co.x = cx + x * c - y * s + dx
            v.co.y = cy + x * s + y * c + dy


def weather(bm, seed=0, step=0.16, chip=0.03, freq=2.5, thresh=0.25, bump=0.003, sharp=50.0, max_cuts=60):
    """Break up perfect CG edges: split sharp edges and push random runs of them inward as chips."""
    bm.normal_update()
    lim = math.radians(sharp)

    def is_sharp(e):
        return len(e.link_faces) == 2 and e.calc_face_angle(0.0) > lim

    groups = {}
    for e in bm.edges:
        if is_sharp(e):
            n = min(max_cuts, int(e.calc_length() / step))
            if n >= 1:
                groups.setdefault(n, []).append(e)
    for n, es in groups.items():
        bmesh.ops.subdivide_edges(bm, edges=es, cuts=n, use_grid_fill=False)
    bm.normal_update()
    edge_verts = set()
    for e in bm.edges:
        if is_sharp(e):
            edge_verts.update(e.verts)
    off = Vector((seed * 17.3, seed * 5.1, seed * 9.7))
    rnd = random.Random(seed)
    moves = []
    for v in bm.verts:
        p = v.co * freq + off
        d = bump * mnoise.noise(p * 3.1)
        if v in edge_verts:
            k = max(0.0, min(1.0, (mnoise.noise(p) - thresh) / 0.25))
            d -= chip * k * (0.5 + 0.5 * rnd.random())
        moves.append((v, v.normal.copy(), d))
    for v, n, d in moves:
        v.co += n * d


def prism(mb, poly, axis, a0, a1):
    """Extrude a 2d polygon (u, v=z) along axis 'x' or 'y' from a0 to a1."""
    poly = clean(poly)
    if axis == 'x':
        P = lambda u, v, a: (a, u, v)
    else:
        P = lambda u, v, a: (u, a, v)
    f = mb.verts([P(u, v, a0) for u, v in poly])
    b = mb.verts([P(u, v, a1) for u, v in poly])
    mb.face(f)
    mb.face(b[::-1])
    n = len(poly)
    for i in range(n):
        j = (i + 1) % n
        mb.face([f[i], f[j], b[j], b[i]])


def box(mb, x0, x1, y0, y1, z0, z1):
    prism(mb, [(x0, z0), (x1, z0), (x1, z1), (x0, z1)], 'y', y0, y1)


def lathe(mb, cx, cy, prof, seg=16, rot=0.0):
    """Revolve [(r, z), ...] around a vertical axis; ends are capped."""
    rings = []
    for r, z in prof:
        rings.append(mb.verts([(cx + r * math.cos(rot + 2 * math.pi * k / seg),
                                cy + r * math.sin(rot + 2 * math.pi * k / seg), z) for k in range(seg)]))
    for i in range(len(rings) - 1):
        for k in range(seg):
            k2 = (k + 1) % seg
            mb.face([rings[i][k], rings[i][k2], rings[i + 1][k2], rings[i + 1][k]])
    mb.face(rings[0][::-1])
    mb.face(rings[-1])


def cyl(mb, cx, cy, z0, z1, r, seg=12, rot=0.0, drum=None, rnd=None):
    """Cylinder; with `drum` it is built of stone drums with sunk joints and slightly uneven radii."""
    if not drum:
        lathe(mb, cx, cy, [(r, z0), (r, z1)], seg, rot)
        return
    rnd = rnd or random.Random(int(cx * 100 + cy * 7))
    n = max(1, round((z1 - z0) / drum))
    prof = [(r, z0)]
    for i in range(1, n):
        z = z0 + (z1 - z0) * i / n
        ra, rb = r * rnd.uniform(0.994, 1.006), r * rnd.uniform(0.994, 1.006)
        prof += [(ra, z - 0.014), (min(ra, rb) * 0.965, z), (rb, z + 0.014)]
    prof.append((r, z1))
    lathe(mb, cx, cy, prof, seg, rot)


def tube(mb, pts, r, depth=None, seg=8, ref=None, closed=False):
    """Sweep an ellipse (half-width r, half-depth `depth` along ref) along pts."""
    pts = [Vector(p) for p in pts]
    depth = r if depth is None else depth
    n = len(pts)
    T = []
    for i in range(n):
        if closed:
            t = pts[(i + 1) % n] - pts[i - 1]
        elif i == 0:
            t = pts[1] - pts[0]
        elif i == n - 1:
            t = pts[-1] - pts[-2]
        else:
            t = pts[i + 1] - pts[i - 1]
        T.append(t.normalized())
    ref = Vector(ref) if ref is not None else (Vector((0, 0, 1)) if abs(T[0].z) < 0.9 else Vector((1, 0, 0)))
    N = (ref - T[0] * ref.dot(T[0])).normalized()
    rings = []
    for i in range(n):
        N = (N - T[i] * N.dot(T[i])).normalized()
        B = T[i].cross(N)
        rings.append(mb.verts([pts[i] + N * (math.cos(2 * math.pi * k / seg) * depth)
                               + B * (math.sin(2 * math.pi * k / seg) * r) for k in range(seg)]))
    last = n if closed else n - 1
    for i in range(last):
        i2 = (i + 1) % n
        for k in range(seg):
            k2 = (k + 1) % seg
            f = mb.face([rings[i][k], rings[i2][k], rings[i2][k2], rings[i][k2]])
            if f:
                f.normal_update()
                if f.normal.dot(f.calc_center_median() - pts[i]) < 0:
                    f.normal_flip()


# ---------------------------------------------------------------- materials / objects
def get_mat(name, color=(0.5, 0.5, 0.5), rough=0.8):
    m = bpy.data.materials.get(name)
    if m is None:
        m = bpy.data.materials.new(name)
        m.use_nodes = True
        bsdf = next(n for n in m.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
        bsdf.inputs['Base Color'].default_value = (*color, 1.0)
        bsdf.inputs['Roughness'].default_value = rough
    return m


def box_uv(bm, scale):
    uv = bm.loops.layers.uv.verify()
    for f in bm.faces:
        n = f.normal
        ax = max(range(3), key=lambda i: abs(n[i]))
        for l in f.loops:
            co = l.vert.co
            if ax == 0:
                u, v = co.y, co.z
            elif ax == 1:
                u, v = co.x, co.z
            else:
                u, v = co.x, co.y
            l[uv].uv = (u / scale, v / scale)


def make_obj(name, mb, mat, coll, closed=True, uv_scale=2.0, smooth=35.0, uv_fn=None, wear=None):
    bm = mb.bm
    if closed:
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    if wear is not None:
        weather(bm, **wear)
    bm.normal_update()
    if uv_fn:
        uv_fn(bm)
    else:
        box_uv(bm, uv_scale)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    if me.uv_layers:
        me.uv_layers[0].name = 'UVMap'
    if smooth:
        for p in me.polygons:
            p.use_smooth = True
        me.set_sharp_from_angle(angle=math.radians(smooth))
    ob = bpy.data.objects.new(name, me)
    coll.objects.link(ob)
    me.materials.append(mat if isinstance(mat, bpy.types.Material) else get_mat(mat))
    return ob


def reset():
    for ob in list(bpy.data.objects):
        if ob.name in ('Cube', 'Light'):
            bpy.data.objects.remove(ob, do_unlink=True)
    coll = bpy.data.collections.get('Church')
    if coll:
        for ob in list(coll.all_objects):
            data = ob.data
            bpy.data.objects.remove(ob, do_unlink=True)
            if data is not None and data.users == 0:
                if isinstance(data, bpy.types.Mesh):
                    bpy.data.meshes.remove(data)
                elif isinstance(data, bpy.types.Light):
                    bpy.data.lights.remove(data)
    else:
        coll = bpy.data.collections.new('Church')
        bpy.context.scene.collection.children.link(coll)
    return coll


# ---------------------------------------------------------------- architecture
def bays():
    for i in range(NBAY):
        y0 = Y0 + i * BAY
        yield y0, y0 + BAY, y0 + BAY / 2


def build_vaults(coll):
    mb = MB()
    ribs = MB()

    def vault(x0, x1, y0, y1, spring, rise, n=24):
        cx, cy, hx, hy = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
        g = [math.sin(math.pi / 2 * (-1 + 2 * i / n)) for i in range(n + 1)]
        V = [[mb.bm.verts.new((cx + hx * g[i], cy + hy * g[j],
                               spring + rise * max(pointed(g[i]), pointed(g[j]))))
              for j in range(n + 1)] for i in range(n + 1)]
        for i in range(n):
            for j in range(n):
                a, b, c, d = V[i][j], V[i + 1][j], V[i + 1][j + 1], V[i][j + 1]
                tris = [(a, b, c), (a, c, d)] if (g[i] + g[i + 1]) * (g[j] + g[j + 1]) > 0 else [(a, b, d), (b, c, d)]
                for t in tris:
                    f = mb.face(list(t))
                    f.normal_update()
                    if f.normal.z > 0:
                        f.normal_flip()

    def surf(cx, cy, hx, hy, spring, rise, u, v, drop):
        return (cx + hx * u, cy + hy * v, spring + rise * max(pointed(u), pointed(v)) - drop)

    def vault_ribs(x0, x1, y0, y1, spring, rise, r, n=24):
        cx, cy, hx, hy = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
        s = [math.sin(math.pi / 2 * (-1 + 2 * i / n)) for i in range(n + 1)]
        drop = r * 0.6
        tube(ribs, [surf(cx, cy, hx, hy, spring, rise, t, t, drop) for t in s], r, r * 1.5)
        tube(ribs, [surf(cx, cy, hx, hy, spring, rise, t, -t, drop) for t in s], r, r * 1.5)
        # boss where the diagonals cross
        lathe(ribs, cx, cy, [(r * 2.2, spring + rise - 0.05), (r * 2.6, spring + rise - r * 1.6),
                             (r * 1.2, spring + rise - r * 2.6)], seg=12)

    def transverse(x0, x1, y, spring, rise, r, n=24):
        cx, hx = (x0 + x1) / 2, (x1 - x0) / 2
        s = [math.sin(math.pi / 2 * (-1 + 2 * i / n)) for i in range(n + 1)]
        tube(ribs, [(cx + hx * t, y, spring + rise * pointed(t) - r * 0.6) for t in s], r * 1.25, r * 1.6)

    def wall_rib(x, y0, y1, spring, rise, r, n=24):
        cy, hy = (y0 + y1) / 2, (y1 - y0) / 2
        s = [math.sin(math.pi / 2 * (-1 + 2 * i / n)) for i in range(n + 1)]
        tube(ribs, [(x, cy + hy * t, spring + rise * pointed(t) - r * 0.4) for t in s], r * 0.8, r)

    for y0, y1, yc in bays():
        vault(-NAVE, NAVE, y0, y1, V_SPRING, V_RISE)
        vault_ribs(-NAVE, NAVE, y0, y1, V_SPRING, V_RISE, 0.16)
        for s in (-1, 1):
            xa, xb = sorted((s * WALL_OUT, s * AISLE))
            vault(xa, xb, y0, y1, A_SPRING, A_RISE, 16)
            vault_ribs(xa, xb, y0, y1, A_SPRING, A_RISE, 0.11, 16)
            wall_rib(s * NAVE, y0, y1, V_SPRING, V_RISE, 0.14)
            wall_rib(s * AISLE, y0, y1, A_SPRING, A_RISE, 0.11)
    for i in range(NBAY + 1):
        y = Y0 + i * BAY
        transverse(-NAVE, NAVE, y, V_SPRING, V_RISE, 0.16)
        for s in (-1, 1):
            xa, xb = sorted((s * WALL_OUT, s * AISLE))
            transverse(xa, xb, y, A_SPRING, A_RISE, 0.11, 16)
    # ridge ribs
    tube(ribs, [(0, Y0, V_SPRING + V_RISE - 0.1), (0, Y1, V_SPRING + V_RISE - 0.1)], 0.12, 0.14)
    make_obj('Vaults', mb, 'Stone', coll, closed=False, uv_scale=2.5, smooth=50)
    make_obj('Ribs', ribs, 'StoneTrim', coll, closed=False, uv_scale=1.5, smooth=70)


def build_piers(coll):
    mb = MB()
    for i in range(NBAY + 1):
        y = Y0 + i * BAY
        for s in (-1, 1):
            x = s * PIER_X
            inward = math.pi if s > 0 else 0.0           # direction towards the nave centre
            lathe(mb, x, y, [(0.98, 0.0), (0.98, 0.32), (0.86, 0.32), (0.86, 0.42)], seg=8, rot=math.pi / 8)
            lathe(mb, x, y, [(0.80, 0.42), (0.88, 0.5), (0.80, 0.62)], seg=20)
            cyl(mb, x, y, 0.42, 6.0, 0.55, seg=20, drum=0.6)
            for k in range(8):
                a = inward + k * math.pi / 4
                off, r = (0.55, 0.17) if k % 2 == 0 else (0.5, 0.11)
                top = V_SPRING - 0.4 if k == 0 else 6.0
                cyl(mb, x + off * math.cos(a), y + off * math.sin(a), 0.42, top, r, seg=10,
                    drum=0.55 if k == 0 else None)
            lathe(mb, x, y, [(0.74, 5.95), (0.76, 6.05), (0.98, 6.4), (0.98, 6.45)], seg=20)
            lathe(mb, x, y, [(1.04, 6.45), (1.04, 6.6)], seg=8, rot=math.pi / 8)
            # vault shafts above the arcade capital
            sx = x + 0.55 * math.cos(inward)
            for dy in (-0.3, 0.3):
                cyl(mb, sx + s * 0.07, y + dy, 6.6, V_SPRING - 0.4, 0.1, seg=8)
            for z in (TRIF_BOT - 0.1, TRIF_TOP):
                lathe(mb, sx, y, [(0.24, z - 0.08), (0.27, z), (0.24, z + 0.08)], seg=12)
            lathe(mb, sx + s * 0.05, y, [(0.3, V_SPRING - 0.42), (0.32, V_SPRING - 0.32),
                                         (0.5, V_SPRING - 0.02), (0.5, V_SPRING + 0.06)], seg=12)
        # outer wall responds
        for s in (-1, 1):
            x = s * AISLE
            lathe(mb, x, y, [(0.42, 0.0), (0.42, 0.35), (0.32, 0.45)], seg=12)
            cyl(mb, x, y, 0.45, A_SPRING - 0.4, 0.24, seg=12)
            lathe(mb, x, y, [(0.26, A_SPRING - 0.45), (0.28, A_SPRING - 0.35), (0.46, A_SPRING - 0.05),
                             (0.46, A_SPRING + 0.04)], seg=12)
    make_obj('Piers', mb, 'Stone', coll, uv_scale=2.0, smooth=40,
             wear=dict(seed=3, step=0.12, chip=0.025, freq=3.0, sharp=55))


def build_walls(coll):
    mb = MB()
    trim = MB()
    for y0, y1, yc in bays():
        for s in (-1, 1):
            xa, xb = s * NAVE, s * WALL_OUT
            # arcade spandrel
            poly = ([(y0, ARC_SPRING)] + arch(yc, BAY / 2 - PIER_HALF, ARC_SPRING)
                    + [(y1, ARC_SPRING), (y1, TRIF_BOT), (y0, TRIF_BOT)])
            prism(mb, poly, 'x', xa, xb)
            # arch mouldings on both faces of the arcade
            for xm in (s * (NAVE - 0.02), s * (WALL_OUT + 0.02)):
                tube(trim, [(xm, u, v) for u, v in arch(yc, BAY / 2 - PIER_HALF + 0.05, ARC_SPRING - 0.05, n=16)],
                     0.13, 0.13, seg=8)
            # clerestory with the window opening
            # the wall runs on past the vault to the roof, closing the roof space for the bake
            left = ([(y0, TRIF_TOP), (yc, TRIF_TOP), (yc, CL_SILL), (yc - CL_H, CL_SILL)]
                    + half_arch(yc, CL_H, CL_SPRING) + [(yc, TOP), (y0, TOP)])
            prism(mb, left, 'x', xa, xb)
            prism(mb, mirror(left, yc), 'x', xa, xb)
            # outer aisle wall with its window
            xa, xb = s * AISLE, s * (AISLE + OUTER_T)
            left = ([(y0, 0.0), (yc, 0.0), (yc, AW_SILL), (yc - AW_H, AW_SILL)]
                    + half_arch(yc, AW_H, AW_SPRING) + [(yc, AISLE_TOP), (y0, AISLE_TOP)])
            prism(mb, left, 'x', xa, xb)
            prism(mb, mirror(left, yc), 'x', xa, xb)
    for s in (-1, 1):
        prism(mb, [(Y0, TRIF_BOT), (Y1, TRIF_BOT), (Y1, TRIF_TOP), (Y0, TRIF_TOP)], 'x', s * NAVE, s * WALL_OUT)
        # string courses
        for z in (TRIF_BOT, TRIF_TOP, CL_SILL - 0.05):
            tube(trim, [(s * (NAVE - 0.02), Y0, z), (s * (NAVE - 0.02), Y1, z)], 0.1, 0.09, seg=8)
        tube(trim, [(s * (AISLE - 0.02), Y0, AW_SILL - 0.05), (s * (AISLE - 0.02), Y1, AW_SILL - 0.05)], 0.08, 0.08, seg=8)
        # blind triforium arcade
        for y0, y1, yc in bays():
            n = 4
            a, b = y0 + 0.5, y1 - 0.5
            w = (b - a) / n
            xm = s * (NAVE - 0.04)
            for k in range(n + 1):
                tube(trim, [(xm, a + k * w, TRIF_BOT + 0.1), (xm, a + k * w, 11.95)], 0.06, 0.06, seg=6)
            for k in range(n):
                tube(trim, [(xm, u, v) for u, v in arch(a + (k + 0.5) * w, w / 2, 11.95, n=8)], 0.06, 0.06, seg=6)

    # east wall with the great window
    left = ([(-AISLE - OUTER_T, 0.0), (0.0, 0.0), (0.0, E_SILL), (-E_H, E_SILL)]
            + half_arch(0.0, E_H, E_SPRING, n=20) + [(0.0, TOP), (-AISLE - OUTER_T, TOP)])
    prism(mb, left, 'y', Y1, Y1 + END_T)
    prism(mb, mirror(left, 0.0), 'y', Y1, Y1 + END_T)
    # west wall with door and rose
    ring = [(R_R * math.cos(math.radians(a)), R_Z + R_R * math.sin(math.radians(a))) for a in range(270, 89, -6)]
    left = ([(-AISLE - OUTER_T, 0.0), (-D_H, 0.0)] + half_arch(0.0, D_H, D_SPRING) + ring
            + [(0.0, TOP), (-AISLE - OUTER_T, TOP)])
    prism(mb, left, 'y', Y0 - END_T, Y0)
    prism(mb, mirror(left, 0.0), 'y', Y0 - END_T, Y0)
    make_obj('Walls', mb, 'Stone', coll, uv_scale=2.0, smooth=30,
             wear=dict(seed=1, step=0.15, chip=0.035, freq=2.2, bump=0.004))
    # stone bench along both aisle walls
    bench = MB()
    for s in (-1, 1):
        for y0, y1, yc in bays():
            prism(bench, [(s * (AISLE - 0.42), 0.0), (s * AISLE, 0.0), (s * AISLE, 0.44), (s * (AISLE - 0.42), 0.44)],
                  'y', y0 + 0.3, y1 - 0.3)
    make_obj('Bench', bench, 'StoneTrim', coll, uv_scale=1.5, smooth=30,
             wear=dict(seed=5, step=0.1, chip=0.03, freq=3.5, thresh=0.15))
    make_obj('Mouldings', trim, 'StoneTrim', coll, closed=False, uv_scale=1.5, smooth=70)


def build_roof(coll):
    """Bake-only roofs: keep moonlight out of the space above the vaults. Not exported."""
    mb = MB()
    box(mb, -WALL_OUT, WALL_OUT, Y0 - END_T, Y1 + END_T, TOP, TOP + 0.3)
    for s in (-1, 1):
        lean = [(s * WALL_OUT, TRIF_TOP), (s * (AISLE + OUTER_T), AISLE_TOP),
                (s * (AISLE + OUTER_T), AISLE_TOP + 0.3), (s * WALL_OUT, TRIF_TOP + 0.3)]
        prism(mb, lean, 'y', Y0 - END_T, Y1 + END_T)
    make_obj('Roof', mb, 'Roof', coll, smooth=0)


# joints of floor_tiles_04 (4 m texture, UV = world / 4): rows (v0, v1) and the joints in each row
TILE = 4.0
TILE_ROWS = [(-0.035, 0.965, (0.946, 1.949, 3.451)),
             (0.965, 1.959, (0.438, 1.437, 2.947)),
             (1.959, 2.967, (0.914, 1.916, 3.427)),
             (2.967, 3.965, (0.44, 1.435, 2.947))]


def slab_rects(x0, x1, y0, y1):
    """Slab rectangles that follow the joints painted in the floor texture."""
    out = []
    for ky in range(math.floor(y0 / TILE) - 1, math.ceil(y1 / TILE) + 1):
        for r0, r1, js in TILE_ROWS:
            ya, yb = ky * TILE + r0, ky * TILE + r1
            if yb <= y0 or ya >= y1:
                continue
            for kx in range(math.floor(x0 / TILE) - 1, math.ceil(x1 / TILE) + 1):
                for i in range(len(js)):
                    xa = kx * TILE + js[i]
                    xb = kx * TILE + (js[i + 1] if i + 1 < len(js) else js[0] + TILE)
                    if xb <= x0 or xa >= x1:
                        continue
                    out.append((max(xa, x0), min(xb, x1), max(ya, y0), min(yb, y1)))
    return out


def slab(mb, xa, xb, ya, yb, z, rnd, depth=0.05, gap=0.003, bev=0.012, tilt=0.004, chip=0.12, sink=0.0):
    """A stone with a chamfered top, a little out of level, sometimes with a broken corner."""
    xa, xb, ya, yb = xa + gap, xb - gap, ya + gap, yb - gap
    if xb - xa < 0.06 or yb - ya < 0.06:
        return
    z -= sink
    hz = [z + rnd.uniform(-tilt, tilt) for _ in range(4)]
    outer = [(xa, ya), (xb, ya), (xb, yb), (xa, yb)]
    inner = [(xa + bev, ya + bev), (xb - bev, ya + bev), (xb - bev, yb - bev), (xa + bev, yb - bev)]
    top = mb.verts([(u, v, hz[i] - (rnd.uniform(0.008, 0.025) if rnd.random() < chip else 0.0))
                    for i, (u, v) in enumerate(inner)])
    rim = mb.verts([(u, v, hz[i] - bev * 0.8) for i, (u, v) in enumerate(outer)])
    bot = mb.verts([(u, v, z - depth) for u, v in outer])
    mb.face(top)
    for i in range(4):
        j = (i + 1) % 4
        mb.face([top[i], rim[i], rim[j], top[j]])
        mb.face([rim[i], bot[i], bot[j], rim[j]])


def stone_tint(rnd, ledger=0.0):
    if rnd.random() < ledger:
        g = rnd.uniform(0.42, 0.52)
        return (g * 0.95, g * 0.97, g * 1.05)          # dark slate ledger stone
    b = rnd.uniform(0.8, 1.08)
    return (b * rnd.uniform(0.97, 1.04), b, b * rnd.uniform(0.93, 1.03))


def build_floor(coll):
    rnd = random.Random(7)
    mb = MB()
    for xa, xb, ya, yb in slab_rects(-AISLE, AISLE, Y0, Y1):
        if max(abs(xa), abs(xb)) <= NAVE and ya >= CHANCEL_Y:
            continue                                    # under the chancel
        m = mb.mark()
        worn = 0.004 if abs((xa + xb) / 2) < 1.2 or abs(abs((xa + xb) / 2) - 7.2) < 1.0 else 0.0
        slab(mb, xa, xb, ya, yb, 0.0, rnd, sink=worn * rnd.random())
        mb.paint(m, stone_tint(rnd, ledger=0.05))
    make_obj('Floor', mb, 'Floor', coll, closed=False, uv_scale=TILE, smooth=0)

    mortar = MB()
    box(mortar, -AISLE, AISLE, Y0, Y1, -0.3, -0.045)          # below the deepest chipped corner
    make_obj('Mortar', mortar, 'Mortar', coll, smooth=0)

    # chancel: a core, two steps of separate blocks and a paved platform
    mb = MB()
    m = mb.mark()
    slab(mb, -NAVE, NAVE, CHANCEL_Y + 0.7, Y1, CHANCEL_H - 0.015, rnd, depth=CHANCEL_H, gap=0.0, bev=0.001, tilt=0.0, chip=0.0)
    mb.paint(m, (0.5, 0.5, 0.5))
    for k in range(2):
        y0 = CHANCEL_Y + k * 0.35
        x = -NAVE
        while x < NAVE - 0.05:
            w = min(NAVE - x, rnd.uniform(0.7, 1.35))
            mid = abs(x + w / 2)
            m = mb.mark()
            slab(mb, x, x + w, y0, y0 + 0.75 - k * 0.35, 0.15 * (k + 1), rnd, depth=0.15 * (k + 1),
                 gap=0.002, bev=0.03 if mid < 1.5 else 0.015, chip=0.3, sink=0.006 if mid < 1.5 else 0.0)
            mb.paint(m, stone_tint(rnd))
            x += w
    for xa, xb, ya, yb in slab_rects(-NAVE, NAVE, CHANCEL_Y + 0.7, Y1):
        m = mb.mark()
        slab(mb, xa, xb, ya, yb, CHANCEL_H, rnd, depth=0.03)
        mb.paint(m, stone_tint(rnd, ledger=0.08))
    make_obj('Chancel', mb, 'Floor', coll, closed=False, uv_scale=TILE, smooth=0)


# ---------------------------------------------------------------- windows
def plane_map(axis, a):
    return (lambda u, v: (a, u, v)) if axis == 'x' else (lambda u, v: (u, a, v))


def glass_face(mb, poly, axis, a, uv_of):
    P = plane_map(axis, a)
    f = mb.face(mb.verts([P(u, v) for u, v in clean(poly)]))
    uv = mb.bm.loops.layers.uv.verify()
    for l, (u, v) in zip(f.loops, clean(poly)):
        l[uv].uv = uv_of(u, v)
    return f


def window(stone, glass, axis, a, out, c, h, sill, spring, levels, uv_rect):
    """Lancet window: glass pane plus stone tracery. `out` is +-1, the outward side."""
    apex = spring + h * SQ3
    outline = [(c - h, sill), (c + h, sill)] + list(reversed(arch(c, h, spring, n=16)))
    u0, v0, u1, v1 = uv_rect
    glass_face(glass, outline, axis, a + out * 0.12,
               lambda u, v: (u0 + (u1 - u0) * (u - c + h) / (2 * h), v0 + (v1 - v0) * (v - sill) / (apex - sill)))
    P = plane_map(axis, a)
    nrm = (1, 0, 0) if axis == 'x' else (0, 1, 0)
    rim = [(c - h, sill)] + arch(c, h, spring, n=16) + [(c + h, sill)]
    tube(stone, [P(u, v) for u, v in rim], 0.08, 0.2, seg=6, ref=nrm)
    tube(stone, [P(c - h, sill), P(c + h, sill)], 0.09, 0.22, seg=6, ref=nrm)

    def tracery(c, h, lvl):
        if lvl == 0:
            return
        r = 0.035 + 0.02 * lvl
        tube(stone, [P(c, sill), P(c, spring)], r, 0.16, seg=6, ref=nrm)
        for cc in (c - h / 2, c + h / 2):
            tube(stone, [P(u, v) for u, v in arch(cc, h / 2, spring, n=10)], r * 0.85, 0.14, seg=6, ref=nrm)
        oz, orad = spring + 1.29 * h, 0.36 * h
        ring = [P(c + orad * math.cos(t * math.pi / 16), oz + orad * math.sin(t * math.pi / 16)) for t in range(32)]
        tube(stone, ring, r * 0.85, 0.14, seg=6, ref=nrm, closed=True)
        fr = orad * 0.42      # quatrefoil inside the oculus
        for k in range(4):
            fx, fz = c + fr * math.cos(k * math.pi / 2), oz + fr * math.sin(k * math.pi / 2)
            ring = [P(fx + fr * 0.62 * math.cos(t * math.pi / 8), fz + fr * 0.62 * math.sin(t * math.pi / 8)) for t in range(16)]
            tube(stone, ring, r * 0.5, 0.1, seg=5, ref=nrm, closed=True)
        tracery(c - h / 2, h / 2, lvl - 1)
        tracery(c + h / 2, h / 2, lvl - 1)

    tracery(c, h, levels)


def rose(stone, glass, y, out):
    P = lambda u, v: (u, y, v)
    nrm = (0, 1, 0)
    disk = [(R_R * math.cos(t * math.pi / 36), R_Z + R_R * math.sin(t * math.pi / 36)) for t in range(72)]
    glass_face(glass, disk, 'y', y + out * 0.12, lambda u, v: (0.5 + u / (2 * R_R), 0.5 + (v - R_Z) / (2 * R_R)))

    def circle(cx, cz, r, rr, depth, n=48):
        tube(stone, [P(cx + r * math.cos(t * 2 * math.pi / n), cz + r * math.sin(t * 2 * math.pi / n)) for t in range(n)],
             rr, depth, seg=6, ref=nrm, closed=True)

    circle(0, R_Z, R_R - 0.05, 0.12, 0.22, 72)
    circle(0, R_Z, R_R * 0.8, 0.07, 0.16, 64)
    circle(0, R_Z, R_R * 0.3, 0.08, 0.16, 32)
    circle(0, R_Z, R_R * 0.12, 0.05, 0.12, 16)
    for k in range(12):
        t = k * math.pi / 6
        ca, sa = math.cos(t), math.sin(t)
        tube(stone, [P(R_R * 0.3 * ca, R_Z + R_R * 0.3 * sa), P(R_R * 0.8 * ca, R_Z + R_R * 0.8 * sa)], 0.06, 0.15, seg=6, ref=nrm)
        t2 = t + math.pi / 12
        circle(R_R * 0.9 * math.cos(t2), R_Z + R_R * 0.9 * math.sin(t2), R_R * 0.075, 0.035, 0.1, 12)
        # pointed heads between the spokes
        a0, a1 = t, t + math.pi / 6
        pts = []
        for i in range(9):
            tt = a0 + (a1 - a0) * i / 8
            bump = 0.08 * math.sin(math.pi * i / 8) ** 0.7
            pts.append(P(R_R * (0.66 + bump) * math.cos(tt), R_Z + R_R * (0.66 + bump) * math.sin(tt)))
        tube(stone, pts, 0.045, 0.12, seg=6, ref=nrm)


def build_windows(coll):
    stone = MB()
    glass = MB()
    for i, (y0, y1, yc) in enumerate(bays()):
        for s in (-1, 1):
            col = (i + (s > 0)) % 2
            window(stone, glass, 'x', s * PIER_X, s, yc, CL_H, CL_SILL, CL_SPRING, 1, (col * 0.25, 0, col * 0.25 + 0.25, 1))
            col = 2 + (i + (s < 0)) % 2
            window(stone, glass, 'x', s * (AISLE + OUTER_T / 2), s, yc, AW_H, AW_SILL, AW_SPRING, 0,
                   (col * 0.25, 0, col * 0.25 + 0.25, 1))
    make_obj('Tracery', stone, 'StoneTrim', coll, closed=False, uv_scale=1.5, smooth=70)
    make_obj('Glass', glass, 'Glass', coll, closed=False, smooth=0, uv_fn=lambda bm: None)

    stone, glass = MB(), MB()
    window(stone, glass, 'y', Y1 + END_T / 2, 1, 0.0, E_H, E_SILL, E_SPRING, 2, (0, 0, 1, 1))
    make_obj('EastTracery', stone, 'StoneTrim', coll, closed=False, uv_scale=1.5, smooth=70)
    make_obj('EastGlass', glass, 'GlassEast', coll, closed=False, smooth=0, uv_fn=lambda bm: None)

    stone, glass = MB(), MB()
    rose(stone, glass, Y0 - END_T / 2, -1)
    make_obj('RoseTracery', stone, 'StoneTrim', coll, closed=False, uv_scale=1.5, smooth=70)
    make_obj('RoseGlass', glass, 'GlassRose', coll, closed=False, smooth=0, uv_fn=lambda bm: None)


# ---------------------------------------------------------------- furnishings
PEW_ROWS = [-15.6 + k * 1.0 for k in range(25)]


def build_furniture(coll):
    wood = MB()
    rnd = random.Random(11)
    for y in PEW_ROWS:
        for s in (-1, 1):
            xa, xb = sorted((s * 1.0, s * 3.9))
            m = wood.mark()
            box(wood, xa + 0.05, xb - 0.05, y + 0.05, y + 0.48, 0.42, 0.47)        # seat
            box(wood, xa + 0.05, xb - 0.05, y, y + 0.05, 0.47, 0.97)               # back
            box(wood, xa + 0.05, xb - 0.05, y - 0.03, y + 0.08, 0.97, 1.01)        # top rail
            box(wood, xa + 0.05, xb - 0.05, y - 0.18, y, 0.76, 0.79)               # book shelf
            box(wood, xa + 0.05, xb - 0.05, y - 0.5, y - 0.36, 0.1, 0.17)          # kneeler
            box(wood, xa + 0.05, xb - 0.05, y + 0.02, y + 0.06, 0.05, 0.42)        # modesty board
            end = ([(y - 0.2, 0.0), (y + 0.5, 0.0), (y + 0.5, 0.9)]
                   + list(reversed(arch(y + 0.15, 0.35, 0.9, rise=0.32, n=8))))
            for xe in (xa, xb - 0.06):
                prism(wood, end, 'x', xe, xe + 0.06)
            # pews are never quite in line
            wood.transform_since(m, (xa + xb) / 2, y, math.radians(rnd.uniform(-0.7, 0.7)),
                                 rnd.uniform(-0.015, 0.015), rnd.uniform(-0.03, 0.03))
    # west doors
    door = [(-D_H, 0.0), (D_H, 0.0)] + list(reversed(arch(0.0, D_H, D_SPRING, n=16)))
    prism(wood, door, 'y', Y0 - 0.55, Y0 - 0.45)
    # rood: a great cross hanging over the chancel steps
    ry = CHANCEL_Y - 0.4
    box(wood, -0.14, 0.14, ry - 0.09, ry + 0.09, 8.6, 13.6)
    box(wood, -1.9, 1.9, ry - 0.09, ry + 0.09, 12.05, 12.33)
    for (cx, cz) in ((0, 13.75), (0, 8.45), (-2.05, 12.19), (2.05, 12.19)):
        lathe(wood, cx, ry, [(0.001, cz - 0.24), (0.2, cz - 0.1), (0.2, cz + 0.1), (0.001, cz + 0.24)], seg=4)
    make_obj('Pews', wood, 'Wood', coll, uv_scale=2.0, smooth=0,
             wear=dict(seed=9, step=0.3, chip=0.009, freq=4.0, thresh=0.3, bump=0.0015))

    iron = MB()
    for cx in (-1.7, 1.7):
        tube(iron, [(cx, ry, 12.33), (cx * 0.6, ry, V_SPRING + V_RISE * pointed(cx * 0.6 / NAVE) - 0.15)], 0.01, seg=4)
    for z in (0.6, 1.8, 3.0):
        box(iron, -D_H, D_H, Y0 - 0.45, Y0 - 0.42, z, z + 0.08)
    box(iron, -0.03, 0.03, Y0 - 0.45, Y0 - 0.4, 0.0, D_SPRING + D_H * SQ3 - 0.1)
    make_obj('DoorIron', iron, 'Iron', coll, smooth=0)

    cloth = MB()
    box(cloth, -0.75, 0.75, Y0 + 0.6, CHANCEL_Y, 0.0, 0.012)                       # runner
    box(cloth, -1.35, 1.35, 17.44, 17.47, CHANCEL_H + 0.12, CHANCEL_H + 1.06)       # frontal
    make_obj('Cloth', cloth, 'Cloth', coll, smooth=0)
    linen = MB()
    box(linen, -1.62, 1.62, 17.42, 18.48, CHANCEL_H + 1.1, CHANCEL_H + 1.115)
    make_obj('Linen', linen, 'Linen', coll, smooth=0)

    st = MB()
    ch = CHANCEL_H
    box(st, -1.5, 1.5, 17.5, 18.4, ch, ch + 1.0)                                  # altar
    box(st, -1.65, 1.65, 17.4, 18.5, ch + 1.0, ch + 1.1)                          # mensa
    # reredos: a screen of blind arches behind the altar
    box(st, -3.4, 3.4, 18.9, 19.4, ch, ch + 0.6)
    box(st, -3.4, 3.4, 19.0, 19.4, ch + 0.6, ch + 4.0)
    for k in range(7):
        c = -3.0 + k * 1.0
        tube(st, [(c - 0.5, 18.98, ch + 0.6), (c - 0.5, 18.98, ch + 3.0)], 0.06, 0.06, seg=6)
        tube(st, [(u, 18.98, v) for u, v in arch(c, 0.5, ch + 3.0, n=8)], 0.06, 0.06, seg=6)
        # gable over each niche
        gable = [(c - 0.5, ch + 4.0), (c + 0.5, ch + 4.0), (c, ch + 4.9)]
        prism(st, gable, 'y', 19.05, 19.35)
        lathe(st, c + 0.5, 19.2, [(0.08, ch + 3.6), (0.08, ch + 4.4), (0.001, ch + 5.0)], seg=4)
    tube(st, [(3.5, 18.98, ch + 0.6), (3.5, 18.98, ch + 3.0)], 0.06, 0.06, seg=6)
    lathe(st, -3.5, 19.2, [(0.08, ch + 3.6), (0.08, ch + 4.4), (0.001, ch + 5.0)], seg=4)
    # pulpit on the north side of the nave
    px, py = -3.75, 10.0
    lathe(st, px, py, [(0.25, 0.0), (0.18, 0.2), (0.16, 1.2), (0.72, 1.9), (0.72, 2.0)], seg=8, rot=math.pi / 8)
    lathe(st, px, py, [(0.72, 2.0), (0.72, 3.0), (0.78, 3.05), (0.78, 3.15)], seg=8, rot=math.pi / 8)
    # font near the west door
    lathe(st, 6.9, -16.5, [(0.55, 0.0), (0.55, 0.15), (0.25, 0.25), (0.22, 0.7), (0.6, 0.85), (0.62, 1.05),
                           (0.66, 1.1)], seg=8, rot=math.pi / 8)
    make_obj('AltarStone', st, 'StoneTrim', coll, uv_scale=1.5, smooth=40,
             wear=dict(seed=4, step=0.1, chip=0.022, freq=3.5, thresh=0.2))


# ---------------------------------------------------------------- candles
WAX_TINTS = [(1.0, 1.0, 1.0), (1.0, 0.95, 0.84), (1.0, 0.9, 0.74), (0.97, 0.97, 0.95)]


def puddle(mb, cx, cy, z, r, rnd, h=0.004, n=12):
    """An irregular pool of spilt wax."""
    rim = []
    for k in range(n):
        a = 2 * math.pi * k / n
        rr = r * rnd.uniform(0.7, 1.15)
        rim.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    top = mb.verts([(u, v, z + h * rnd.uniform(0.6, 1.0)) for u, v in rim])
    bot = mb.verts([(u, v, z + 0.0005) for u, v in rim])
    c = mb.bm.verts.new((cx, cy, z + h * 1.4))
    for k in range(n):
        k2 = (k + 1) % n
        mb.face([c, top[k], top[k2]])
        mb.face([top[k], bot[k], bot[k2], top[k2]])


def candle(wax, wick, x, y, z, h, r, rnd, state='lit', pool=0.7):
    """A used candle: melted crater, drips, a wax pool and a wick. Returns the flame position or None."""
    m = wax.mark()
    if state == 'out':
        h = rnd.uniform(0.012, 0.03)
    top = z + h
    lathe(wax, x, y, [(r * 1.02, z), (r, z + h * 0.5), (r * 0.99, top - 0.006), (r * 1.015, top - 0.0015),
                      (r * 0.9, top), (r * 0.55, top - 0.007), (r * 0.12, top - 0.009)], seg=12)
    for _ in range(rnd.randint(1, 4) if h > 0.03 else rnd.randint(0, 2)):
        a = rnd.uniform(0, 2 * math.pi)
        ln = rnd.uniform(0.2, 0.9) * h
        ca, sa = math.cos(a), math.sin(a)
        p0 = Vector((x + r * 0.95 * ca, y + r * 0.95 * sa, top - 0.002))
        p2 = Vector((x + (r + 0.0025) * ca, y + (r + 0.0025) * sa, top - ln))
        tube(wax, [p0, (p0 + p2) / 2 + Vector((ca, sa, 0)) * 0.0015, p2], rnd.uniform(0.003, 0.0055), seg=5)
        lathe(wax, p2.x, p2.y, [(0.001, p2.z - 0.007), (0.0065, p2.z - 0.003), (0.004, p2.z + 0.004)], seg=6)
    if state == 'out' or rnd.random() < pool:
        puddle(wax, x, y, z, r * rnd.uniform(1.4, 2.3), rnd)
    wax.paint(m, rnd.choice(WAX_TINTS))
    cyl(wick, x, y, top - 0.009, top + (0.004 if state == 'out' else 0.011), 0.0013, seg=4)
    return None if state == 'out' else (x, y, top + 0.03)


def candlestick(gold, wax, wick, x, y, z, h, ch, rnd, state='lit'):
    lathe(gold, x, y, [(0.11, z), (0.11, z + 0.02), (0.05, z + 0.06), (0.022, z + 0.1), (0.022, z + h - 0.06),
                       (0.04, z + h - 0.04), (0.065, z + h), (0.065, z + h + 0.012)], seg=12)
    return candle(wax, wick, x, y, z + h + 0.012, ch, 0.03, rnd, state)


def burn(rnd, hmin, hmax, p_out=0.0, p_stub=0.0):
    """How far a candle has burnt: (state, height)."""
    r = rnd.random()
    if r < p_out:
        return 'out', 0.0
    if r < p_out + p_stub:
        return 'lit', rnd.uniform(0.025, 0.07)
    return 'lit', rnd.uniform(hmin, hmax)


def build_candles(coll):
    gold, iron, wax = MB(), MB(), MB()
    rnd = random.Random(23)
    lights = []          # (position, power W, kind)

    def add(p, w, kind):
        if p:
            lights.append((p, w, kind))

    ch = CHANCEL_H
    # altar cross and candlesticks
    box(gold, -0.035, 0.035, 18.15, 18.22, ch + 1.115, ch + 2.35)
    box(gold, -0.32, 0.32, 18.15, 18.22, ch + 1.88, ch + 1.95)
    lathe(gold, 0, 18.185, [(0.16, ch + 1.115), (0.12, ch + 1.18), (0.05, ch + 1.25)], seg=12)
    for k, x in enumerate((-1.35, -0.95, -0.55, 0.55, 0.95, 1.35)):
        hh = 0.45 + 0.12 * (2 - min(k, 5 - k))
        state, h = burn(rnd, 0.12, 0.36, p_stub=0.2) if k != 4 else ('lit', 0.05)
        add(candlestick(gold, wax, iron, x, 18.1, ch + 1.115, hh, h, rnd, state), 3.2, 'altar')
    # two great standing candelabra before the chancel steps
    for s in (-1, 1):
        cx, cy = s * 1.65, 10.5
        lathe(iron, cx, cy, [(0.32, 0.0), (0.32, 0.04), (0.1, 0.12), (0.04, 0.25)], seg=12)
        cyl(iron, cx, cy, 0.0, 1.62, 0.028, seg=8)
        tube(iron, [(cx - 0.55, cy, 1.62), (cx + 0.55, cy, 1.62)], 0.022, seg=6)
        for k, dx in enumerate((-0.55, -0.27, 0.0, 0.27, 0.55)):
            top = 1.62 + 0.18 * (1 - abs(dx) / 0.55) + 0.08
            cyl(iron, cx + dx, cy, 1.6, top, 0.018, seg=6)
            lathe(iron, cx + dx, cy, [(0.06, top), (0.06, top + 0.02), (0.03, top + 0.04)], seg=10)
            state, h = burn(rnd, 0.08, 0.3, p_out=0.15, p_stub=0.2)
            add(candle(wax, iron, cx + dx, cy, top + 0.02, h, 0.026, rnd, state), 2.2, 'stand')
    # hanging iron coronas over the nave
    for cy in (-12.5, -2.5, 7.5):
        z = 6.4
        R = 1.25
        tube(iron, [(R * math.cos(t * math.pi / 24), cy + R * math.sin(t * math.pi / 24), z) for t in range(48)],
             0.035, 0.05, seg=6, closed=True)
        tube(iron, [(R * 0.82 * math.cos(t * math.pi / 24), cy + R * 0.82 * math.sin(t * math.pi / 24), z + 0.12) for t in range(48)],
             0.02, 0.02, seg=5, closed=True)
        hub = (0, cy, z + 2.2)
        for k in range(4):
            t = k * math.pi / 2 + math.pi / 4
            tube(iron, [(R * math.cos(t), cy + R * math.sin(t), z), hub], 0.008, seg=4)
        tube(iron, [hub, (0, cy, V_SPRING + V_RISE - 0.2)], 0.012, seg=4)
        lathe(iron, 0, cy, [(0.1, z + 2.15), (0.1, z + 2.25)], seg=8)
        for k in range(10):
            t = k * math.pi / 5
            x, y = R * math.cos(t), cy + R * math.sin(t)
            lathe(iron, x, y, [(0.055, z + 0.03), (0.055, z + 0.05), (0.02, z + 0.07)], seg=8)
            state, h = burn(rnd, 0.06, 0.24, p_out=0.25, p_stub=0.25)
            add(candle(wax, iron, x, y, z + 0.05, h, 0.025, rnd, state, pool=0.4), 1.8, 'corona')
    # pricket stands in the aisles
    for (x, y), h in zip(((-8.6, -12.5), (8.6, -7.5), (-8.6, 2.5), (8.6, 7.5)), (0.44, 0.09, 0.27, 0.04)):
        lathe(iron, x, y, [(0.25, 0.0), (0.25, 0.03), (0.06, 0.1), (0.025, 0.2)], seg=10)
        cyl(iron, x, y, 0.0, 1.25, 0.022, seg=8)
        lathe(iron, x, y, [(0.13, 1.25), (0.13, 1.28), (0.05, 1.3)], seg=12)
        puddle(wax, x, y, 1.28, 0.1, rnd, h=0.006)
        add(candle(wax, iron, x, y, 1.285, h, 0.04, rnd), 2.4, 'pricket')
    # votive rack in the north aisle: a third of them have burnt out
    vx, vy = -8.55, -2.5
    for t in range(3):
        z = 0.75 + t * 0.18
        xo = vx + 0.25 - t * 0.2
        box(iron, xo - 0.12, xo + 0.12, vy - 1.0, vy + 1.0, z - 0.02, z)
        for k in range(9):
            yy = vy - 0.88 + k * 0.22
            state, h = burn(rnd, 0.03, 0.07, p_out=0.35)
            add(candle(wax, iron, xo, yy, z, h, 0.024, rnd, state, pool=0.5), 0.6, 'votive')
    for dy in (-1.0, 1.0):
        box(iron, vx - 0.25, vx + 0.4, vy + dy - 0.02, vy + dy, 0.0, 1.15)
    make_obj('Gold', gold, 'Gold', coll, smooth=40)
    make_obj('Iron', iron, 'Iron', coll, closed=False, smooth=40)
    make_obj('Wax', wax, 'Wax', coll, closed=False, smooth=40)
    return lights


def build_flames(coll, lights):
    """Small emissive flame shapes; three.js replaces them with flickering sprites."""
    mb = MB()
    for p, w, kind in lights:
        s = 0.6 if kind == 'votive' else 1.0
        x, y, z = p
        lathe(mb, x, y, [(0.004 * s, z - 0.025 * s), (0.011 * s, z - 0.012 * s), (0.012 * s, z),
                         (0.008 * s, z + 0.02 * s), (0.001, z + 0.045 * s)], seg=8)
    make_obj('Flames', mb, 'Flame', coll, smooth=80)


def build_lights(coll, lights):
    for i, (p, w, kind) in enumerate(lights):
        ld = bpy.data.lights.new(f'Candle_{kind}_{i:02d}', 'POINT')
        ld.energy = w
        ld.color = (1.0, 0.52, 0.2)
        ld.shadow_soft_size = 0.02
        ob = bpy.data.objects.new(ld.name, ld)
        ob.location = (p[0], p[1], p[2] + 0.01)
        coll.objects.link(ob)
    # moonlight through the north windows
    ld = bpy.data.lights.new('Moon', 'SUN')
    ld.energy = 1.2
    ld.color = (0.55, 0.68, 1.0)
    ld.angle = math.radians(0.6)
    ob = bpy.data.objects.new('Moon', ld)
    d = Vector((0.62, 0.32, -0.72)).normalized()
    ob.rotation_euler = d.to_track_quat('-Z', 'Y').to_euler()
    coll.objects.link(ob)


def build(parts=('vaults', 'piers', 'walls', 'floor', 'windows', 'furniture', 'candles', 'lights')):
    coll = reset()
    if 'vaults' in parts: build_vaults(coll)
    if 'piers' in parts: build_piers(coll)
    if 'walls' in parts: build_walls(coll)
    if 'floor' in parts: build_floor(coll)
    if 'walls' in parts: build_roof(coll)
    if 'windows' in parts: build_windows(coll)
    if 'furniture' in parts: build_furniture(coll)
    lights = build_candles(coll) if 'candles' in parts else []
    if lights:
        build_flames(coll, lights)
    if 'lights' in parts:
        build_lights(coll, lights)
    return coll, lights


if __name__ == '__main__':
    build()
