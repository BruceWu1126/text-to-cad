import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import * as THREE from 'three';
import { parseTZA } from 'oidn-webgpu/lib/tza.js';
import { detectUNetModelSpec, validateUNetModel } from 'oidn-webgpu/lib/modelSpec.js';
import { planTileGrid } from 'oidn-webgpu/lib/tileScheduler.js';
import { NativeUNetCore } from 'oidn-webgpu/lib/nativeUNetCore.js';
import { RecordingDevice } from 'oidn-webgpu/lib/native/recordingDevice.js';
import { inspectPreviewScene } from './scene.js';
import { PREVIEW_OIDN_PROFILE, planPreviewResources, planPreviewTextures, previewSourceStats,
  createPreviewResourcePool, reservePreviewResources, estimatePreviewGuideInputs, estimatePreviewInference } from './resourcePolicy.js';

const viewport = { width: 1920, height: 1080 };
const deviceLimits = { maxStorageBufferBindingSize: 128 * 1024 ** 2, maxBufferSize: 256 * 1024 ** 2,
  maxTextureDimension2D: 8192, maxTextureArrayLayers: 256 };
function model() {
  const root = new THREE.Group();
  const geometry = new THREE.BoxGeometry(), material = new THREE.MeshStandardMaterial();
  root.add(new THREE.Mesh(geometry, material));
  return { root, geometry, material, admission: inspectPreviewScene(root) };
}

test('the memory allowance bounds actual installed OIDN allocations for the bundled model without a GPU', async () => {
  const bytes = await readFile(new URL('./weights/rt_hdr_calb_cnrm.tza', import.meta.url));
  const tensors = parseTZA(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
  const model = validateUNetModel(tensors, detectUNetModelSpec(tensors));
  for (const shaderF16 of [false, true]) for (const [width, height, size] of [[256, 256, 64], [320, 320, 128], [128, 112, 64], [16, 16, 64]]) {
    const device = new RecordingDevice({ features: shaderF16 ? ['shader-f16'] : [], adapterInfo: {}, limits: {
      ...deviceLimits, maxComputeWorkgroupStorageSize: 32768, maxComputeInvocationsPerWorkgroup: 256,
      maxComputeWorkgroupSizeX: 256, maxComputeWorkgroupSizeY: 256, maxComputeWorkgroupsPerDimension: 65535 } });
    // Match the production UNet's precision:auto and fixed maximum output tile.
    const core = new NativeUNetCore(device, model, { precision: 'auto' });
    const weights = device.buffers.reduce((sum, buffer) => sum + buffer.size, 0);
    const overlap = Math.ceil(model.spec.receptiveField / 2 / 16) * 16;
    const grid = planTileGrid(width, height, size, overlap);
    const inputWidth = Math.max(...grid.tiles.map(tile => tile.input.width));
    const inputHeight = Math.max(...grid.tiles.map(tile => tile.input.height));
    const inputs = Array.from({ length: 3 }, () => device.createBuffer({ size: inputWidth * inputHeight * 16, usage: 128 }));
    try {
      core.execute(inputs, inputWidth, inputHeight);
      const actual = device.buffers.reduce((sum, buffer) => sum + buffer.size, 0);
      const estimated = estimatePreviewInference({ width, height, denoiseTileSize: size }, shaderF16);
      assert.ok(estimated.bytes >= 2 * (weights + 3 * (actual - weights)), `OIDN ${size}/${core.precision}: ${actual} exceeds admission`);
      assert.ok(estimated.largestBuffer >= Math.max(...device.buffers.map(buffer => buffer.size)));
      assert.equal(estimated.precision, core.precision);
    } finally { core.dispose(); }
  }
});

test('Auto preserves the existing settled detail; safety can reduce an explicit preference without changing the source', () => {
  const { admission, geometry, material } = model();
  const auto = planPreviewResources({ admission, viewport, deviceLimits, denoiseTileSize: 64 });
  assert.equal(auto.ok, true);
  assert.ok(auto.profile.width * auto.profile.height > 990000);
  assert.equal(auto.profile.samples, 128);
  assert.equal(auto.profile.firstDenoiseSamples, 32);
  assert.equal(auto.memory.freeVramKnown, false);
  assert.ok(auto.memory.sourceReserveBytes > 128 * 1024 ** 2);
  const low = planPreviewResources({ admission, viewport, deviceLimits,
    performance: { mode: 'manual', value: 100 }, denoiseTileSize: 64, budgetBytes: auto.memory.peakBytes - 64 * 1024 ** 2 });
  assert.equal(low.ok, true);
  assert.ok(low.profile.effectiveValue < 100);
  assert.ok(low.memory.peakBytes <= low.memory.budgetBytes);
  assert.equal(geometry.boundsTree, undefined);
  assert.equal(material.roughness, 1);
  geometry.dispose(); material.dispose();
});

test('a generated oversized geometry is refused at admission before private copying or GPU work', () => {
  const root = new THREE.Group(), geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(6000003), 3));
  geometry.setAttribute('normal', new THREE.BufferAttribute(new Float32Array(6000003), 3));
  geometry.clone = () => assert.fail('oversized source must not be copied');
  root.add(new THREE.Mesh(geometry, new THREE.MeshStandardMaterial()));
  const result = planPreviewResources({ admission: inspectPreviewScene(root), viewport, deviceLimits });
  assert.equal(result.ok, false);
  assert.match(result.reason, /storage buffer|resource budget|scene budget/);
  assert.equal(geometry.boundsTree, undefined);
  geometry.dispose(); root.children[0].material.dispose();
});

