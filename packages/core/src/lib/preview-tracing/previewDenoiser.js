import { OIDNDenoiser } from 'three-gpu-pathtracer/webgpu';
import { Color, ExternalTexture, NearestFilter, NoToneMapping, RenderTarget, UnsignedByteType } from 'three/webgpu';
import { diffuseColor, mrt, normalView, vec4 } from 'three/tsl';
import { createPreviewGuideScene } from './previewGuides.js';

// OIDN is driven explicitly: the upstream tracer's automatic integration only starts
// after convergence, does not expose tile errors, and presents partially denoised tiles.
// This adapter publishes only after guided RGB and coverage passes both complete.
export class PreviewDenoiser extends OIDNDenoiser {
  constructor(options, { onComplete = () => {}, onError = () => {} } = {}) {
    super(options);
    this.onComplete = onComplete;
    this.onError = onError;
    this.source = null;
    this.status = 'idle';
    this.previousTexture = null;
    this.previousRawTexture = null;
    this.guideMRT = mrt({ output: diffuseColor, normal: vec4(normalView.mul(0.5).add(0.5), 1) });
    this.guidesValid = false;
    this.guideScene = null;
    this.timings = { guideMs: 0, networkMs: 0, rgbMs: 0, alphaNetworkMs: 0, alphaMs: 0 };
    this.alphaUNet = null;
  }

  // Tell WebGPUPathTracer there is no automatic pass to start. Its presentation
  // still reads texture, which remains null until our whole pass has finished.
  get complete() { return true; }
  get texture() { return this._complete ? this._texture : this.previousTexture; }
  update() { return this.texture; }

  async run(color) {
    if (this._running) return;
    if (this._complete) {
      this.previousTexture?.dispose();
      this.previousRawTexture?.destroy();
      this.previousTexture = this._texture;
      this.previousRawTexture = this._rawTexture;
      this._texture = null;
      this._rawTexture = null;
      this._complete = false;
    }
    this._running = true;
    this.status = 'preparing';
    this.source = color;
    const id = ++this._requestId;
    try {
      const guideStart = performance.now();
      const aux = this._renderAuxiliaryBuffers(color.width, color.height);
      this.timings.guideMs = performance.now() - guideStart;
      const networkStart = performance.now();
      const unet = await this._initUNet(true);
      this.timings.networkMs = performance.now() - networkStart;
      if (id !== this._requestId || !this._running) return;
      const renderer = this.renderer, backend = renderer.backend;
      renderer.initTexture(color);
      for (const texture of aux.textures) renderer.initTexture(texture);
      const { width, height } = color;
      this.status = 'denoising';
      const rgbStart = performance.now();
      this._abort = unet.tileExecute({
        color: { data: backend.get(color).texture, width, height },
        albedo: { data: backend.get(aux.textures[0]).texture, width, height },
        normal: { data: backend.get(aux.textures[1]).texture, width, height },
        scheduling: 'animation-frame',
        done: output => {
          if (id !== this._requestId || !this._running) return;
          this.timings.rgbMs = performance.now() - rgbStart;
          this._abort = null;
          void this._runAlphaPass(output, aux, width, height, id);
        },
        error: error => this._fail(error, id),
      });
    } catch (error) { this._fail(error, id); }
  }

  // oidn-webgpu's data-preparation shaders bake denoiseAlpha on first use,
  // so RGB and alpha need separate UNets. Their output buffers remain owned by
  // those networks; keep RGB alive until the alpha pass completes and combine
  // straight RGB with estimated coverage in a GPU copy.
  async _runAlphaPass(rgb, aux, width, height, id) {
    try {
      this.status = 'preparing-alpha';
      const start = performance.now();
      if (!this.alphaUNet) {
        const options = { aux: true, hdr: true };
        if (this.maxTileSize !== null) options.maxTileSize = this.maxTileSize;
        if (this.dynamicTile !== null) options.dynamicTile = this.dynamicTile;
        const device = this.renderer.backend.device;
        this.alphaUNet = this.initUNetFromURL(this.auxWeightsUrl, { device, adapterInfo: device.adapterInfo }, options);
      }
      const unet = await this.alphaUNet;
      this.timings.alphaNetworkMs = performance.now() - start;
      if (id !== this._requestId || !this._running) return;
      const backend = this.renderer.backend;
      const passStart = performance.now();
      this.status = 'denoising-alpha';
      this._abort = unet.tileExecute({
        color: { data: backend.get(this.source).texture, width, height },
        albedo: { data: backend.get(aux.textures[0]).texture, width, height },
        normal: { data: backend.get(aux.textures[1]).texture, width, height },
        denoiseAlpha: true,
        scheduling: 'animation-frame',
        done: alpha => {
          if (id !== this._requestId || !this._running) return;
          try {
            this.timings.alphaMs = performance.now() - passStart;
            this._copyOutputToTexture(rgb, alpha, aux, width, height);
            this._abort = null;
            this._running = false;
            this._complete = true;
            this.previousTexture?.dispose();
            this.previousRawTexture?.destroy();
            this.previousTexture = null;
            this.previousRawTexture = null;
            this.status = 'complete';
            this.onComplete();
          } catch (error) { this._fail(error, id); }
        },
        error: error => this._fail(error, id),
      });
    } catch (error) { this._fail(error, id); }
  }

  _fail(error, id) {
    if (id !== this._requestId) return;
    this._running = false;
    this._abort = null;
    this.status = 'failed';
    this.onError(error);
  }

