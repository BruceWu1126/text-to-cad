import { expect, it } from 'vitest';
import {
  BOARD_DISPLAY_DEFAULTS, boardDisplayAirwires, boardDisplayIsCustom, boardDrawView, readBoardDisplay, selectBoardMode,
} from '../../../../dist/renderers/plot/board/BoardDisplay.js';

// A board's sheet as the plot lays it out: its layers by kind, courtyards, mask and paste included.
const SHEET = {
  layers: [
    ['B.CrtYd', 'courtyard'], ['B.Fab', 'fab'], ['B.SilkS', 'silk'], ['B.Mask', 'mask'], ['B.Cu', 'copper'], ['F.Cu', 'copper'],
    ['F.Mask', 'mask'], ['F.Paste', 'paste'], ['F.SilkS', 'silk'], ['F.Fab', 'fab'], ['F.CrtYd', 'courtyard'], ['Edge.Cuts', 'outline'],
    ['vias', 'via'], ['ratsnest', 'ratsnest'], ['drills', 'drill'],
  ].map(([id, kind]) => ({ id, kind })),
};

it('reads a stored display, a display stored before modes as its mode', () => {
  expect(readBoardDisplay(null)).toEqual(BOARD_DISPLAY_DEFAULTS);
  expect(readBoardDisplay({ mode: 'placement', side: 'bottom' })).toEqual({ mode: 'placement', side: 'bottom', poured: true });
  expect(readBoardDisplay({ layers: 'all', poured: false })).toEqual({ mode: 'board', side: 'top', poured: false });
  expect(readBoardDisplay({ layers: 'assembly' }).mode).toBe('assembly');
  expect(readBoardDisplay({ mode: 'nonsense' }).mode).toBe('board');
});

it('draws each mode its layers: courtyards for assembly and placement, no copper for placement', () => {
  const drawn = (mode: string) => boardDrawView({ ...BOARD_DISPLAY_DEFAULTS, mode }, SHEET).layers;
  // Board is the board as it is made: every layer but the courtyards, mask and paste included.
  expect(drawn('board')).toEqual(['B.Fab', 'B.SilkS', 'B.Mask', 'B.Cu', 'F.Cu', 'F.Mask', 'F.Paste', 'F.SilkS', 'F.Fab', 'Edge.Cuts', 'vias', 'ratsnest', 'drills']);
  expect(drawn('copper')).toEqual(['B.Cu', 'F.Cu', 'Edge.Cuts', 'vias', 'ratsnest', 'drills']);
  expect(drawn('assembly')).toEqual(['B.CrtYd', 'B.Fab', 'B.SilkS', 'F.SilkS', 'F.Fab', 'F.CrtYd', 'Edge.Cuts', 'ratsnest', 'drills']);
  // Placement's airwires are every connection, drawn by the overlay: the plot's ratsnest (only the unrouted) is left out.
  // Nor are the vias and drills: a via is routing; the overlay draws the pads' holes and the board's.
  expect(drawn('placement')).toEqual(['B.CrtYd', 'B.Fab', 'F.Fab', 'F.CrtYd', 'Edge.Cuts']);
  expect(boardDisplayAirwires({ ...BOARD_DISPLAY_DEFAULTS, mode: 'placement' })).toBe(true);
  expect(boardDisplayAirwires({ ...BOARD_DISPLAY_DEFAULTS, mode: 'copper' })).toBe(false);
  expect(boardDisplayAirwires(null)).toBe(false);
});

it('reads as Custom once a setting leaves its mode, and choosing a mode reapplies it, keeping the side', () => {
  const unpoured = { mode: 'copper', side: 'bottom', poured: false };
  expect(boardDisplayIsCustom(unpoured)).toBe(true);
  expect(boardDisplayIsCustom({ ...unpoured, poured: true })).toBe(false);
  expect(selectBoardMode(unpoured, 'copper')).toEqual({ mode: 'copper', side: 'bottom', poured: true });
  expect(selectBoardMode(unpoured, 'placement')).toEqual({ mode: 'placement', side: 'bottom', poured: true });
});
