import { cleanup, render } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { DocumentGlyph } from '../../../dist/file-viewer/navigation/DocumentGlyph.js';

afterEach(cleanup);

// What a glyph draws: a lucide icon by its class (`lucide-box`), the viewer's own file glyphs by their kind.
const icon = (props: { model?: boolean; path: string }) => {
  const { container } = render(<DocumentGlyph {...props} />);
  const svg = container.querySelector('svg')!;
  return svg.getAttribute('data-file-glyph') ?? [...svg.classList].find(name => name.startsWith('lucide-'));
};

it('draws a model as the cube and every CAD file as a file glyph of its kind, as the explorer, the Home and the name menu do', () => {
  expect(icon({ model: true, path: '6f1c2a4e-1b2c-5d3e-8f4a-0123456789ab' })).toBe('lucide-box');
  const kinds = Object.fromEntries(['a.step', 'a.stp', 'a.xbf', 'a.stl', 'a.3mf', 'a.glb', 'a.dxf', 'a.urdf', 'a.srdf', 'a.sdf'].map(path => [path, icon({ path })]));
  expect(kinds).toEqual({
    'a.step': 'solid', 'a.stp': 'solid', 'a.xbf': 'solid', 'a.stl': 'mesh', 'a.3mf': 'mesh',
    'a.glb': 'scene', 'a.dxf': 'drawing', 'a.urdf': 'mechanism', 'a.srdf': 'mechanism', 'a.sdf': 'mechanism',
  });
  // Drawn as lucide's are: its 24-unit grid, its stroke unless the list asks for a lighter one.
  const { container } = render(<DocumentGlyph path="a.step" strokeWidth={1.5} />);
  expect([container.querySelector('svg')!.getAttribute('viewBox'), container.querySelector('svg')!.getAttribute('stroke-width')]).toEqual(['0 0 24 24', '1.5']);
});
