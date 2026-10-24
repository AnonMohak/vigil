# materials.py - stained-glass texture generator, materials, world and render settings.
# Run inside Blender after build_church.py:  exec(open(PATH).read())
import bpy, os, math
import numpy as np

ROOT = r"C:\Users\mohak\projects\cc-no-404\gothic\blender"
TEX = os.path.join(ROOT, 'textures')

PAL = {
    'blue':   [(0.08, 0.20, 0.62), (0.05, 0.13, 0.50), (0.13, 0.30, 0.72), (0.04, 0.10, 0.38)],
    'red':    [(0.62, 0.05, 0.07), (0.48, 0.03, 0.08), (0.72, 0.12, 0.06)],
    'amber':  [(0.88, 0.58, 0.12), (0.78, 0.42, 0.08)],
    'green':  [(0.12, 0.42, 0.20), (0.22, 0.50, 0.14)],
    'white':  [(0.84, 0.82, 0.70), (0.72, 0.74, 0.68)],
    'purple': [(0.38, 0.12, 0.44)],
}
REGION_MIX = {
    0: [('blue', 0.93), ('red', 0.07)],                                             # background diaper
    1: [('red', 0.62), ('blue', 0.18), ('amber', 0.2)],                             # border
    2: [('red', 0.85), ('blue', 0.15)],                                             # medallion ground
    3: [('white', 0.6), ('amber', 0.4)],                                            # medallion pearls
    4: [('blue', 0.6), ('red', 0.3), ('amber', 0.1)],                               # window head
    5: [('amber', 0.5), ('white', 0.5)],                                            # rose centre
    6: [('white', 0.32), ('amber', 0.3), ('green', 0.22), ('purple', 0.16)],        # figures
}


