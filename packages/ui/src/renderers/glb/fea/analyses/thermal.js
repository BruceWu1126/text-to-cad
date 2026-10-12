/**
 * Heat: the steady temperature from faces held at a temperature, heat put in, air carrying it away and
 * radiation. The hottest point is measured from the coolest temperature the study sets; nothing deforms.
 */
import { fieldAndDeformation } from "../controls.js";
import { planned } from "./stub.js";
import { cooledRows, heatedRows, keptAtRows, madeOfRows, radiatesRows } from "../setup.js";

/** The heat analyses' setup: kept at, heated, cooled by air, radiates, made of. */
export const HEAT_SETUP = Object.freeze([keptAtRows, heatedRows, cooledRows, radiatesRows, madeOfRows]);

/** The heat analyses' markers: a dot where a temperature is held, a wavy arrow where heat goes in, strokes where air cools it. */
export const HEAT_MARKERS = Object.freeze(["temperature", "heat", "convection"]);

/** The field select alone, over every field the result carries (temperature first): a temperature has no deformation. */
export const fieldOnly = (result) => [fieldAndDeformation(result)[0]];

export default planned({
  name: "thermal", word: "Heat", noun: "this heat",
  checks: ["temperature"],
  defaultControls: fieldOnly,
  setupGroups: HEAT_SETUP,
  markers: HEAT_MARKERS, displayTitle: "Heat inputs and temperatures",
});

