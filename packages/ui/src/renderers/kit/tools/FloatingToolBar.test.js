import assert from 'node:assert/strict';
import test from 'node:test';
import FloatingToolBar, { touchStripFit } from './FloatingToolBar.js';
import { render, elements } from '../../../../scripts/reactHarness.mjs';

const tool = (id, extra = {}) => ({ id, label: id.toUpperCase(), icon: null, onSelect() {}, ...extra });
const buttons = tree => elements(tree).filter(node => typeof node.props.label === 'string');

test('the strip draws the tools it is handed, in order, and knows none by name', () => {
  const view = render(FloatingToolBar, { tools: [tool('b', { active: true }), tool('a', { disabled: true })] });
  assert.deepEqual(buttons(view.tree).map(node => [node.props.label, node.props.active, node.props['aria-pressed'], node.props.disabled]),
    [['B', true, true, undefined], ['A', false, false, true]]);
  assert.deepEqual(elements(view.tree).filter(node => node.props.role === 'group').map(node => node.props['aria-label']), ['Interaction tools']);
  view.unmount();
});

test('every press reaches onSelect, and a press is all a tool is: no tool opens a menu from the strip', () => {
  const pressed = [];
  const view = render(FloatingToolBar, { tools: [
    tool('plain', { onSelect: () => pressed.push('plain') }),
    tool('active', { active: true, description: 'described', onSelect: () => pressed.push('active') }),
  ] });
  const [plain, active] = buttons(view.tree);
  plain.props.onClick();
  active.props.onClick();
  active.props.onClick();
  assert.deepEqual(pressed, ['plain', 'active', 'active'], 'a press on an active tool is the tool\'s too');
  assert.equal(active.props['aria-description'], 'described');
  assert.equal([plain, active].some(node => node.props.onPointerDown || node.props.onKeyDown), false);
  view.unmount();
});

test('a phone\'s strip keeps its 36px tiles in one row and hands what does not fit, from its end, to More', () => {
  // A STEP's strip: Select with its filter's chevron, five more tools, then Display and the Studio.
  const entries = [{ width: 56, view: false }, ...Array(5).fill({ width: 36, view: false }), { width: 36, view: true }, { width: 36, view: true }];
  // A 375px phone (the column 359px): everything fits, no More.
  assert.equal(touchStripFit(entries, 359), 8);
  // A 320px phone (304px): Select and the next three tools, then More (the last tool, Display, the Studio).
  assert.equal(touchStripFit(entries, 304), 5);
  // Narrower still: fewer tools before More.
  assert.equal(touchStripFit(entries, 200), 3);
});

test('on a phone the file sidebar\'s tiles lead the strip, ruled off from the tools, and are the last to go into More', () => {
  // A STEP's phone strip: Features and Position, a rule, Select with its chevron and five more tools, a rule, Display and the Studio.
  const entries = [{ width: 36, view: false, section: true }, { width: 36, view: false, section: true },
    { width: 56, view: false }, ...Array(5).fill({ width: 36, view: false }), { width: 36, view: true }, { width: 36, view: true }];
  // All of it: the frame, nine tiles and Select with its chevron, nine gaps, and two rules with a gap each.
  assert.equal(touchStripFit(entries, 10 + 9 * 36 + 56 + 9 * 4 + 2 * 9), 10);
  // A 375px phone (the column 359px): the view's controls and the last tools go first, behind More.
  assert.equal(touchStripFit(entries, 359), 7);
  // Narrow enough to keep only the tiles, then only the first of them.
  assert.equal(touchStripFit(entries, 10 + 3 * 36 + 2 * 4), 2);
  assert.equal(touchStripFit(entries, 10 + 2 * 36 + 4), 1);
});