# ---------------------------------------------------------------- stained glass
def voronoi(w, h, cell, seed):
    rng = np.random.default_rng(seed)
    gx, gy = w // cell + 3, h // cell + 3
    jit = rng.uniform(0.1, 0.9, (gy, gx, 2))
    gxx, gyy = np.meshgrid(np.arange(gx), np.arange(gy))
    px = (gxx + jit[..., 0] - 1) * cell
    py = (gyy + jit[..., 1] - 1) * cell
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    ci = (xs // cell).astype(np.int32) + 1
    cj = (ys // cell).astype(np.int32) + 1
    d1 = np.full((h, w), 1e9, np.float32)
    d2 = np.full((h, w), 1e9, np.float32)
    idx = np.zeros((h, w), np.int32)
    for dj in (-1, 0, 1):
        for di in (-1, 0, 1):
            jj = np.clip(cj + dj, 0, gy - 1)
            ii = np.clip(ci + di, 0, gx - 1)
            d = np.hypot(xs - px[jj, ii], ys - py[jj, ii])
            m = d < d1
            d2 = np.where(m, d1, np.minimum(d2, d))
            idx = np.where(m, jj * gx + ii, idx)
            d1 = np.where(m, d, d1)
    return idx, d1, d2, xs, ys, gx * gy


def colorize(reg, idx, d1, d2, lead, ncell, cell, seed, xs, ys):
    rng = np.random.default_rng(seed + 101)
    pick = rng.random(ncell)
    shade = rng.uniform(0.62, 1.12, ncell)
    var = rng.integers(0, 4, ncell)
    # figures: groups of cells inside medallions, picked by a smooth field at each cell centre
    cnt = np.bincount(idx.ravel(), minlength=ncell).astype(np.float32) + 1e-6
    sx = np.bincount(idx.ravel(), xs.ravel(), ncell) / cnt
    sy = np.bincount(idx.ravel(), ys.ravel(), ncell) / cnt
    fig = (np.sin(sx * 0.045 + seed) * np.sin(sy * 0.038 + 2 * seed) + 0.5 * np.sin((sx + sy) * 0.07 + seed)) > 0.2
    reg = reg.copy()
    reg[(reg == 2) & fig[idx]] = 6
    out = np.zeros(reg.shape + (3,), np.float32)
    for r, mix in REGION_MIX.items():
        m = reg == r
        if not m.any():
            continue
        p = pick[idx[m]]
        acc = 0.0
        col = np.zeros((m.sum(), 3), np.float32)
        for name, wgt in mix:
            sel = (p >= acc) if name == mix[-1][0] else (p >= acc) & (p < acc + wgt)
            pal = np.array(PAL[name], np.float32)
            col[sel] = pal[var[idx[m]][sel] % len(pal)]
            acc += wgt
        out[m] = col
    edge = np.clip((d2 - d1) / (cell * 0.45), 0, 1)
    f = shade[idx] * (0.7 + 0.3 * edge)
    # grisaille: painted dark strokes inside the figure panels
    strokes = np.abs(np.sin(xs * 0.11 + 3.0 * np.sin(ys * 0.035 + seed))) < 0.07
    strokes &= (reg == 6) & (pick[idx] < 0.6)
    f = np.where(strokes, f * 0.35, f)
    noise = np.random.default_rng(seed + 7).normal(1.0, 0.04, reg.shape).astype(np.float32)
    out *= (f * noise)[..., None]
    out[lead] = (0.015, 0.013, 0.012)
    return np.clip(out, 0, 1)


def design_lancet(w, h, seed, cols=1, rows=3, spring_v=0.7, cell=22):
    idx, d1, d2, xs, ys, ncell = voronoi(w, h, cell, seed)
    lw = w / cols / 256 * 1.6
    lightw = w / cols
    lx = xs % lightw
    li = xs // lightw
    bw = lightw * 0.11
    sh = spring_v * h
    reg = np.zeros((h, w), np.int8)
    lead = (d2 - d1) < lw * 1.5
    border = (lx < bw) | (lx > lightw - bw)
    reg[border] = 1
    lead |= (np.abs(lx - bw) < lw) | (np.abs(lx - (lightw - bw)) < lw)
    rad = lightw * 0.34
    for j in range(rows):
        cy = (j + 0.55) / rows * sh
        d = np.hypot(xs - (li + 0.5) * lightw, ys - cy)
        rim = (d >= rad) & (d < rad * 1.14)
        reg[rim & ~border] = 3
        reg[d < rad] = 2
        lead |= (np.abs(d - rad) < lw) | (np.abs(d - rad * 1.14) < lw)
    reg[(ys > sh) & ~border] = 4
    for k in range(1, 14):
        lead |= np.abs(ys - k * h / 14) < lw * 1.3       # iron saddle bars
    return colorize(reg, idx, d1, d2, lead, ncell, cell, seed, xs, ys)


def design_rose(n, seed):
    cell = 18
    idx, d1, d2, xs, ys, ncell = voronoi(n, n, cell, seed)
    u = (xs + 0.5) / n * 2 - 1
    v = (ys + 0.5) / n * 2 - 1
    r = np.hypot(u, v)
    th = np.arctan2(v, u)
    sector = np.floor((th + np.pi) / (np.pi / 6)).astype(np.int32)
    lw = 1.7
    reg = np.zeros((n, n), np.int8)
    reg[r < 0.3] = np.where(sector[r < 0.3] % 2 == 0, 4, 0)
    reg[r < 0.12] = 5
    petal = (r >= 0.3) & (r < 0.66)
    reg[petal] = 0
    # a figure medallion in every petal
    for k in range(12):
        t = -np.pi + (k + 0.5) * np.pi / 6
        d = np.hypot(u - 0.48 * np.cos(t), v - 0.48 * np.sin(t))
        reg[d < 0.11] = 2
        reg[(d >= 0.11) & (d < 0.13)] = 3
    reg[(r >= 0.66) & (r < 0.8)] = 4
    reg[r >= 0.8] = 1
    lead = (d2 - d1) < lw * 1.5
    for rr in (0.12, 0.3, 0.66, 0.8):
        lead |= np.abs(r - rr) * n / 2 < lw
    frac = (th + np.pi) / (np.pi / 6) - sector
    lead |= (np.minimum(frac, 1 - frac) * (np.pi / 6) * r * n / 2 < lw) & (r > 0.12)
    return colorize(reg, idx, d1, d2, lead, ncell, cell, seed, xs, ys)


def save_png(name, rgb):
    h, w, _ = rgb.shape
    im = bpy.data.images.get(name)
    if im is None or tuple(im.size) != (w, h):
        if im:
            bpy.data.images.remove(im)
        im = bpy.data.images.new(name, w, h, alpha=False)
    rgba = np.ones((h, w, 4), np.float32)
    rgba[..., :3] = rgb
    im.pixels.foreach_set(rgba.ravel())
    path = os.path.join(TEX, name + '.png')
    im.filepath_raw = path
    im.file_format = 'PNG'
    im.save()
    return path


def make_damage_mask():
    """One cell per window (see build_church.dmg_rect): 1 = glass still in the lead, 0 = knocked out."""
    import json
    ruin = json.loads(bpy.context.scene.get('ruin', '{"windows": []}'))
    W, H, cols, rows = 2048, 2048, 8, 5
    cw, ch = W // cols, H // rows
    idx, d1, d2, xs, ys, ncell = voronoi(W, H, 11, 91)
    cnt = np.bincount(idx.ravel(), minlength=ncell).astype(np.float32) + 1e-6
    sx = np.bincount(idx.ravel(), xs.ravel(), ncell) / cnt
    sy = np.bincount(idx.ravel(), ys.ravel(), ncell) / cnt
    rng = np.random.default_rng(5)
    keep = np.ones((H, W), np.float32)
    for w in ruin['windows']:
        lvl = w['level']
        if lvl <= 0:
            continue
        c, r = w['cell'] % cols, w['cell'] // cols
        x0, y0 = c * cw, r * ch
        # where it was hit: low down for the lancets (stones thrown from outside), anywhere for the others
        ix = x0 + cw * rng.uniform(0.25, 0.75)
        iy = y0 + ch * (rng.uniform(0.08, 0.45) if w['kind'] == 'lancet' else rng.uniform(0.15, 0.85))
        d = np.hypot(sx - ix, (sy - iy) * 0.8) / ch
        p = lvl * 1.9 * np.exp(-d / 0.3) + lvl * 0.25
        holes = rng.random(ncell) < p
        region = (slice(y0, y0 + ch), slice(x0, x0 + cw))
        keep[region][holes[idx[region]]] = 0.0
    rgb = np.repeat(keep[..., None], 3, axis=2)
    return save_png('glass_damage', rgb)


def make_glass_textures():
    cols = [design_lancet(512, 1536, 11, 2, 3, 0.71),
            design_lancet(512, 1536, 23, 2, 3, 0.71),
            design_lancet(512, 1536, 37, 1, 3, 0.72),
            design_lancet(512, 1536, 41, 1, 3, 0.72)]
    save_png('glass_lancets', np.concatenate(cols, axis=1))
    save_png('glass_east', design_lancet(1024, 2048, 53, 4, 4, 0.64, cell=24))
    save_png('glass_rose', design_rose(1024, 67))


# ---------------------------------------------------------------- materials
def image(file, colorspace='sRGB'):
    path = os.path.join(TEX, file)
    im = next((i for i in bpy.data.images if bpy.path.abspath(i.filepath) == path), None)
    if im is None:
        im = bpy.data.images.load(path)
    else:
        im.reload()
    im.colorspace_settings.name = colorspace
    return im


def fresh(name):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    return m, nt, out


def tex(nt, file, colorspace='sRGB', uv='UVMap'):
    uvn = nt.nodes.new('ShaderNodeUVMap')
    uvn.uv_map = uv
    t = nt.nodes.new('ShaderNodeTexImage')
    t.image = image(file, colorspace)
    nt.links.new(uvn.outputs[0], t.inputs[0])
    return t


def vertex_tint(nt, socket):
    """Multiply whatever feeds `socket` by the 'Col' colour attribute."""
    vc = nt.nodes.new('ShaderNodeVertexColor')
    vc.layer_name = 'Col'
    mul = nt.nodes.new('ShaderNodeVectorMath')
    mul.operation = 'MULTIPLY'
    link = socket.links[0] if socket.links else None
    if link:
        nt.links.new(link.from_socket, mul.inputs[0])
    else:
        mul.inputs[0].default_value = socket.default_value[:3]
    nt.links.new(vc.outputs['Color'], mul.inputs[1])
    nt.links.new(mul.outputs[0], socket)


def pbr(name, base, rough=None, normal=None, tint=(1, 1, 1), rough_mul=1.0, nstr=1.0, metallic=0.0, vc=False):
    m, nt, out = fresh(name)
    b = nt.nodes.new('ShaderNodeBsdfPrincipled')
    nt.links.new(b.outputs[0], out.inputs[0])
    t = tex(nt, base)
    mul = nt.nodes.new('ShaderNodeVectorMath')
    mul.operation = 'MULTIPLY'
    mul.inputs[1].default_value = tint
    nt.links.new(t.outputs[0], mul.inputs[0])
    nt.links.new(mul.outputs[0], b.inputs['Base Color'])
    if vc:
        vertex_tint(nt, b.inputs['Base Color'])
    if rough:
        r = tex(nt, rough, 'Non-Color')
        mm = nt.nodes.new('ShaderNodeMath')
        mm.operation = 'MULTIPLY'
        mm.inputs[1].default_value = rough_mul
        nt.links.new(r.outputs[0], mm.inputs[0])
        nt.links.new(mm.outputs[0], b.inputs['Roughness'])
    if normal:
        n = tex(nt, normal, 'Non-Color')
        nm = nt.nodes.new('ShaderNodeNormalMap')
        nm.inputs['Strength'].default_value = nstr
        nt.links.new(n.outputs[0], nm.inputs['Color'])
        nt.links.new(nm.outputs[0], b.inputs['Normal'])
    b.inputs['Metallic'].default_value = metallic
    return m


def flat(name, color, rough=0.5, metallic=0.0, sheen=0.0, emit=None, strength=0.0):
    m, nt, out = fresh(name)
    b = nt.nodes.new('ShaderNodeBsdfPrincipled')
    nt.links.new(b.outputs[0], out.inputs[0])
    b.inputs['Base Color'].default_value = (*color, 1)
    b.inputs['Roughness'].default_value = rough
    b.inputs['Metallic'].default_value = metallic
    if sheen:
        b.inputs['Sheen Weight'].default_value = sheen
    if emit:
        b.inputs['Emission Color'].default_value = (*emit, 1)
        b.inputs['Emission Strength'].default_value = strength
    return m


def glass(name, file, strength):
    """Stained glass: lets coloured moonlight through and glows a little."""
    m, nt, out = fresh(name)
    t = tex(nt, file)
    tr = nt.nodes.new('ShaderNodeBsdfTransparent')
    em = nt.nodes.new('ShaderNodeEmission')
    em.inputs['Strength'].default_value = strength
    add = nt.nodes.new('ShaderNodeAddShader')
    nt.links.new(t.outputs[0], tr.inputs['Color'])
    nt.links.new(t.outputs[0], em.inputs['Color'])
    nt.links.new(tr.outputs[0], add.inputs[0])
    nt.links.new(em.outputs[0], add.inputs[1])
    # broken panes: where the damage mask is 0 there is no glass at all, and the moonlight comes in white
    mask = tex(nt, 'glass_damage.png', 'Non-Color', uv='Damage')
    hole = nt.nodes.new('ShaderNodeBsdfTransparent')
    mix = nt.nodes.new('ShaderNodeMixShader')
    nt.links.new(mask.outputs[0], mix.inputs[0])
    nt.links.new(hole.outputs[0], mix.inputs[1])
    nt.links.new(add.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], out.inputs[0])
    try:
        m.surface_render_method = 'BLENDED'
    except Exception:
        pass
    return m


def flame():
    m, nt, out = fresh('Flame')
    em = nt.nodes.new('ShaderNodeEmission')
    em.inputs['Color'].default_value = (1.0, 0.52, 0.16, 1)
    em.inputs['Strength'].default_value = 60.0
    nt.links.new(em.outputs[0], out.inputs[0])
    return m


def setup_materials(glass_strength=2.0):
    pbr('Stone', 'medieval_blocks_03_Diffuse.jpg', 'medieval_blocks_03_Rough.jpg', 'medieval_blocks_03_nor_gl.jpg',
        tint=(0.62, 0.6, 0.58), nstr=1.2)
    pbr('StoneTrim', 'rock_01_Diffuse.jpg', 'rock_01_Rough.jpg', 'rock_01_nor_gl.jpg', tint=(1.0, 0.95, 0.88), nstr=0.8)
    pbr('Floor', 'floor_tiles_04_Diffuse.jpg', 'floor_tiles_04_Rough.jpg', 'floor_tiles_04_nor_gl.jpg',
        tint=(0.38, 0.36, 0.34), rough_mul=0.75, vc=True)
    pbr('Wood', 'dark_wooden_planks_Diffuse.jpg', 'dark_wooden_planks_Rough.jpg', 'dark_wooden_planks_nor_gl.jpg',
        tint=(0.75, 0.6, 0.5), rough_mul=0.7)
    pbr('Iron', 'rust_coarse_01_Diffuse.jpg', 'rust_coarse_01_Rough.jpg', 'rust_coarse_01_nor_gl.jpg',
        tint=(0.45, 0.4, 0.38), nstr=1.3, metallic=0.3)
    flat('Gold', (0.75, 0.52, 0.2), rough=0.3, metallic=1.0)
    m = flat('Wax', (0.8, 0.72, 0.56), rough=0.45)
    vertex_tint(m.node_tree, next(n for n in m.node_tree.nodes if n.type == 'BSDF_PRINCIPLED').inputs['Base Color'])
    flat('Mortar', (0.05, 0.045, 0.04), rough=0.9)
    m = flat('Shard', (0.7, 0.7, 0.7), rough=0.12)
    vertex_tint(m.node_tree, next(n for n in m.node_tree.nodes if n.type == 'BSDF_PRINCIPLED').inputs['Base Color'])
    flat('Web', (0.6, 0.6, 0.6), rough=0.9)
    flat('Roof', (0.05, 0.05, 0.05), rough=0.9)
    flat('Cloth', (0.16, 0.008, 0.012), rough=0.85, sheen=0.6)
    flat('Linen', (0.62, 0.6, 0.55), rough=0.8)
    flame()
    glass('Glass', 'glass_lancets.png', glass_strength)
    glass('GlassEast', 'glass_east.png', glass_strength)
    glass('GlassRose', 'glass_rose.png', glass_strength)


def setup_world(color=(0.006, 0.009, 0.02)):
    w = bpy.context.scene.world or bpy.data.worlds.new('World')
    bpy.context.scene.world = w
    w.use_nodes = True
    bg = next(n for n in w.node_tree.nodes if n.type == 'BACKGROUND')
    bg.inputs['Color'].default_value = (*color, 1)
    bg.inputs['Strength'].default_value = 1.0


def setup_render(samples=96, res=(1280, 720), exposure=0.0):
    sc = bpy.context.scene
    try:
        sc.render.engine = 'CYCLES'
    except TypeError as e:
        print('engine', e)
    prefs = bpy.context.preferences.addons['cycles'].preferences
    prefs.compute_device_type = 'CUDA'
    prefs.refresh_devices()
    for d in prefs.devices:
        d.use = d.type == 'CUDA'
    sc.cycles.device = 'GPU'
    sc.cycles.samples = samples
    sc.cycles.use_denoising = True
    sc.cycles.max_bounces = 6
    sc.cycles.transparent_max_bounces = 8
    sc.render.resolution_x, sc.render.resolution_y = res
    sc.render.resolution_percentage = 100
    sc.view_settings.view_transform = 'AgX'
    try:
        sc.view_settings.look = 'AgX - Medium High Contrast'
    except TypeError:
        pass
    sc.view_settings.exposure = exposure


def setup_haze(density=0.016):
    """Render-only volume for god rays; hidden while baking."""
    import bmesh
    ob = bpy.data.objects.get('Haze')
    if ob is None:
        me = bpy.data.meshes.new('Haze')
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        bm.to_mesh(me)
        bm.free()
        ob = bpy.data.objects.new('Haze', me)
        bpy.data.collections['Church'].objects.link(ob)
    ob.location = (0, 0, 12.2)
    ob.scale = (19.0, 40.0, 24.3)
    m = bpy.data.materials.get('HazeVol') or bpy.data.materials.new('HazeVol')
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    pv = nt.nodes.new('ShaderNodeVolumePrincipled')
    pv.inputs['Density'].default_value = density
    pv.inputs['Anisotropy'].default_value = 0.45
    pv.inputs['Color'].default_value = (0.8, 0.82, 0.9, 1)
    nt.links.new(pv.outputs[0], out.inputs['Volume'])
    ob.data.materials.clear()
    ob.data.materials.append(m)
    ob.visible_shadow = False


def finish_objects():
    fl = bpy.data.objects.get('Flames')
    if fl:
        fl.visible_shadow = False
    bpy.data.lights['Moon'].energy = 8.0
    el = math.radians(58)
    hx, hy = 0.92, 0.39
    n = math.hypot(hx, hy)
    from mathutils import Vector
    d = Vector((math.cos(el) * hx / n, math.cos(el) * hy / n, -math.sin(el)))
    bpy.data.objects['Moon'].rotation_euler = d.to_track_quat('-Z', 'Y').to_euler()
    for name in ('Glass', 'GlassEast', 'GlassRose'):
        m = bpy.data.materials[name]
        next(x for x in m.node_tree.nodes if x.type == 'EMISSION').inputs['Strength'].default_value = 0.7
    c = bpy.data.materials['Cloth']
    next(x for x in c.node_tree.nodes if x.type == 'BSDF_PRINCIPLED').inputs['Base Color'].default_value = (0.07, 0.004, 0.006, 1)


if __name__ == '__main__':
    os.makedirs(TEX, exist_ok=True)
    if not os.path.exists(os.path.join(TEX, 'glass_rose.png')):
        make_glass_textures()
    make_damage_mask()
    setup_materials()
    setup_world()
    setup_haze()
    finish_objects()
