// Vigil - first-person walk through a baked gothic nave.
// Lighting is baked in Blender Cycles (assets/lightmaps); candles, god rays, dust and grading run live.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { DRACOLoader } from 'three/addons/loaders/DRACOLoader.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { ShaderPass } from 'three/addons/postprocessing/ShaderPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';

const ASSETS = './assets/';
const LM_INTENSITY = Math.PI;               // three divides indirect diffuse by PI

// lightmaps hold x = L / (L + 0.5) (see bake_export.encode): turn it back into light
{
  const from = 'vec3 lightMapIrradiance = lightMapTexel.rgb * lightMapIntensity;';
  const chunk = THREE.ShaderChunk.lights_fragment_maps;
  if (!chunk.includes(from)) console.warn('lightmap decode: chunk changed, lightmaps will look wrong');
  THREE.ShaderChunk.lights_fragment_maps = chunk.replace(from,
    'vec3 lmx = min(lightMapTexel.rgb, vec3(0.996)); vec3 lmL = 0.5 * lmx / (1.0 - lmx);' +
    ' vec3 lightMapIrradiance = lmL / (1.0 + 0.55 * lmL) * lightMapIntensity;');   // soft roll-off for raw moonbeams
}
const EXPOSURE = 3.6;                       // well under Blender's +2.5 stops, for a dark nave
const MOUSE = 0.00055;                      // radians per pixel
const EYE = 1.65;
const RADIUS = 0.3;

// Blender (x, y, z) -> three (x, z, -y)
const B = (x, y, z = 0) => new THREE.Vector3(x, z, -y);

// ---------------------------------------------------------------- renderer
const MOBILE = matchMedia('(hover: none) and (pointer: coarse)').matches;
const renderer = new THREE.WebGLRenderer({ powerPreference: 'high-performance' });
renderer.setPixelRatio(Math.min(devicePixelRatio, MOBILE ? 1.25 : 1.5));
renderer.setSize(innerWidth, innerHeight);
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.AgXToneMapping;
renderer.toneMappingExposure = EXPOSURE;
document.body.prepend(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0.002, 0.0016, 0.0012);
scene.fog = new THREE.FogExp2(new THREE.Color(0.011, 0.008, 0.0055), 0.026);

const camera = new THREE.PerspectiveCamera(70, innerWidth / innerHeight, 0.05, 120);
camera.rotation.order = 'YXZ';

// ---------------------------------------------------------------- loading
const manager = new THREE.LoadingManager();
const bar = document.getElementById('progress');
const enter = document.getElementById('enter');
manager.onProgress = (_, done, total) => { bar.style.width = `${(100 * done / total).toFixed(0)}%`; };

const texLoader = new THREE.TextureLoader(manager);
const maxAniso = Math.min(8, renderer.capabilities.getMaxAnisotropy());
const texCache = new Map();
function tex(file, srgb = true, repeat = true) {
  const key = file + srgb;
  if (texCache.has(key)) return texCache.get(key);
  const t = texLoader.load(ASSETS + file);
  t.flipY = false;                                   // glTF UV convention
  if (srgb) t.colorSpace = THREE.SRGBColorSpace;
  if (repeat) t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.anisotropy = maxAniso;
  texCache.set(key, t);
  return t;
}

const PBR = {
  Stone:     { set: 'medieval_blocks_03', color: [0.62, 0.6, 0.58], roughness: 1, normalScale: 1.2, grime: 'walls' },
  StoneTrim: { set: 'rock_01', color: [1.0, 0.95, 0.88], roughness: 1, normalScale: 0.8, grime: 'walls' },
  Floor:     { set: 'floor_tiles_04', color: [0.38, 0.36, 0.34], roughness: 0.75, normalScale: 1.4, grime: 'floor' },
  Wood:      { set: 'dark_wooden_planks', color: [0.75, 0.6, 0.5], roughness: 0.7, normalScale: 1, grime: 'wood' },
  Iron:      { set: 'rust_coarse_01', color: [0.26, 0.21, 0.19], roughness: 1, metalness: 0.3, normalScale: 1.6, grime: 'wood' },
  Shard:     { color: [1, 1, 1], roughness: 0.15 },
  Gold:      { color: [0.75, 0.52, 0.2], roughness: 0.3, metalness: 0.55 },
  Wax:       { color: [0.8, 0.72, 0.56], roughness: 0.45 },
  Cloth:     { color: [0.07, 0.004, 0.006], roughness: 0.85 },
  Linen:     { color: [0.62, 0.6, 0.55], roughness: 0.8 },
};
const GLASS = { Glass: 'glass_lancets', GlassEast: 'glass_east', GlassRose: 'glass_rose' };

function pbrMaterial(name, meshName) {
  const d = PBR[name] ?? PBR.Stone;
  const m = new THREE.MeshStandardMaterial({
    color: new THREE.Color().setRGB(...d.color, THREE.LinearSRGBColorSpace),
    roughness: d.roughness, metalness: d.metalness ?? 0,
  });
  if (d.set) {
    m.map = tex(`textures/${d.set}_diff.jpg`);
    m.normalMap = tex(`textures/${d.set}_nor.jpg`, false);
    m.normalScale.setScalar(d.normalScale);
    m.roughnessMap = tex(`textures/${d.set}_rough.jpg`, false);
  }
  const lm = tex(`lightmaps/${meshName}.png`, true, false);
  lm.channel = 1;
  m.lightMap = lm;
  m.lightMapIntensity = LM_INTENSITY;
  if (d.grime) addGrime(m, d.grime);
  return m;
}

