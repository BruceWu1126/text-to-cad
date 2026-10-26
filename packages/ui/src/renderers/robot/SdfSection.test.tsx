import { cleanup, render } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import SdfSection from '../../../dist/renderers/robot/SdfSection.js';
import { fixturePayload } from './__tests__/robotFixtures.js';

afterEach(cleanup);

// What an SDF says of itself beyond its links and joints, from cadgen's answer for `swing.sdf` (the
// robot renderer shows it as the sidebar's SDF section). The ground plane under its base is a
// collision the viewer cannot draw: counted, never refused.
it("an SDF's section says its document, its counts and its lights, and counts the geometry the viewer cannot draw", () => {
  const { container } = render(<SdfSection info={fixturePayload('swing.sdf').sdf} />);
  const text = container.textContent!.replace(/\s+/g, '');  // each label runs into its value
  for (const fact of ['Version1.9', 'Documentworld', 'Worldlab', 'Rootlinkbase', 'Modelswing', 'Links2', 'Joints1', 'Lights1',
    'Unsupportedgeom.1', 'sun/directional']) expect(text).toContain(fact);
});
