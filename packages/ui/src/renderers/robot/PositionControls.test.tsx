import React from 'react';
import { act, cleanup, fireEvent, render as mount, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import PositionControls from '../../../dist/renderers/robot/PositionControls.js';
import { createPoseStore } from '../../../dist/renderers/robot/poseStore.js';
import FileSidebar from '../../../dist/renderers/kit/tools/FileSidebar.js';
import ToolPanel from '../../../dist/renderers/kit/tools/ToolPanel.js';
import { fixturePayload } from './__tests__/robotFixtures.js';

Object.assign(globalThis, { React });
// The slider primitive measures its thumb.
beforeEach(() => vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} }));
afterEach(cleanup);

// As the robot's file sidebar draws them: its Position section, whose heading holds the Pose menu.
const render = (controls: React.ReactElement) => mount(<FileSidebar layout={{ panels: {}, collapsed: {} }} onLayoutChange={() => {}}>
  <ToolPanel id="position" title="Position" label="Position controls" fit="details">{controls}</ToolPanel>
</FileSidebar>);
const poseLabel = () => screen.getByRole('button', { name: /^Pose: / }).getAttribute('aria-label');
const field = (name: string, unit: string) => screen.getByRole('textbox', { name: `${name} value in ${unit}` }) as HTMLInputElement;

it('names the pose in its section\'s heading, then a slider per control of the articulation: no sections, and no Reset of its own', () => {
  // The SRDF's "home" state is the opening pose, so the robot opens at its default.
  render(<PositionControls pose={createPoseStore(fixturePayload('arm.srdf'))}/>);
  // One section's rows: the Position section around them is the panel's, its one heading.
  expect(screen.getAllByRole('heading').map(heading => heading.textContent)).toEqual(['Position']);
  expect(poseLabel()).toBe('Pose: Default');
  expect(document.querySelector('[data-sidebar-head] [data-pose-menu]')).not.toBeNull();
  expect([field('shoulder', 'deg').value, field('lift', 'm').value, field('grip', 'm').value, field('nod', 'deg').value]).toEqual(['-28.6°', '0.1 m', '0 m', '0°']);
  // No row for a fixed joint, and none for a mimic follower: it is posed through its leader.
  expect(screen.queryByRole('textbox', { name: /camera_mount|grip_mirror/ })).toBeNull();
  // There is no Reset button: the Pose menu's Default is the way back.
  expect(screen.queryByRole('button', { name: 'Reset' })).toBeNull();
  // Each thumb is named for its control, as its number field is.
  expect(screen.getAllByRole('slider').map(thumb => thumb.getAttribute('aria-label'))).toEqual(['shoulder', 'lift', 'grip', 'nod']);
});

it('a plain description has a Pose menu too, Default its one choice, the way back to its default pose; one with nothing to move says so', () => {
  const pose = createPoseStore(fixturePayload('arm.urdf'));
  render(<PositionControls pose={pose}/>);
  expect([poseLabel(), field('shoulder', 'deg').value]).toEqual(['Pose: Default', '0°']);
  act(() => { pose.write('shoulder', 30); });
  expect(poseLabel()).toBe('Pose: Custom');
  cleanup();
  const fixed = { articulation: { schemaVersion: 2, controls: [], joints: [{ id: 'mount', parent: null, kind: 'fixed' }], carries: { mount: ['camera'] }, handles: [], poses: {}, opening: {} } };
  render(<PositionControls pose={createPoseStore(fixed)}/>);
  expect(screen.getByText('No movable joints.')).toBeTruthy();
});

it('follows the pose store, and writes to it: a typed value is clamped, and a reset shows the defaults', () => {
  const pose = createPoseStore(fixturePayload('arm.srdf'));
  render(<PositionControls pose={pose}/>);
  act(() => { pose.write('shoulder', 30); });
  expect(field('shoulder', 'deg').value).toBe('30°');
  fireEvent.change(field('lift', 'm'), { target: { value: '5' } });
  fireEvent.blur(field('lift', 'm'));
  expect(pose.getSnapshot().values.lift).toBe(0.3);
  act(() => { pose.selectGroupState(pose.groupStates.find(state => state.name === 'raised')); });
  expect(poseLabel()).toBe('Pose: raised');
  expect(field('shoulder', 'deg').value).toBe('-57.3°');
  act(() => { pose.reset(); });
  expect(pose.getSnapshot().values).toEqual(pose.defaults);
  expect(poseLabel()).toBe('Pose: Default');
});
