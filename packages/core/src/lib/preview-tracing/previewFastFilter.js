import { ExternalTexture } from 'three/webgpu';

// One small guided spatial pass for interactive views. Filter premultiplied
// radiance and coverage separately, then return straight RGB for the tracer's
// normal presentation pass. No history or extra bounce approximation is used.
const shader = /* wgsl */`
  @group(0) @binding(0) var traced: texture_2d<f32>;
  @group(0) @binding(1) var albedo: texture_2d<f32>;
  @group(0) @binding(2) var normals: texture_2d<f32>;
  @group(0) @binding(3) var output: texture_storage_2d<rgba16float, write>;

  @compute @workgroup_size(8, 8)
  fn main(@builtin(global_invocation_id) id: vec3u) {
    let size = textureDimensions(output);
    if (id.x >= size.x || id.y >= size.y) { return; }
    let pos = vec2i(id.xy);
    let center = textureLoad(traced, pos, 0);
    let centerAlbedo = textureLoad(albedo, pos, 0);
    if (center.a == 0.0 && centerAlbedo.a == 0.0) {
      textureStore(output, pos, vec4f(0.0));
      return;
    }
    let centerNormalTexel = textureLoad(normals, pos, 0);
    var premultiplied = vec3f(0.0);
    var coverage = 0.0;
    var weights = 0.0;
    for (var y = -2; y <= 2; y++) {
      for (var x = -2; x <= 2; x++) {
        let q = clamp(pos + vec2i(x, y), vec2i(0), vec2i(size) - vec2i(1));
        let radiance = textureLoad(traced, q, 0);
        let guide = textureLoad(albedo, q, 0);
        let normalTexel = textureLoad(normals, q, 0);
        let colorDelta = centerAlbedo.rgb - guide.rgb;
        var normalDifference = 0.0;
        if (centerNormalTexel.a > 0.0 && normalTexel.a > 0.0) {
          // MSAA resolves encoded guide RGB premultiplied by its coverage.
          let centerNormal = centerNormalTexel.rgb / centerNormalTexel.a * 2.0 - 1.0;
          let normal = normalTexel.rgb / normalTexel.a * 2.0 - 1.0;
          let a = centerNormal / max(length(centerNormal), 0.000001);
          let b = normal / max(length(normal), 0.000001);
          normalDifference = dot(a - b, a - b);
        }
        let spatial = f32(3 - abs(x)) * f32(3 - abs(y));
        let edge = exp(-8.0 * dot(colorDelta, colorDelta) - 6.0 * normalDifference
          - 8.0 * abs(centerAlbedo.a - guide.a));
        let w = spatial * edge;
        let alpha = select(0.0, clamp(radiance.a, 0.0, 1.0), radiance.a == radiance.a);
        let rgb = select(vec3f(0.0), clamp(radiance.rgb, vec3f(-65504.0), vec3f(65504.0)), all(radiance.rgb == radiance.rgb));
        premultiplied += rgb * alpha * w;
        coverage += alpha * w;
        weights += w;
      }
    }
    let alpha = clamp(coverage / max(weights, 0.000001), 0.0, 1.0);
    let rgb = select(vec3f(0.0), premultiplied / max(coverage, 0.000001), coverage > 0.0);
    textureStore(output, pos, vec4f(rgb, alpha));
  }
`;

export class PreviewFastFilter {
  constructor(renderer) {
    this.renderer = renderer;
    this.pipeline = null;
    this.raw = null;
    this.texture = null;
  }

  apply(source, guides) {
    const renderer = this.renderer, device = renderer.backend.device;
    const { width, height } = source;
    if (!this.pipeline) {
      const layout = device.createBindGroupLayout({ entries: [
        ...[0, 1, 2].map(binding => ({ binding, visibility: GPUShaderStage.COMPUTE, texture: { sampleType: 'unfilterable-float' } })),
        { binding: 3, visibility: GPUShaderStage.COMPUTE, storageTexture: { access: 'write-only', format: 'rgba16float' } },
      ] });
      const module = device.createShaderModule({ code: shader });
      this.pipeline = device.createComputePipeline({ layout: device.createPipelineLayout({ bindGroupLayouts: [layout] }), compute: { module, entryPoint: 'main' } });
    }
    if (!this.raw || this.raw.width !== width || this.raw.height !== height) {
      this.texture?.dispose(); this.raw?.destroy();
      this.raw = device.createTexture({ size: [width, height], format: 'rgba16float',
        usage: GPUTextureUsage.STORAGE_BINDING | GPUTextureUsage.TEXTURE_BINDING | GPUTextureUsage.COPY_SRC });
      this.texture = new ExternalTexture(this.raw);
      this.texture.image = { width, height };
    }
    renderer.initTexture(source);
    for (const guide of guides.textures) renderer.initTexture(guide);
    const bindGroup = device.createBindGroup({ layout: this.pipeline.getBindGroupLayout(0), entries: [
      { binding: 0, resource: renderer.backend.get(source).texture.createView() },
      { binding: 1, resource: renderer.backend.get(guides.textures[0]).texture.createView() },
      { binding: 2, resource: renderer.backend.get(guides.textures[1]).texture.createView() },
      { binding: 3, resource: this.raw.createView() },
    ] });
    const encoder = device.createCommandEncoder(), pass = encoder.beginComputePass();
    pass.setPipeline(this.pipeline); pass.setBindGroup(0, bindGroup);
    pass.dispatchWorkgroups(Math.ceil(width / 8), Math.ceil(height / 8));
    pass.end(); device.queue.submit([encoder.finish()]);
    return this.texture;
  }

  dispose() { this.texture?.dispose(); this.raw?.destroy(); this.texture = null; this.raw = null; this.pipeline = null; }
}