  reset() {
    ++this._requestId;
    super.reset();
    this.guidesValid = false;
    this.previousTexture?.dispose();
    this.previousRawTexture?.destroy();
    this.previousTexture = null;
    this.previousRawTexture = null;
    this.source = null;
    this.status = 'idle';
  }

  setScene(scene, camera) {
    this.guideScene?.dispose();
    this.guideScene = null;
    super.setScene(scene, camera);
    this.guidesValid = false;
  }

  // Keep one MRT identity: three keys its render-object cache by MRT id.
  // A still image's albedo and normals do not change as samples accumulate,
  // so its later denoise pass can reuse the already-rendered guide buffers.
  _renderAuxiliaryBuffers(width, height) {
    if (!this._auxTarget) {
      this._auxTarget = new RenderTarget(1, 1, { count: 2, type: UnsignedByteType,
        minFilter: NearestFilter, magFilter: NearestFilter, samples: 4 });
      this._auxTarget.textures[0].name = 'output';
      this._auxTarget.textures[1].name = 'normal';
    }
    const target = this._auxTarget;
    if (this.guidesValid && target.width === width && target.height === height) return target;
    target.setSize(width, height);
    const renderer = this.renderer;
    const originalTarget = renderer.getRenderTarget(), originalMRT = renderer.getMRT(), originalToneMapping = renderer.toneMapping;
    const originalClearColor = renderer.getClearColor(new Color()).clone(), originalClearAlpha = renderer.getClearAlpha();
    try {
      renderer.toneMapping = NoToneMapping;
      renderer.setClearColor(0x000000, 0);
      renderer.setRenderTarget(target);
      renderer.setMRT(this.guideMRT);
      this.guideScene ||= createPreviewGuideScene(this.scene);
      renderer.render(this.guideScene.scene, this.camera);
      this.guidesValid = true;
    } finally {
      renderer.setMRT(originalMRT);
      renderer.setRenderTarget(originalTarget);
      renderer.setClearColor(originalClearColor, originalClearAlpha);
      renderer.toneMapping = originalToneMapping;
    }
    return target;
  }

  dispose() {
    this.reset();
    this._auxTarget?.dispose();
    this._auxTarget = null;
    this.guideScene?.dispose();
    this.guideScene = null;
    this.alphaUNet?.then(unet => unet.dispose()).catch(() => {});
    this.alphaUNet = null;
    for (const key in this._unets) {
      this._unets[key]?.then(unet => unet.dispose()).catch(() => {});
      this._unets[key] = null;
    }
  }

  // Combine two independently denoised outputs into a sampleable HDR texture.
  _copyOutputToTexture(output, alpha, aux, width, height) {
    const device = this.renderer.backend.device;
    if (!this._resultPipeline) {
      const module = device.createShaderModule({ code: /* wgsl */`
        @group(0) @binding(0) var<storage, read> filtered: array<vec4f>;
        @group(0) @binding(1) var<storage, read> filteredAlpha: array<vec4f>;
        @group(0) @binding(2) var destination: texture_storage_2d<rgba16float, write>;
        @group(0) @binding(3) var traced: texture_2d<f32>;
        @group(0) @binding(4) var guide: texture_2d<f32>;
        @compute @workgroup_size(8, 8)
        fn main(@builtin(global_invocation_id) p: vec3u) {
          let size = textureDimensions(destination);
          if (p.x >= size.x || p.y >= size.y) { return; }
          let rgb = filtered[p.y * size.x + p.x].rgb;
          // OIDN can invent a border matte in empty space. Preserve exact empty
          // coverage only when both the trace and raster guide see no surface.
          let empty = textureLoad(traced, p.xy, 0).a == 0.0 && textureLoad(guide, p.xy, 0).a == 0.0;
          let coverage = select(clamp(filteredAlpha[p.y * size.x + p.x].a, 0.0, 1.0), 0.0, empty);
          textureStore(destination, p.xy, vec4f(rgb, coverage));
        }
      ` });
      this._resultPipeline = device.createComputePipeline({ layout: 'auto', compute: { module, entryPoint: 'main' } });
    }
    if (!this._rawTexture || this._rawTexture.width !== width || this._rawTexture.height !== height) {
      this._rawTexture?.destroy();
      this._rawTexture = device.createTexture({ size: [width, height], format: 'rgba16float',
        usage: GPUTextureUsage.STORAGE_BINDING | GPUTextureUsage.TEXTURE_BINDING });
      this._texture?.dispose();
      this._texture = new ExternalTexture(this._rawTexture);
      this._texture.image = { width, height };
    }
    const bindGroup = device.createBindGroup({ layout: this._resultPipeline.getBindGroupLayout(0), entries: [
      { binding: 0, resource: { buffer: output.data } },
      { binding: 1, resource: { buffer: alpha.data } },
      { binding: 2, resource: this._rawTexture.createView() },
      { binding: 3, resource: this.renderer.backend.get(this.source).texture.createView() },
      { binding: 4, resource: this.renderer.backend.get(aux.textures[0]).texture.createView() },
    ] });
    const encoder = device.createCommandEncoder();
    const pass = encoder.beginComputePass();
    pass.setPipeline(this._resultPipeline);
    pass.setBindGroup(0, bindGroup);
    pass.dispatchWorkgroups(Math.ceil(width / 8), Math.ceil(height / 8));
    pass.end();
    device.queue.submit([encoder.finish()]);
  }
}