// world-space grime: damp rising from the floor, run-off streaks and blotches, so no stone is clean.
// Three tileable fbm fields packed in one small texture keep it to a few lookups per pixel.
const GRIME_TEX = (() => {
  const N = 256;
  const data = new Uint8Array(N * N * 4);
  const rand = (seed) => () => ((seed = (seed * 16807) % 2147483647) - 1) / 2147483646;
  for (let ch = 0; ch < 3; ch++) {
    const field = new Float32Array(N * N);
    let amp = 0.5, total = 0;
    for (const g of [6, 12, 24, 48]) {
      const r = rand(1234 + ch * 97 + g);
      const grid = Float32Array.from({ length: g * g }, r);
      for (let y = 0; y < N; y++) {
        const gy = (y / N) * g, y0 = Math.floor(gy), fy = gy - y0, sy = fy * fy * (3 - 2 * fy), y1 = (y0 + 1) % g;
        for (let x = 0; x < N; x++) {
          const gx = (x / N) * g, x0 = Math.floor(gx), fx = gx - x0, sx = fx * fx * (3 - 2 * fx), x1 = (x0 + 1) % g;
          const a = grid[y0 * g + x0] + (grid[y0 * g + x1] - grid[y0 * g + x0]) * sx;
          const b = grid[y1 * g + x0] + (grid[y1 * g + x1] - grid[y1 * g + x0]) * sx;
          field[y * N + x] += amp * (a + (b - a) * sy);
        }
      }
      total += amp;
      amp *= 0.5;
    }
    for (let i = 0; i < N * N; i++) data[i * 4 + ch] = Math.round((field[i] / total) * 255);
  }
  for (let i = 0; i < N * N; i++) data[i * 4 + 3] = 255;
  const t = new THREE.DataTexture(data, N, N);
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.magFilter = t.minFilter = THREE.LinearFilter;
  t.needsUpdate = true;
  return t;
})();
const GRIME_GLSL = /* glsl */`
  uniform sampler2D uGrimeTex;
  varying vec3 vGrimePos;
`;
function addGrime(m, mode) {
  m.onBeforeCompile = (s) => {
    s.uniforms.uGrimeTex = { value: GRIME_TEX };
    s.vertexShader = s.vertexShader
      .replace('#include <common>', '#include <common>\nvarying vec3 vGrimePos;\nvarying vec3 vGrimeN;')
      .replace('#include <project_vertex>', `#include <project_vertex>
        vGrimePos = (modelMatrix * vec4(transformed, 1.0)).xyz;
        vGrimeN = normalize(mat3(modelMatrix) * objectNormal);`);
    const walls = /* glsl */`
      vec2 q = vec2(p.x + p.z, p.y);
      float big = texture2D(uGrimeTex, q * 0.05).r;
      float edge = texture2D(uGrimeTex, q * 0.13).g;
      float damp = 1.0 - smoothstep(0.0, 0.9 + 1.6 * edge, p.y);
      float streak = texture2D(uGrimeTex, vec2(q.x * 0.35, q.y * 0.018)).b;
      float g = mix(0.6, 1.2, big) * (1.0 - 0.45 * damp) * mix(0.68, 1.1, streak);`;
    const floor = /* glsl */`
      float big = texture2D(uGrimeTex, p.xz * 0.07).r;
      float stain = smoothstep(0.5, 0.72, texture2D(uGrimeTex, p.xz * 0.21 + 0.37).g);
      float g = mix(0.6, 1.2, big) * (1.0 - 0.38 * stain);`;
    // years of dust settled on everything that faces up; on the floor it is thinner where people still walk
    const dustAmt = mode === 'floor' ? 'mix(0.2, 0.7, smoothstep(0.8, 2.6, abs(p.x)))' : mode === 'wood' ? '0.55' : '0.7';
    s.fragmentShader = s.fragmentShader
      .replace('#include <common>', '#include <common>\n' + GRIME_GLSL + '\nvarying vec3 vGrimeN;')
      .replace('#include <map_fragment>', `#include <map_fragment>
        {
          vec3 p = vGrimePos;
          ${mode === 'floor' ? floor : mode === 'wood' ? 'float g = 1.0;' : walls}
          diffuseColor.rgb *= g;
          float up = smoothstep(0.55, 0.95, normalize(vGrimeN).y);
          float dn = texture2D(uGrimeTex, p.xz * 0.9 + 0.13).g;
          diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.36, 0.34, 0.31), up * (0.35 + 0.65 * dn) * ${dustAmt});
        }`);
  };
  m.customProgramCacheKey = () => 'grime-' + mode;
}

// ---------------------------------------------------------------- church
const draco = new DRACOLoader(manager).setDecoderPath('https://cdn.jsdelivr.net/npm/three@0.170.0/examples/jsm/libs/draco/gltf/');
const gltfLoader = new GLTFLoader(manager).setDRACOLoader(draco);
gltfLoader.load(ASSETS + 'church.glb', (gltf) => {
  const drop = [];
  gltf.scene.traverse((o) => {
    if (!o.isMesh) return;
    const mat = o.material.name;
    if (mat === 'Web') {                 // older exports still carry cobwebs; they are not shown any more
      drop.push(o);
      return;
    }
    if (GLASS[mat]) {
      const map = tex(`glass/${GLASS[mat]}.jpg`, true, false);
      const alphaMap = tex('glass/glass_damage.png', false, false);      // knocked-out panes, per window (uv1)
      alphaMap.channel = 1;
      o.material = new THREE.MeshBasicMaterial({ map, alphaMap, alphaTest: 0.5, color: new THREE.Color(0.75, 0.72, 0.78), fog: false, side: THREE.DoubleSide });
    } else if (mat === 'Mortar') {
      o.material = new THREE.MeshBasicMaterial({ color: new THREE.Color(0.0015, 0.0012, 0.001) });
    } else {
      o.material = pbrMaterial(mat, o.name);
      o.material.vertexColors = !!o.geometry.attributes.color;
      if (o.name === 'AltarStone') o.material.color.multiplyScalar(0.72);   // pale stone right beside the candles
    }
    o.matrixAutoUpdate = false;
    o.updateMatrix();
  });
  for (const o of drop) {
    o.removeFromParent();
    o.geometry.dispose();
  }
  scene.add(gltf.scene);
});