test('texture plans preserve aspect and source identities and reserve FloatType source bytes after resizing', () => {
  const { admission, geometry, material } = model();
  admission.textures = new Map([
    ['source:srgb', { width: 4096, height: 512, bytesPerPixel: 16 }],
    ['source:linear', { width: 4096, height: 512, bytesPerPixel: 16 }],
  ]);
  const plan = planPreviewTextures(admission, { maxSide: 1024, deviceLimits });
  assert.equal(plan.ok, true);
  assert.deepEqual(plan.textures.map(t => [t.key, t.width, t.height, t.bytesPerPixel]),
    [['source:srgb', 1024, 128, 16], ['source:linear', 1024, 128, 16]]);
  assert.equal(plan.bytes, 2 * 1024 * 128 * 16);
  const stats = previewSourceStats(admission);
  const transferred = { ...admission, ...stats, textures: new Map(plan.textures.map(t => [t.key, t])) };
  const after = planPreviewResources({ admission: transferred, viewport, deviceLimits });
  const before = planPreviewResources({ admission, viewport, deviceLimits });
  assert.equal(after.memory.sourceReserveBytes, before.memory.sourceReserveBytes);
  assert.equal(admission.textures.get('source:srgb').width, 4096);
  geometry.dispose(); material.dispose();
});

test('all individual device limits remain binding, even when the total budget has room', () => {
  const { admission, geometry, material } = model();
  const tiny = planPreviewResources({ admission, viewport, deviceLimits: { ...deviceLimits, maxBufferSize: 1024 } });
  assert.equal(tiny.ok, false); assert.match(tiny.reason, /storage buffer/);
  const smallTextures = planPreviewResources({ admission, viewport, deviceLimits: { ...deviceLimits, maxTextureDimension2D: 512 } });
  assert.equal(smallTextures.ok, true);
  assert.ok(smallTextures.profile.width <= 512 && smallTextures.profile.height <= 512);
  geometry.dispose(); material.dispose();
});

test('page reservations share a total ceiling and promote a probe lease atomically', () => {
  const pool = createPreviewResourcePool({ budgetBytes: 1000 });
  const probe = pool.reserve(100), other = pool.reserve(400);
  const plan = bytes => ({ ok: true, memory: { peakBytes: bytes } });
  assert.equal(reservePreviewResources(plan(700), { pool, lease: probe }), null);
  assert.equal(pool.reservedBytes, 500);
  assert.equal(reservePreviewResources(plan(600), { pool, lease: probe }), probe);
  assert.equal(pool.reservedBytes, 1000);
  assert.equal(pool.reserve(1), null);
  other.release(); other.release();
  assert.equal(pool.availableBytes, 400);
  probe.release();
  assert.equal(pool.reservedBytes, 0);
});


