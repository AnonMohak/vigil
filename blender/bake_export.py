# bake_export.py - lightmap UVs, Cycles lightmap bakes and glTF export for the three.js viewer.
# Run inside Blender after build_church.py and materials.py.
import bpy, os, math, json
import numpy as np

ROOT = r"C:\Users\mohak\projects\cc-no-404\gothic"
LM_DIR = os.path.join(ROOT, 'blender', 'lightmaps')
WEB = os.path.join(ROOT, 'public', 'assets')

# object -> lightmap resolution
LIGHTMAPS = {
    'Floor': (2048, 1024), 'Walls': 2048, 'Vaults': 2048, 'Piers': 2048, 'Ribs': 2048, 'Pews': 2048,
    'Mouldings': 1024, 'Tracery': 1024, 'AltarStone': 1024, 'Chancel': 1024, 'Bench': 1024, 'EastTracery': 512,
    'RoseTracery': 512, 'Cloth': 512, 'Iron': 1024, 'Linen': 256, 'Gold': 256, 'Wax': 1024, 'DoorIron': 128,
    'Rubble': 1024, 'Shards': 256,
}
UNLIT = ('Glass', 'EastGlass', 'RoseGlass', 'Mortar')
FLOOR_RECT = (-9.5, 9.5, -20.0, 20.0)     # planar lightmap for the floor slabs: u along y, v along x
LM_K = 0.5            # lightmaps store sRGB(L / (L + LM_K)); main.js decodes them


def select_only(ob):
    bpy.context.view_layer.update()
    for o in bpy.context.view_layer.objects:
        if o is not None:
            o.select_set(False)
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob


def lightmap_uv(name, margin=0.004):
    ob = bpy.data.objects[name]
    me = ob.data
    if 'Lightmap' not in me.uv_layers:
        me.uv_layers.new(name='Lightmap')
    me.uv_layers['UVMap'].active_render = True
    me.uv_layers.active = me.uv_layers['Lightmap']
    if name == 'Floor':
        # one seamless top-down projection; slab sides are a few mm tall and share their edge texels
        x0, x1, y0, y1 = FLOOR_RECT
        uv = me.uv_layers['Lightmap'].data
        for loop in me.loops:
            co = me.vertices[loop.vertex_index].co
            uv[loop.index].uv = ((co.y - y0) / (y1 - y0), (co.x - x0) / (x1 - x0))
        return
    select_only(ob)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.smart_project(angle_limit=math.radians(60), island_margin=margin, area_weight=0.0,
                             correct_aspect=True, scale_to_bounds=False)
    bpy.ops.object.mode_set(mode='OBJECT')


def _normal_maps(strength):
    for m in bpy.data.materials:
        if m.node_tree:
            for n in m.node_tree.nodes:
                if n.type == 'NORMAL_MAP':
                    n.inputs['Strength'].default_value = strength if strength is not None else n.get('orig', 1.0)


def bake(name, samples=256, margin=8):
    ob = bpy.data.objects[name]
    res = LIGHTMAPS[name]
    res = res if isinstance(res, tuple) else (res, res)
    me = ob.data
    me.uv_layers.active = me.uv_layers['Lightmap']
    img = bpy.data.images.get('LM_' + name)
    if img is None or tuple(img.size) != res:
        if img:
            bpy.data.images.remove(img)
        img = bpy.data.images.new('LM_' + name, res[0], res[1], alpha=False, float_buffer=True)
    img.colorspace_settings.name = 'Linear Rec.709'
    added = []
    metals = []           # metals have no diffuse closure, so bake them as dielectrics
    for slot in ob.material_slots:
        nt = slot.material.node_tree
        for b in nt.nodes:
            if b.type == 'BSDF_PRINCIPLED' and b.inputs['Metallic'].default_value > 0:
                metals.append((b, b.inputs['Metallic'].default_value))
                b.inputs['Metallic'].default_value = 0.0
        n = nt.nodes.new('ShaderNodeTexImage')
        n.name = '__bake__'
        n.image = img
        nt.nodes.active = n
        added.append((nt, n))
    sc = bpy.context.scene
    sc.cycles.samples = samples
    haze = bpy.data.objects.get('Haze')
    if haze:
        haze.hide_render = True
    for m in bpy.data.materials:
        if m.node_tree:
            for n in m.node_tree.nodes:
                if n.type == 'NORMAL_MAP':
                    n['orig'] = n.inputs['Strength'].default_value
                    n.inputs['Strength'].default_value = 0.0
    select_only(ob)
    try:
        bpy.ops.object.bake(type='DIFFUSE', pass_filter={'DIRECT', 'INDIRECT'}, margin=margin,
                            margin_type='EXTEND', use_clear=True)
    finally:
        _normal_maps(None)
        for b, v in metals:
            b.inputs['Metallic'].default_value = v
        for nt, n in added:
            nt.nodes.remove(n)
        if haze:
            haze.hide_render = False
        me.uv_layers.active = me.uv_layers['UVMap']
    return img


