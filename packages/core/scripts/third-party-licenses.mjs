/**
 * The third-party notice a page's build ships beside it: every package of `node_modules` whose code
 * is in the build's chunks (its own and its workers'), by name and version, with the licence its
 * package.json declares and the licence text it ships (LICENSE, LICENCE or COPYING). The list
 * comes from what the bundler put in the chunks, so it is the build's and nobody keeps it.
 *
 * `thirdPartyLicenses()` returns `plugin()`, for the page's build, which writes the notice into the
 * output folder once the page is written (`THIRD_PARTY_LICENSES.txt`), and `worker`, the same
 * collector for the build's `worker.plugins`: a worker is bundled by a build of its own, and the
 * packages only it inlines must be named too. A package that declares no licence fails the build.
 * One that declares a licence and ships no text of it is named with its author and repository,
 * where the text is kept, as its package.json gives them.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

export const NOTICE_FILE = 'THIRD_PARTY_LICENSES.txt';
const LICENSE_FILE = /^(?:licen[cs]e|copying)(?:[.-].*)?$/i;

/** The package folder a bundled module is in, or null for one of this repository's own. */
export function packageDirOf(moduleId) {
  const id = String(moduleId).split('?')[0].replaceAll('\\', '/');
  if (!id || id.startsWith('\0')) return null;
  const at = id.lastIndexOf('/node_modules/');
  if (at < 0) return null;
  const parts = id.slice(at + '/node_modules/'.length).split('/');
  const depth = parts[0].startsWith('@') ? 2 : 1;
  if (parts.length <= depth) return null;
  return id.slice(0, at + '/node_modules/'.length) + parts.slice(0, depth).join('/');
}

function licenseOf(manifest) {
  const declared = manifest.license ?? manifest.licenses;
  if (typeof declared === 'string') return declared.trim();
  if (Array.isArray(declared)) return declared.map(item => item?.type ?? item).filter(Boolean).join(' OR ');
  if (declared && typeof declared === 'object') return String(declared.type || '').trim();
  return '';
}

function shippedNone(manifest) {
  const author = typeof manifest.author === 'string' ? manifest.author : manifest.author?.name;
  const repository = typeof manifest.repository === 'string' ? manifest.repository : manifest.repository?.url;
  return [`The package ships no licence text; its package.json declares ${licenseOf(manifest) || 'none'}.`,
    ...(author ? [`Author: ${author}`] : []), ...(repository ? [`Repository: ${repository}`] : [])].join('\n');
}

/** Each package the modules come from, once: `{ name, version, license, text }`, sorted by name. */
export function bundledPackages(moduleIds) {
  const ids = [...moduleIds];
  const directories = new Set(ids.map(packageDirOf).filter(Boolean));
  // oidn-webgpu's Node-compatible dist entry already bundles its float16
  // dependency, so Rollup cannot report that dependency as a separate module.
  for (const id of ids) {
    if (/\/node_modules\/oidn-webgpu\/dist\/oidn\.js(?:\?|$)/.test(String(id).replaceAll('\\', '/'))) {
      directories.add(packageDirOf(createRequire(String(id).split('?')[0]).resolve('@petamoriken/float16')));
    }
  }
  const found = new Map();
  for (const dir of directories) {
    if (!dir || found.has(dir)) continue;
    const manifest = JSON.parse(fs.readFileSync(path.join(dir, 'package.json'), 'utf8'));
    const file = fs.readdirSync(dir).filter(name => LICENSE_FILE.test(name)).sort()[0];
    found.set(dir, {
      name: manifest.name, version: manifest.version, license: licenseOf(manifest),
      text: file ? fs.readFileSync(path.join(dir, file), 'utf8').trim() : shippedNone(manifest),
    });
  }
  // Vendored inference data is redistributed too. Its loader survives both the
  // Viewer's asset build and the CAD app's inline-asset build, unlike a .tza
  // asset entry (which disappears when inlined). Keep its license with either.
  if (ids.some(id => /\/lib\/preview-tracing\/weights\/index\.js(?:\?|$)/.test(String(id).replaceAll('\\', '/')))) {
    const license = fileURLToPath(new URL('../src/lib/preview-tracing/weights/LICENSE.txt', import.meta.url));
    found.set('oidn-weights', {
      name: 'Intel Open Image Denoise model weights',
      version: '28883d1769d5930e13cf7f1676dd852bd81ed9e7',
      license: 'Apache-2.0',
      text: fs.readFileSync(license, 'utf8').trim(),
    });
  }
  const unique = new Map([...found.values()].map(entry => [`${entry.name}@${entry.version}`, entry]));
  const packages = [...unique.values()].sort((a, b) => a.name.localeCompare(b.name) || a.version.localeCompare(b.version));
  const missing = packages.filter(entry => !entry.license).map(entry => `${entry.name}@${entry.version}`);
  if (missing.length) throw new Error(`These bundled packages declare no licence: ${missing.join(', ')}`);
  return packages;
}

/** The notice's text for `packages` (`bundledPackages`'). */
export function noticeText(packages) {
  const rule = '='.repeat(78);
  return [
    'The JavaScript and model data in this directory include these third-party packages,',
    'as the build that made it resolved them: each by name, version and licence, then the licence',
    'text it ships.',
    '',
    ...packages.map(entry => `  ${entry.name} ${entry.version} (${entry.license})`),
    '',
    ...packages.flatMap(entry => [rule, `${entry.name} ${entry.version} (${entry.license})`, rule, '', entry.text, '']),
  ].join('\n');
}

export function thirdPartyLicenses({ fileName = NOTICE_FILE } = {}) {
  const modules = new Set();
  const collect = (name) => ({
    name,
    generateBundle: {
      order: 'pre',
      handler(_, bundle) {
        for (const item of Object.values(bundle)) if (item.type === 'chunk') for (const id of item.moduleIds) modules.add(id);
      },
    },
  });
  return {
    worker: () => [collect('third-party-licenses:worker')],
    plugin() {
      return {
        ...collect('third-party-licenses'),
        writeBundle(options) {
          fs.writeFileSync(path.join(options.dir, fileName), noticeText(bundledPackages(modules)));
        },
      };
    },
  };
}