test('pre-copy admission bounds hierarchy, placement and texture metadata before packing', () => {
  const { admission, geometry, material } = model();
  for (const changes of [{ objectCount: 20001 }, { groups: 20001 },
    { textures: new Map(Array.from({ length: 1025 }, (_, i) => [String(i), { width: 1, height: 1 }])) }]) {
    const result = planPreviewResources({ admission: { ...admission, ...changes }, viewport, deviceLimits });
    assert.equal(result.ok, false); assert.match(result.reason, /object or placement budget/);
  }
  const result = planPreviewResources({ admission, viewport, deviceLimits,
    packedNodes: { primitives: 100001, transforms: 1, nodeBytes: 64 } });
  assert.equal(result.ok, false);
  const base = planPreviewResources({ admission, viewport, deviceLimits });
  const hierarchy = planPreviewResources({ admission: { ...admission, objectCount: 1000 }, viewport, deviceLimits });
  assert.ok(hierarchy.memory.metadataBytes >= 1000 * 12288);
  assert.ok(hierarchy.memory.sourceReserveBytes > base.memory.sourceReserveBytes);
  const enormousViewport = planPreviewResources({ admission, viewport: { width: 100000, height: 100000 }, deviceLimits });
  assert.equal(enormousViewport.ok, false);
  assert.match(enormousViewport.reason, /resource budget/);
  geometry.dispose(); material.dispose();
});


test('guide uploads reserve independent geometry buffers, integer expansion and texture mipmaps', () => {
  const { admission, geometry, material } = model();
  geometry.setAttribute('color', new THREE.BufferAttribute(new Uint8Array(geometry.attributes.position.count * 3), 3));
  const texturePlan = planPreviewTextures(admission);
  texturePlan.textures = [{ width: 1, height: 1024, bytesPerPixel: 4 }];
  texturePlan.bytes = 4096;
  const upload = estimatePreviewGuideInputs(admission, texturePlan);
  const floatAttributes = Object.values(geometry.attributes).filter(a => a !== geometry.attributes.color).reduce((sum, a) => sum + a.array.byteLength, 0);
  assert.equal(upload.geometryBytes, floatAttributes + geometry.attributes.color.count * 12 + geometry.index.count * 4);
  assert.equal(upload.textureBytes, (2048 - 1) * 4, 'a narrow mip chain exceeds the square-image 4/3 approximation');
  const planned = planPreviewResources({ admission, viewport, deviceLimits });
  assert.equal(planned.memory.guideInputs.geometryBytes, upload.geometryBytes);
  assert.ok(planned.memory.gpuBytes >= planned.memory.guideInputs.geometryBytes + planned.memory.guideInputs.textureBytes
    + Object.values(planned.memory.packed).reduce((sum, n) => sum + n, 0) + planned.memory.atlas.bytes);
  geometry.dispose(); material.dispose();
});


test('staged admission permits bounded BVH construction, then checks actual render nodes before GPU packing', () => {
  const { admission, geometry, material } = model();
  const large = { ...admission, vertices: 1309786, triangles: 1882063, groups: 2400,
    cloneBytes: 48 * 1024 ** 2, objectCount: 3000 };
  const before = planPreviewResources({ admission: large, viewport, deviceLimits, phase: 'build' });
  assert.equal(before.ok, true);
  assert.equal(before.memory.peakBytes, before.memory.buildBytes);
  assert.ok(before.memory.renderBytes > before.memory.peakBytes);
  const after = planPreviewResources({ admission: large, viewport, deviceLimits,
    packedNodes: { nodeBytes: 24 * 1024 ** 2, transforms: 2401, primitives: 5000 } });
  assert.equal(after.ok, true);
  assert.ok(after.memory.peakBytes >= after.memory.renderBytes);
  const rejected = planPreviewResources({ admission: large, viewport, deviceLimits,
    packedNodes: { nodeBytes: 140 * 1024 ** 2, transforms: 2401, primitives: 5000 } });
  assert.equal(rejected.ok, false); assert.match(rejected.reason, /storage buffer|resource budget/);
  geometry.dispose(); material.dispose();
});

