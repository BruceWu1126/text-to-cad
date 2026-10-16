import assert from 'node:assert/strict';
import test from 'node:test';

import { promptView, promptViews, sameView } from './views.js';

/** A prompt's views: the view on screen and those the annotation was drawn in, as snapshot jobs say them. */

const camera = (overrides = {}) => ({ position: [0, -100, 0], target: [0, 0, 0], up: [0, 0, 1], orthographicHalfHeight: 50, ...overrides });
const view = (overrides = {}) => ({ camera: camera(), width: 1600, height: 1200, ...overrides });
// The camera turned `degrees` about the up axis.
const turned = degrees => {
  const radians = degrees * Math.PI / 180;
  return camera({ position: [100 * Math.sin(radians), -100 * Math.cos(radians), 0] });
};

test("a view is the viewer's camera at the prompt's size, with what places or hides the parts and nothing of how it looks", () => {
  const made = promptView({ camera: camera(), width: 800, height: 600 }, {
    display: { mode: 'render', clip: { enabled: true, axis: 'x', at: 2, invert: false }, exploded: { enabled: false, amount: 0.5 } },
    state: { kinematics: { elbow: 30 }, jointValues: {}, hide: ['o1.2'], focus: [] },
  });
  assert.deepEqual(made, {
    camera: camera(), width: 1600, height: 1200,
    display: { clip: { enabled: true, axis: 'x', at: 2, invert: false } },
    kinematics: { elbow: 30 },
    selection: { hide: ['o1.2'] },
  });
  assert.equal(promptView(null), null);
  // Explode is the view's where it spreads the parts, never on at no amount.
  const spread = amount => promptView({ camera: camera(), width: 800, height: 600 }, { display: { exploded: { enabled: true, amount } } }).display;
  assert.deepEqual([spread(0), spread(0.5)], [undefined, { exploded: { enabled: true, amount: 0.5 } }]);
});

test("a routine's kept frame is the view's animation, a snapshot job's {clip, time}", () => {
  const made = promptView({ camera: camera(), width: 800, height: 600 }, { state: { animation: { clip: 'swing', time: 1.5 } } });
  assert.deepEqual(made.animation, { clip: 'swing', time: 1.5 });
  assert.equal(promptView({ camera: camera(), width: 800, height: 600 }, { state: { animation: null } }).animation, undefined);
  assert.ok(sameView(view({ animation: { clip: 'swing', time: 1.5 } }), view({ animation: { clip: 'swing', time: 1.5 } })));
  assert.ok(!sameView(view(), view({ animation: { clip: 'swing', time: 1.5 } })));
  assert.ok(!sameView(view({ animation: { clip: 'swing', time: 1 } }), view({ animation: { clip: 'swing', time: 1.5 } })));
});

test('a camera nudged less than 2°, zoomed or panned less than 5% is the same view; more, or any other change, is another', () => {
  assert.ok(sameView(view(), view({ camera: turned(1.5) })));
  assert.ok(!sameView(view(), view({ camera: turned(3) })));
  assert.ok(sameView(view(), view({ camera: camera({ orthographicHalfHeight: 52 }) })));
  assert.ok(!sameView(view(), view({ camera: camera({ orthographicHalfHeight: 60 }) })));
  assert.ok(sameView(view(), view({ camera: camera({ target: [3, 0, 0], position: [3, -100, 0] }) })));
  assert.ok(!sameView(view(), view({ camera: camera({ target: [8, 0, 0], position: [8, -100, 0] }) })));
  assert.ok(!sameView(view(), view({ display: { exploded: { enabled: true, amount: 1 } } })));
  assert.ok(!sameView(view(), view({ selection: { hide: ['o1.2'] } })));
});

test("a prompt carries the view on screen, then each drawn view that still has ink; a drawn view the same as the one on screen stands for it", () => {
  const drawn = [{ ...view(), label: 'view 1' }, { ...view({ camera: turned(90) }), label: 'view 2' }, { ...view({ camera: turned(180) }), label: 'view 3' }];
  const ink = [{ view: 'view 1' }, { view: 'view 3' }];
  // Sent from where view 1 was drawn: it stands for the view on screen.
  assert.deepEqual(promptViews(view({ camera: turned(1) }), drawn, ink).map(entry => entry.label), ['view 1', 'view 3']);
  // Sent from elsewhere: the view on screen leads.
  assert.deepEqual(promptViews(view({ camera: turned(45) }), drawn, ink).map(entry => entry.label), ['current', 'view 1', 'view 3']);
  // No ink: the view on screen alone.
  assert.deepEqual(promptViews(view(), drawn, []).map(entry => entry.label), ['current']);
});
