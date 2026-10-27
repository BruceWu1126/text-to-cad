# Preview denoising weights

`rt_hdr_calb_cnrm.tza` is Intel Open Image Denoise's HDR model guided by clean
albedo and normal buffers. The file is an unmodified, ordinary Git blob, not an
LFS pointer. `weights/index.js` gives the renderer a module-relative URL; Vite
ships it as a local asset in the Viewer and embeds it in the single-file CAD app.
No model download occurs from an external service at runtime.

- Source: [RenderKit/oidn-weights at 28883d1769d5930e13cf7f1676dd852bd81ed9e7](https://github.com/RenderKit/oidn-weights/tree/28883d1769d5930e13cf7f1676dd852bd81ed9e7)
- File: `rt_hdr_calb_cnrm.tza`, 1,841,160 bytes.
- SHA-256: `d0057057bd76c64e3e67b449728ef8f4fd2f11ccfb74c1e59b9d50dafd50ee0e`.
- License: [Apache 2.0](LICENSE.txt), copied from the same upstream commit.

The core build copies the model and its license. The bundled third-party notice
includes the license whenever a page bundles this loader. The inference code is
the separately pinned `oidn-webgpu` npm dependency (MIT).