test('larger denoising tiles require room at the same settled detail and stay fixed on session replans', () => {
  const { admission, geometry, material } = model();
  const full = planPreviewResources({ admission, viewport, deviceLimits });
  assert.equal(full.profile.denoiseTileSize, 128);
  const small = planPreviewResources({ admission, viewport, deviceLimits, denoiseTileSize: 64 });
  const constrained = planPreviewResources({ admission, viewport, deviceLimits, budgetBytes: small.memory.peakBytes + 1024 });
  assert.equal(constrained.profile.denoiseTileSize, 64);
  assert.deepEqual([constrained.profile.width, constrained.profile.height, constrained.profile.samples],
    [full.profile.width, full.profile.height, full.profile.samples]);
  const fixed = planPreviewResources({ admission, viewport, deviceLimits, denoiseTileSize: 64, performance: { mode: 'manual', value: 0 } });
  assert.equal(fixed.profile.denoiseTileSize, 64);
  geometry.dispose(); material.dispose();
});


test('adopted worker arrays remove one private copy while retaining the editor source reserve', () => {
  const { admission, geometry, material } = model();
  const ordinary = planPreviewResources({ admission, viewport, deviceLimits });
  const worker = planPreviewResources({ admission, viewport, deviceLimits, ownedGeometry: true });
  assert.equal(ordinary.memory.cpuBytes - worker.memory.cpuBytes, admission.cloneBytes);
  assert.equal(ordinary.memory.sourceReserveBytes, worker.memory.sourceReserveBytes);
  const fp16 = planPreviewResources({ admission, viewport, deviceLimits: { ...deviceLimits, shaderF16: true }, ownedGeometry: true });
  assert.equal(fp16.memory.inference.precision, 'fp16');
  assert.ok(fp16.memory.inferenceBytes < worker.memory.inferenceBytes);
  geometry.dispose(); material.dispose();
});


test('a viewport resize budgets retained network shapes and targets alongside the new source reserve', () => {
  const { admission, geometry, material } = model();
  const first = planPreviewResources({ admission, viewport, deviceLimits, ownedGeometry: true });
  const nextOptions = { admission, viewport: { width: 2500, height: 1600 }, deviceLimits, ownedGeometry: true,
    performance: { mode: 'manual', value: 0 }, denoiseTileSize: first.profile.denoiseTileSize };
  const unretained = planPreviewResources(nextOptions);
  const retained = planPreviewResources({ ...nextOptions, inferenceBounds: first.memory.inference });
  assert.equal(retained.ok, true);
  assert.ok(retained.profile.width * retained.profile.height < first.profile.width * first.profile.height);
  assert.equal(retained.memory.inference.targetPixels, first.memory.inference.targetPixels);
  assert.equal(retained.memory.inference.frameBudget, first.memory.inference.frameBudget);
  assert.equal(retained.memory.inference.inputWidth, first.memory.inference.inputWidth);
  assert.equal(retained.memory.inference.inputHeight, first.memory.inference.inputHeight);
  assert.equal(retained.memory.sourceReserveBytes, unretained.memory.sourceReserveBytes);
  assert.ok(retained.memory.peakBytes > Math.max(first.memory.peakBytes, unretained.memory.peakBytes));
  const denied = planPreviewResources({ ...nextOptions, inferenceBounds: first.memory.inference,
    budgetBytes: Math.max(first.memory.peakBytes, unretained.memory.peakBytes) });
  assert.equal(denied.ok, false);
  geometry.dispose(); material.dispose();
});