def denoise(img, tmp):
    """Run OIDN on a baked image through the compositor (Workbench render, no scene cost)."""
    sc = bpy.context.scene
    tree = bpy.data.node_groups.get('LMDenoise')
    if tree is None:
        tree = bpy.data.node_groups.new('LMDenoise', 'CompositorNodeTree')
        tree.interface.new_socket('Image', in_out='OUTPUT', socket_type='NodeSocketColor')
        src = tree.nodes.new('CompositorNodeImage')
        src.name = 'src'
        dn = tree.nodes.new('CompositorNodeDenoise')
        go = tree.nodes.new('NodeGroupOutput')
        tree.links.new(src.outputs[0], dn.inputs['Image'])
        tree.links.new(dn.outputs[0], go.inputs[0])
    tree.nodes['src'].image = img
    s = sc.render.image_settings
    old = (sc.render.engine, sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage,
           sc.compositing_node_group, sc.render.use_compositing, s.file_format, s.color_depth)
    try:
        sc.render.engine = 'BLENDER_WORKBENCH'
        sc.render.resolution_x, sc.render.resolution_y = img.size
        sc.render.resolution_percentage = 100
        sc.compositing_node_group = tree
        sc.render.use_compositing = True
        bpy.ops.render.render()
        s.file_format, s.color_depth = 'OPEN_EXR', '32'
        bpy.data.images['Render Result'].save_render(tmp)
    finally:
        (sc.render.engine, sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage,
         sc.compositing_node_group, sc.render.use_compositing, s.file_format, s.color_depth) = old
    im = bpy.data.images.load(tmp, check_existing=False)
    a = np.empty(im.size[0] * im.size[1] * 4, np.float32)
    im.pixels.foreach_get(a)
    bpy.data.images.remove(im)
    return a.reshape(img.size[1], img.size[0], 4)[..., :3]


def bake_one(name, samples=128):
    img = bake(name, samples)
    os.makedirs(LM_DIR, exist_ok=True)
    lin = denoise(img, os.path.join(LM_DIR, '_dn.exr'))
    return save_lightmap(name, lin)


def lm_pixels(name):
    img = bpy.data.images['LM_' + name]
    w, h = img.size
    a = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(a)
    return a.reshape(h, w, 4)[..., :3]


def encode(lin):
    """Linear light -> 8-bit sRGB of L / (L + LM_K): precise in the dark, no clipping in a moonbeam.
    main.js decodes it with L = LM_K * x / (1 - x)."""
    lin = np.maximum(lin, 0.0)
    x = np.clip(lin / (lin + LM_K), 0.0, 1.0)
    s = np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055)
    return (np.clip(s, 0, 1) * 255 + 0.5).astype(np.uint8)


def save_lightmap(name, denoised=None):
    lin = denoised if denoised is not None else lm_pixels(name)
    rgb = encode(lin)
    h, w, _ = rgb.shape
    out = bpy.data.images.get('LMout_' + name)
    if out is None or tuple(out.size) != (w, h):
        if out:
            bpy.data.images.remove(out)
        out = bpy.data.images.new('LMout_' + name, w, h, alpha=False)
    out.colorspace_settings.name = 'sRGB'
    rgba = np.ones((h, w, 4), np.float32)
    rgba[..., :3] = rgb / 255.0
    out.pixels.foreach_set(rgba.ravel())
    os.makedirs(WEB + '/lightmaps', exist_ok=True)
    path = os.path.join(WEB, 'lightmaps', name + '.png')
    out.filepath_raw = path
    out.file_format = 'PNG'
    out.save()
    os.makedirs(LM_DIR, exist_ok=True)
    np.save(os.path.join(LM_DIR, name + '.npy'), lin.astype(np.float16))
    return path


def export_glb():
    lights = [(tuple(o.location), o.data.energy, o.name.split('_')[1]) for o in bpy.data.objects
              if o.type == 'LIGHT' and o.name.startswith('Candle_')]
    keep = list(LIGHTMAPS) + list(UNLIT)
    for o in bpy.context.view_layer.objects:
        o.select_set(o.name in keep)
    os.makedirs(WEB, exist_ok=True)
    bpy.ops.export_scene.gltf(
        filepath=os.path.join(WEB, 'church.glb'), export_format='GLB', use_selection=True,
        export_texcoords=True, export_normals=True, export_materials='EXPORT', export_image_format='NONE',
        export_vertex_color='ACTIVE',
        export_apply=True, export_yup=True, export_draco_mesh_compression_enable=True,
        export_draco_mesh_compression_level=6, export_cameras=False, export_lights=False,
        export_extras=False)
    # candle positions for flames and flicker lights (glTF space: x, z, -y)
    data = [{'p': [round(p[0], 3), round(p[2], 3), round(-p[1], 3)], 'w': w, 'k': k} for p, w, k in lights]
    with open(os.path.join(WEB, 'candles.json'), 'w') as f:
        json.dump(data, f)
    # broken pews, rubble and the hole in the vault, in Blender coordinates
    ruin = json.loads(bpy.context.scene.get('ruin', '{}'))
    ruin.pop('windows', None)
    with open(os.path.join(WEB, 'ruin.json'), 'w') as f:
        json.dump(ruin, f)
    import shutil
    shutil.copy(os.path.join(ROOT, 'blender', 'textures', 'glass_damage.png'), os.path.join(WEB, 'glass', 'glass_damage.png'))