// ---------------------------------------------------------------- candles
function flameTexture() {
  const c = document.createElement('canvas');
  c.width = 64; c.height = 128;
  const g = c.getContext('2d');
  const grd = g.createRadialGradient(32, 92, 2, 32, 80, 60);
  grd.addColorStop(0, 'rgba(255,255,235,1)');
  grd.addColorStop(0.18, 'rgba(255,214,120,0.95)');
  grd.addColorStop(0.45, 'rgba(255,130,30,0.45)');
  grd.addColorStop(1, 'rgba(255,80,0,0)');
  g.fillStyle = grd;
  g.beginPath();
  g.moveTo(32, 4);
  g.bezierCurveTo(50, 50, 56, 84, 32, 124);
  g.bezierCurveTo(8, 84, 14, 50, 32, 4);
  g.fill();
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}
function haloTexture() {
  const c = document.createElement('canvas');
  c.width = c.height = 128;
  const g = c.getContext('2d');
  const grd = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  grd.addColorStop(0, 'rgba(255,190,110,0.9)');
  grd.addColorStop(0.25, 'rgba(255,140,50,0.25)');
  grd.addColorStop(1, 'rgba(255,100,20,0)');
  g.fillStyle = grd;
  g.fillRect(0, 0, 128, 128);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

const flames = [];
const flicker = [];
const flameMat = new THREE.SpriteMaterial({ map: flameTexture(), color: new THREE.Color(9, 5.5, 2.4), blending: THREE.AdditiveBlending, depthWrite: false, fog: false });
const haloMat = new THREE.SpriteMaterial({ map: haloTexture(), color: new THREE.Color(0.5, 0.3, 0.12), blending: THREE.AdditiveBlending, depthWrite: false, fog: false });

fetch(ASSETS + 'candles.json').then((r) => r.json()).then((list) => {
  const clusters = new Map();
  for (const c of list) {
    const p = new THREE.Vector3(...c.p);
    const small = c.k === 'votive';
    const f = new THREE.Sprite(flameMat);
    f.center.set(0.5, 0.12);
    f.position.copy(p).y -= 0.03;
    const h = new THREE.Sprite(haloMat.clone());
    h.position.copy(p);
    const s = small ? 0.65 : 1;
    scene.add(f, h);
    flames.push({ f, h, s, seed: Math.random() * 100, base: p.clone() });
    // group candles into a few flickering real-time lights for live specular glints
    // live lights only for the big groups (8 in all); single candles are already in the lightmaps
    const key = c.k === 'altar' || c.k === 'stand' ? `${c.k}:${Math.sign(p.x)}`
      : c.k === 'corona' ? `corona:${Math.round(p.z / 4)}`
        : c.k === 'votive' ? 'votive' : null;
    if (!key) continue;
    if (!clusters.has(key)) clusters.set(key, { sum: new THREE.Vector3(), n: 0, w: 0 });
    const cl = clusters.get(key);
    cl.sum.add(p); cl.n++; cl.w += c.w;
  }
  for (const cl of clusters.values()) {
    const light = new THREE.PointLight(0xff9a45, 0.0, 14, 2);
    light.position.copy(cl.sum.divideScalar(cl.n)).y += 0.15;
    light.userData.base = Math.min(2.4, 0.22 * cl.w);
    light.userData.seed = Math.random() * 100;
    scene.add(light);
    flicker.push(light);
  }
});

// cheap smooth noise for flicker
const n1 = (t) => Math.sin(t * 1.7) * 0.5 + Math.sin(t * 3.1 + 1.3) * 0.3 + Math.sin(t * 7.3 + 0.7) * 0.2;

// ---------------------------------------------------------------- god rays through the north windows
const MOON = (() => {
  const el = THREE.MathUtils.degToRad(58);
  const h = new THREE.Vector2(0.92, 0.39).normalize();
  return B(Math.cos(el) * h.x, Math.cos(el) * h.y, -Math.sin(el)).normalize();   // direction light travels
})();

function lancet(c, h, sill, spring, n = 10) {
  const pts = [[c - h, sill]];
  for (let i = 0; i <= n; i++) {           // left half up to the apex
    const a = (Math.PI / 3) * i / n;
    pts.push([c + h - 2 * h * Math.cos(a), spring + 2 * h * Math.sin(a)]);
  }
  for (let i = n - 1; i >= 0; i--) {
    const a = (Math.PI / 3) * i / n;
    pts.push([c - h + 2 * h * Math.cos(a), spring + 2 * h * Math.sin(a)]);
  }
  pts.push([c + h, sill]);
  return pts;
}

const shaftMat = new THREE.ShaderMaterial({
  uniforms: { uTime: { value: 0 }, uColor: { value: new THREE.Color(0.34, 0.34, 0.78).multiplyScalar(0.0034) } },
  vertexShader: /* glsl */`
    attribute float along;
    varying float vAlong;
    varying vec3 vWorld;
    varying vec3 vNormal;
    void main() {
      vAlong = along;
      vec4 w = modelMatrix * vec4(position, 1.0);
      vWorld = w.xyz;
      vNormal = normalize(mat3(modelMatrix) * normal);
      gl_Position = projectionMatrix * viewMatrix * w;
    }`,
  fragmentShader: /* glsl */`
    uniform float uTime;
    uniform vec3 uColor;
    varying float vAlong;
    varying vec3 vWorld;
    varying vec3 vNormal;
    void main() {
      vec3 v = normalize(cameraPosition - vWorld);
      float facing = pow(abs(dot(normalize(vNormal), v)), 1.4);
      float fade = smoothstep(0.0, 0.06, vAlong) * (1.0 - smoothstep(0.45, 1.0, vAlong));
      float motes = 0.8 + 0.2 * sin(vWorld.x * 2.3 + uTime * 0.21) * sin(vWorld.z * 1.7 - uTime * 0.17 + vWorld.y);
      float dist = smoothstep(0.6, 3.0, length(cameraPosition - vWorld));
      gl_FragColor = vec4(uColor * facing * fade * motes * dist, 1.0);
    }`,
  transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide,
});

// moonlight straight through the fallen vault: no glass to colour or dim it
const holeShaftMat = shaftMat.clone();
holeShaftMat.uniforms.uColor.value = new THREE.Color(0.42, 0.47, 0.85).multiplyScalar(0.0075);
holeShaftMat.uniforms.uTime = shaftMat.uniforms.uTime;

function addShaft(outline, wallX) {
  addShaftPoints(outline.map(([y, z]) => B(wallX, y, z)), shaftMat);
}

function addShaftPoints(top, mat) {
  const pos = [], along = [];
  for (let i = 0; i < top.length; i++) {
    const a = top[i], b = top[(i + 1) % top.length];
    const a2 = a.clone().addScaledVector(MOON, a.y / -MOON.y);
    const b2 = b.clone().addScaledVector(MOON, b.y / -MOON.y);
    for (const [p, t] of [[a, 0], [b, 0], [b2, 1], [a, 0], [b2, 1], [a2, 1]]) {
      pos.push(p.x, p.y, p.z);
      along.push(t);
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute('along', new THREE.Float32BufferAttribute(along, 1));
  g.computeVertexNormals();
  const m = new THREE.Mesh(g, mat);
  m.renderOrder = 2;
  m.frustumCulled = false;
  scene.add(m);
}
for (let i = 0; i < 8; i++) {
  const yc = -17.5 + i * 5;
  addShaft(lancet(yc, 0.78, 2.35, 6.0), -9.85);
  addShaft(lancet(yc, 1.02, 13.75, 18.1), -5.05);
}

// ---------------------------------------------------------------- dust
const DUST = 1800;
const dustGeo = new THREE.BufferGeometry();
const dustPos = new Float32Array(DUST * 3);
const dustSeed = new Float32Array(DUST);
for (let i = 0; i < DUST; i++) {
  dustPos.set([THREE.MathUtils.randFloat(-9, 9), THREE.MathUtils.randFloat(0.3, 9), THREE.MathUtils.randFloat(-19.5, 19.5)], i * 3);
  dustSeed[i] = Math.random() * 100;
}
dustGeo.setAttribute('position', new THREE.BufferAttribute(dustPos, 3));
const dustTex = (() => {
  const c = document.createElement('canvas');
  c.width = c.height = 32;
  const g = c.getContext('2d');
  const grd = g.createRadialGradient(16, 16, 0, 16, 16, 16);
  grd.addColorStop(0, 'rgba(255,255,255,1)');
  grd.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = grd;
  g.fillRect(0, 0, 32, 32);
  return new THREE.CanvasTexture(c);
})();
const dust = new THREE.Points(dustGeo, new THREE.PointsMaterial({
  size: 0.014, map: dustTex, color: new THREE.Color(0.026, 0.022, 0.018), transparent: true,
  depthWrite: false, blending: THREE.AdditiveBlending,
}));
dust.frustumCulled = false;
scene.add(dust);

// ---------------------------------------------------------------- the ruin: wind
// wind comes in through the broken north windows and the fallen vault, in gusts
const wind = { gust: 0, dir: new THREE.Vector3(1, 0, -0.3).normalize() };
function updateWind(t) {
  const g = n1(t * 0.21 + 3.0) * 0.5 + 0.5;
  wind.gust = THREE.MathUtils.smoothstep(g, 0.45, 0.95) * (0.6 + 0.4 * (n1(t * 1.3) * 0.5 + 0.5));
}

// what the ruin left behind: broken pews (no sitting), the rubble heap, and moonlight through the fallen vault
fetch(ASSETS + 'ruin.json').then((r) => r.json()).then((ruin) => {
  for (const k of ruin.broken_seats) BROKEN_SEATS.add(k);
  for (const [x, y, r] of ruin.rubble) circles.push([x, y, r]);
  const h = ruin.hole;
  if (h) {
    const ring = [];
    for (let i = 0; i < 20; i++) {
      const a = (i / 20) * Math.PI * 2;
      ring.push(B(h.x + Math.cos(a) * h.rx, h.y + Math.sin(a) * h.ry, h.z));
    }
    addShaftPoints(ring, holeShaftMat);
  }
});

// ---------------------------------------------------------------- post
const target = new THREE.WebGLRenderTarget(innerWidth, innerHeight, { type: THREE.HalfFloatType, samples: MOBILE ? 0 : 4 });
const composer = new EffectComposer(renderer, target);
composer.setPixelRatio(renderer.getPixelRatio());
composer.addPass(new RenderPass(scene, camera));
const bloom = new UnrealBloomPass(new THREE.Vector2(innerWidth, innerHeight), 0.55, 0.65, 0.22);
composer.addPass(bloom);
composer.addPass(new OutputPass());
const grade = new ShaderPass({
  uniforms: { tDiffuse: { value: null }, uTime: { value: 0 }, uAspect: { value: innerWidth / innerHeight } },
  vertexShader: `varying vec2 vUv; void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
  fragmentShader: /* glsl */`
    uniform sampler2D tDiffuse;
    uniform float uTime;
    uniform float uAspect;
    varying vec2 vUv;
    float hash(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }
    void main() {
      vec3 c = texture2D(tDiffuse, vUv).rgb;
      // cool shadows, warm highlights
      float l = dot(c, vec3(0.2126, 0.7152, 0.0722));
      c = mix(c * vec3(0.9, 0.96, 1.1), c * vec3(1.06, 1.0, 0.9), smoothstep(0.05, 0.6, l));
      vec2 d = (vUv - 0.5) * vec2(uAspect, 1.0);
      c *= 1.0 - smoothstep(0.35, 1.05, length(d)) * 0.75;
      c += (hash(vUv * 1000.0 + fract(uTime) * 61.0) - 0.5) * 0.028;
      gl_FragColor = vec4(c, 1.0);
    }`,
});
composer.addPass(grade);

// ---------------------------------------------------------------- walking
const player = { x: 0, y: -17.2, yaw: 0, pitch: 0.06, lean: 0, vx: 0, vy: 0, eye: EYE, bob: 0 };
camera.rotation.set(player.pitch, player.yaw, 0);

const circles = [];
for (let i = 0; i <= 8; i++) {
  const y = -20 + i * 5;
  for (const s of [-1, 1]) circles.push([s * 5, y, 1.02], [s * 9.5, y, 0.5]);
}
circles.push([-3.75, 10.0, 0.8], [6.9, -16.5, 0.72], [1.65, 10.5, 0.4], [-1.65, 10.5, 0.4],
  [-8.6, -12.5, 0.3], [8.6, -7.5, 0.3], [-8.6, 2.5, 0.3], [8.6, 7.5, 0.3]);
const boxes = [
  [0.95, 3.95, -16.15, 8.95], [-3.95, -0.95, -16.15, 8.95],      // pews
  [-1.7, 1.7, 17.35, 18.55], [-3.6, 3.6, 18.85, 19.6],          // altar, reredos
  [-9.5, -8.1, -3.55, -1.45],                                   // votive rack
];

function collide(p) {
  p.x = THREE.MathUtils.clamp(p.x, -9.05 + RADIUS, 9.05 - RADIUS);   // stone benches line the aisle walls
  p.y = THREE.MathUtils.clamp(p.y, -20 + 0.6 + RADIUS, 20 - RADIUS);
  for (const [cx, cy, r] of circles) {
    const dx = p.x - cx, dy = p.y - cy, rr = r + RADIUS;
    const d2 = dx * dx + dy * dy;
    if (d2 < rr * rr && d2 > 1e-8) {
      const d = Math.sqrt(d2);
      p.x = cx + dx / d * rr;
      p.y = cy + dy / d * rr;
    }
  }
  for (const [x0, x1, y0, y1] of boxes) {
    if (p.x > x0 - RADIUS && p.x < x1 + RADIUS && p.y > y0 - RADIUS && p.y < y1 + RADIUS) {
      const push = [[x0 - RADIUS - p.x, 0], [x1 + RADIUS - p.x, 0], [0, y0 - RADIUS - p.y], [0, y1 + RADIUS - p.y]];
      push.sort((a, b) => Math.abs(a[0] + a[1]) - Math.abs(b[0] + b[1]));
      p.x += push[0][0];
      p.y += push[0][1];
    }
  }
}

function floorAt(x, y) {
  if (Math.abs(x) < 4.6 && y > 11.0) return y > 11.7 ? 0.45 : y > 11.35 ? 0.3 : 0.15;
  return 0;
}

const keys = new Set();
addEventListener('keydown', (e) => {
  keys.add(e.code);
  if (e.code === 'KeyM') toggleSound();
  if (e.code === 'KeyE' && !e.repeat) toggleSeat();
});
addEventListener('keyup', (e) => keys.delete(e.code));
addEventListener('blur', () => keys.clear());

const canvas = renderer.domElement;
let locked = false;
document.addEventListener('pointerlockchange', () => {
  locked = document.pointerLockElement === canvas;
  document.getElementById('hint').classList.toggle('show', !locked && started && !isTouch);
});
document.addEventListener('mousemove', (e) => {
  if (!locked) return;
  // ignore the occasional huge jump some browsers report when the pointer lock starts
  if (Math.abs(e.movementX) > 300 || Math.abs(e.movementY) > 300) return;
  look(e.movementX, e.movementY, MOUSE);
});
canvas.addEventListener('click', () => { if (started && !isTouch && !locked) canvas.requestPointerLock(); });

function look(dx, dy, k) {
  player.yaw -= dx * k;
  player.pitch = THREE.MathUtils.clamp(player.pitch - dy * k, -1.45, 1.45);
}

// ---- phones: the gyroscope looks; hold the top half of the screen to walk on, the bottom half to step back;
// ---- a sideways drag turns (and without a gyroscope, a vertical drag tilts the view)
const isTouch = matchMedia('(hover: none) and (pointer: coarse)').matches;
const pads = { up: document.getElementById('padUp'), down: document.getElementById('padDown') };
const touch = { id: null, dir: 0, x: 0, y: 0 };
canvas.addEventListener('touchstart', (e) => {
  if (!started) return;
  const t = e.changedTouches[0];
  touch.id = t.identifier;
  touch.dir = seat.mode !== 'walk' ? 0 : t.clientY < innerHeight / 2 ? 1 : -1;     // seated: look only
  touch.x = t.clientX;
  touch.y = t.clientY;
  pads.up.classList.toggle('on', touch.dir > 0);
  pads.down.classList.toggle('on', touch.dir < 0);
}, { passive: true });
canvas.addEventListener('touchmove', (e) => {
  for (const t of e.changedTouches) {
    if (t.identifier !== touch.id) continue;
    const dx = t.clientX - touch.x, dy = t.clientY - touch.y;
    if (gyro.on) gyro.offset -= dx * 0.004;
    else look(dx, dy, 0.004);
    touch.x = t.clientX;
    touch.y = t.clientY;
  }
}, { passive: true });
const touchEnd = (e) => {
  for (const t of e.changedTouches) {
    if (t.identifier !== touch.id) continue;
    touch.id = null;
    touch.dir = 0;
    pads.up.classList.remove('on');
    pads.down.classList.remove('on');
  }
};
canvas.addEventListener('touchend', touchEnd, { passive: true });
canvas.addEventListener('touchcancel', touchEnd, { passive: true });

const gyro = { on: false, a: 0, b: 0, g: 0, offset: null };
const gq = { zee: new THREE.Vector3(0, 0, 1), q: new THREE.Quaternion(), q0: new THREE.Quaternion(),
  q1: new THREE.Quaternion(-Math.sqrt(0.5), 0, 0, Math.sqrt(0.5)), e: new THREE.Euler() };
function onOrientation(e) {
  if (e.alpha == null || e.beta == null) return;
  gyro.on = true;
  gyro.a = e.alpha;
  gyro.b = e.beta;
  gyro.g = e.gamma;
}
async function enableGyro() {
  try {
    const DOE = window.DeviceOrientationEvent;
    if (!DOE) return;
    if (typeof DOE.requestPermission === 'function' && (await DOE.requestPermission()) !== 'granted') return;
    addEventListener('deviceorientation', onOrientation);
  } catch (err) { console.warn('gyroscope unavailable', err); }
}
function applyGyro() {
  // device orientation -> camera, the same maths as three's old DeviceOrientationControls
  const D = THREE.MathUtils.DEG2RAD;
  const orient = (screen.orientation?.angle ?? window.orientation ?? 0) * D;
  gq.e.set(gyro.b * D, gyro.a * D, -gyro.g * D, 'YXZ');
  gq.q.setFromEuler(gq.e).multiply(gq.q1).multiply(gq.q0.setFromAxisAngle(gq.zee, -orient));
  gq.e.setFromQuaternion(gq.q, 'YXZ');
  if (gyro.offset === null) gyro.offset = player.yaw - gq.e.y;     // start facing down the nave
  player.yaw = gq.e.y + gyro.offset;
  player.pitch = THREE.MathUtils.clamp(gq.e.x, -1.45, 1.45);
  return gq.e.z;
}

// ---- sitting: any pew end beside an aisle, or the stone benches along the aisle walls
const PEW_ROWS = Array.from({ length: 25 }, (_, k) => -15.6 + k);
const PEWS_Y0 = -16.2, PEWS_Y1 = 9.0;      // the rails run between the first and the last row
const SEAT_EYE = 1.18;                     // eye height when seated on a 0.47 m pew
const REACH = 1.7;
const seat = { mode: 'walk', t: 0, from: null, to: null, back: null, near: null };
const act = document.getElementById('act');
const BROKEN_SEATS = new Set();            // filled from the scene: pews that are knocked over or broken

function nearestSeat() {
  const k = THREE.MathUtils.clamp(Math.round(player.y - 0.27 + 15.6), 0, PEW_ROWS.length - 1);
  const row = PEW_ROWS[k];
  const cands = [];
  for (const s of [-1, 1]) {
    for (const x of [1.4, 3.5]) {          // the inner and the outer end of each pew (they face the altar)
      if (!BROKEN_SEATS.has(`${k}:${s}`)) cands.push({ x: s * x, y: row + 0.27, yaw: 0, eye: SEAT_EYE });
    }
    const bay = THREE.MathUtils.clamp(Math.floor((player.y + 20) / 5), 0, 7);
    const y0 = -20 + bay * 5;              // stone benches face across the aisle
    cands.push({ x: s * 9.28, y: THREE.MathUtils.clamp(player.y, y0 + 0.6, y0 + 4.4), yaw: s < 0 ? -Math.PI / 2 : Math.PI / 2, eye: 1.15 });
  }
  // prefer the seat you are looking towards
  const fx = -Math.sin(player.yaw), fy = Math.cos(player.yaw);
  let best = null, bd = REACH;
  for (const c of cands) {
    const dx = c.x - player.x, dy = c.y - player.y;
    const d = Math.hypot(dx, dy) + (dx * fx + dy * fy < 0 ? 0.6 : 0);
    if (d < bd) { bd = d; best = c; }
  }
  return best;
}

const ease = (t) => t * t * (3 - 2 * t);
const wrapAngle = (a) => Math.atan2(Math.sin(a), Math.cos(a));

function toggleSeat() {
  if (!started) return;
  if (seat.mode === 'walk' && seat.near) {
    seat.back = { x: player.x, y: player.y };
    seat.from = { x: player.x, y: player.y, eye: player.eye, yaw: player.yaw };
    seat.to = seat.near;
    seat.mode = 'sitting';
    seat.t = 0;
    player.vx = player.vy = 0;
  } else if (seat.mode === 'seated') {
    seat.from = { x: player.x, y: player.y, eye: player.eye, yaw: player.yaw };
    seat.to = { ...seat.back, eye: floorAt(seat.back.x, seat.back.y) + EYE, yaw: player.yaw };
    seat.mode = 'standing';
    seat.t = 0;
  }
}
act.addEventListener('click', (e) => { e.stopPropagation(); toggleSeat(); });
act.addEventListener('touchstart', (e) => e.stopPropagation(), { passive: true });

function setYaw(y) {
  // with a gyroscope the device owns the yaw, so shift its offset instead
  if (gyro.on) gyro.offset += y - player.yaw;
  player.yaw = y;
}

function updateSeat(dt) {
  if (seat.mode === 'sitting' || seat.mode === 'standing') {
    const sitting = seat.mode === 'sitting';
    seat.t = Math.min(1, seat.t + dt / (sitting ? 1.7 : 1.3));
    const t = seat.t, a = seat.from, b = seat.to;
    // turn first, then step into the seat and lower down with a little settle
    const tm = ease(THREE.MathUtils.clamp((t - (sitting ? 0.1 : 0.35)) / 0.6, 0, 1));
    player.x = a.x + (b.x - a.x) * tm;
    player.y = a.y + (b.y - a.y) * tm;
    const th = ease(THREE.MathUtils.clamp((t - (sitting ? 0.3 : 0.0)) / 0.7, 0, 1));
    player.eye = a.eye + (b.eye - a.eye) * th - (sitting ? 0.07 : 0.05) * Math.sin(Math.PI * th);
    player.lean = -0.22 * Math.sin(Math.PI * ease(t));            // bend forward while sitting or rising
    if (sitting) setYaw(a.yaw + wrapAngle(b.yaw - a.yaw) * ease(Math.min(1, t / 0.6)));
    if (t >= 1) {
      seat.mode = sitting ? 'seated' : 'walk';
      player.lean = 0;
    }
  } else if (seat.mode === 'seated' && !gyro.on) {
    // you can look around, but not through the back of your own head
    const d = wrapAngle(player.yaw - seat.to.yaw);
    if (Math.abs(d) > 2.0) player.yaw = seat.to.yaw + Math.sign(d) * 2.0;
  }
}

function updatePrompt() {
  const can = seat.mode === 'walk' ? (seat.near = nearestSeat()) : null;
  const label = seat.mode === 'seated' ? 'Stand' : can ? 'Sit' : '';
  if (act.dataset.label !== label) {
    act.dataset.label = label;
    act.innerHTML = label ? (isTouch ? label : `<kbd>E</kbd> ${label}`) : '';
    act.classList.toggle('show', !!label);
  }
}

function walk(dt) {
  const roll = gyro.on && started ? applyGyro() : 0;
  let bob = 0, sway = 0;
  if (seat.mode === 'walk') {
    let f = touch.dir;
    if (keys.has('KeyW') || keys.has('ArrowUp')) f += 1;
    if (keys.has('KeyS') || keys.has('ArrowDown')) f -= 1;
    f = THREE.MathUtils.clamp(f, -1, 1);
    if (f < 0) f *= 0.7;                                              // stepping back is slower
    const speed = 0.95;                       // a slow, quiet pace
    // between the pew rows you are on rails down the centre aisle (only the part of your view along the nave
    // moves you); before the first row and past the last you walk where you look
    const rails = Math.abs(player.x) < 1.0 && player.y > PEWS_Y0 && player.y < PEWS_Y1;
    const tx = rails ? 0 : -Math.sin(player.yaw) * f * speed;
    const ty = Math.cos(player.yaw) * f * speed;
    const k = 1 - Math.exp(-dt * 8);
    player.vx += (tx - player.vx) * k;
    player.vy += (ty - player.vy) * k;
    const p = { x: rails ? player.x * Math.exp(-dt * 6) : player.x + player.vx * dt, y: player.y + player.vy * dt };
    collide(p);
    player.x = p.x;
    player.y = p.y;
    const moving = Math.min(1, Math.hypot(player.vx, player.vy));
    player.bob += dt * moving * 5.2;
    player.eye += (floorAt(player.x, player.y) + EYE - player.eye) * (1 - Math.exp(-dt * 10));
    bob = Math.sin(player.bob) * 0.022 * moving;
    sway = Math.sin(player.bob * 0.5) * 0.004 * moving;
  } else {
    updateSeat(dt);
  }
  if (started) updatePrompt();
  camera.position.copy(B(player.x, player.y, player.eye + bob));
  camera.rotation.set(player.pitch + player.lean, player.yaw, roll + sway);
}

// ---------------------------------------------------------------- music
let audio = null;
function startSound() {
  const el = new Audio('./assets/audio/ambient.m4a');
  el.loop = true;
  el.preload = 'auto';
  const ctx = new AudioContext();
  const gain = ctx.createGain();                 // master: M fades everything
  gain.gain.value = 0;
  gain.connect(ctx.destination);
  const music = ctx.createGain();
  music.gain.value = 0.7;
  ctx.createMediaElementSource(el).connect(music).connect(gain);
  // wind: looping noise through a moving band-pass, swelling with the gusts
  const len = ctx.sampleRate * 3;
  const buf = ctx.createBuffer(1, len, ctx.sampleRate);
  const d = buf.getChannelData(0);
  let b = 0;
  for (let i = 0; i < len; i++) { b = 0.97 * b + 0.03 * (Math.random() * 2 - 1); d[i] = b * 6; }
  const noise = ctx.createBufferSource();
  noise.buffer = buf;
  noise.loop = true;
  const band = ctx.createBiquadFilter();
  band.type = 'bandpass';
  band.frequency.value = 420;
  band.Q.value = 0.8;
  const windGain = ctx.createGain();
  windGain.gain.value = 0;
  noise.connect(band).connect(windGain).connect(gain);
  noise.start();
  audio = {
    ctx, el, gain, on: true,
    wind(g) {
      windGain.gain.setTargetAtTime(0.05 + 0.32 * g, ctx.currentTime, 0.4);
      band.frequency.setTargetAtTime(320 + 650 * g, ctx.currentTime, 0.5);
    },
  };
  el.play().catch((e) => console.warn('music', e));
  gain.gain.linearRampToValueAtTime(1, ctx.currentTime + 6);
}
function toggleSound() {
  if (!audio) return;
  audio.on = !audio.on;
  const now = audio.ctx.currentTime;
  audio.gain.gain.cancelScheduledValues(now);
  audio.gain.gain.setValueAtTime(audio.gain.gain.value, now);
  audio.gain.gain.linearRampToValueAtTime(audio.on ? 1 : 0, now + 1.2);
  if (audio.on) audio.el.play();
  else setTimeout(() => { if (!audio.on) audio.el.pause(); }, 1300);
}
document.addEventListener('visibilitychange', () => {
  if (!audio) return;
  if (document.hidden) audio.ctx.suspend();
  else audio.ctx.resume();
});

// ---------------------------------------------------------------- start
let started = false;
manager.onLoad = () => {
  bar.style.width = '100%';
  enter.disabled = false;
  enter.textContent = 'Enter';
  // ?shot&x=&y=&yaw=&pitch= skips the title, for screenshots
  const q = new URLSearchParams(location.search);
  if (q.has('shot')) {
    started = true;
    document.getElementById('veil').remove();
    for (const k of ['x', 'y', 'yaw', 'pitch']) if (q.has(k)) player[k] = parseFloat(q.get(k));
    window.__player = player;
    setTimeout(() => { window.__ready = true; }, 1500);
  }
};
enter.addEventListener('click', () => {
  started = true;
  document.getElementById('veil').classList.add('gone');
  if (!isTouch) canvas.requestPointerLock();
  else {
    enableGyro();                       // iOS asks for permission here, inside the tap
    for (const p of Object.values(pads)) p.classList.add('show');
    setTimeout(() => { for (const p of Object.values(pads)) p.classList.remove('show'); }, 5000);
  }
  try { startSound(); } catch (e) { console.warn(e); }
});

addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
  composer.setSize(innerWidth, innerHeight);
  grade.uniforms.uAspect.value = innerWidth / innerHeight;
});

// ---------------------------------------------------------------- loop
const clock = new THREE.Clock();
const tmp = new THREE.Vector3();
const perf = { frames: 0, time: 0, checked: false };
function adapt(dt) {
  // after a few seconds, drop resolution on slow GPUs
  if (perf.checked) return;
  perf.frames++;
  perf.time += dt;
  if (perf.time < 4) return;
  perf.checked = true;
  const fps = perf.frames / perf.time;
  if (fps < 42) {
    const pr = Math.max(0.75, renderer.getPixelRatio() * (fps < 28 ? 0.7 : 0.85));
    renderer.setPixelRatio(pr);
    composer.setPixelRatio(pr);
    composer.setSize(innerWidth, innerHeight);
  }
}
renderer.setAnimationLoop(() => {
  const dt = Math.min(clock.getDelta(), 0.05);
  const t = clock.elapsedTime;
  if (started) adapt(dt);
  walk(started ? dt : 0);
  if (!started) {             // slow drift behind the title
    player.yaw = Math.sin(t * 0.07) * 0.08;
    player.pitch = 0.08 + Math.sin(t * 0.05) * 0.04;
  }
  updateWind(t);
  // flames lean away from the gusts (as seen from here) and gutter harder while they blow
  tmp.set(1, 0, 0).applyQuaternion(camera.quaternion);
  flameMat.rotation = -wind.gust * 0.4 * tmp.dot(wind.dir);
  const gutter = 0.12 + 0.3 * wind.gust;
  for (const c of flames) {
    const n = n1(t * (2.2 + 3 * wind.gust) + c.seed);
    const k = 1 + n * gutter;
    c.f.scale.set(0.034 * c.s * (2 - k) * 0.95, 0.075 * c.s * k * (1 - 0.2 * wind.gust), 1);
    c.f.position.x = c.base.x + n1(t * 3.1 + c.seed * 2) * (0.002 + 0.006 * wind.gust);
    c.h.scale.setScalar(0.24 * c.s * (0.92 + n * 0.08));
    c.h.material.opacity = 0.85 + n * 0.15;
  }
  for (const l of flicker) {
    const n = n1(t * (1.9 + 2.5 * wind.gust) + l.userData.seed);
    l.intensity = l.userData.base * (0.85 + n * (0.18 + 0.2 * wind.gust));
  }
  const p = dustGeo.attributes.position.array;
  const blow = (0.0006 + 0.012 * wind.gust) * dt * 60;
  for (let i = 0; i < DUST; i++) {
    const s = dustSeed[i];
    p[i * 3] += Math.sin(t * 0.13 + s) * 0.0009 + wind.dir.x * blow;
    p[i * 3 + 1] += Math.sin(t * 0.07 + s * 1.3) * 0.0005 - 0.00015 + Math.sin(t * 0.9 + s) * blow * 0.3;
    p[i * 3 + 2] += Math.cos(t * 0.11 + s) * 0.0009 + wind.dir.z * blow;
    if (p[i * 3 + 1] < 0.2) p[i * 3 + 1] = 9;
    if (p[i * 3] > 9.2) p[i * 3] = -9.2;
    if (p[i * 3 + 2] < -19.6) p[i * 3 + 2] = 19.6;
  }
  dustGeo.attributes.position.needsUpdate = true;
  if (audio) audio.wind(wind.gust);
  shaftMat.uniforms.uTime.value = t;
  grade.uniforms.uTime.value = t;
  composer.render();
});
