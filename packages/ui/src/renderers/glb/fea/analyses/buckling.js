/**
 * Buckling: how many times this load the part takes before its shape gives way sideways. Its series is
 * the buckling shapes (`Mode 1 · 13.3×`), each scaled to a largest motion of 1 mm, plus the prestress's
 * stress, the same in every frame. The load control moves its load factor inversely (at k times the load
 * it buckles at λ/k).
 */
import { heldRows, madeOfRows, pushedRows } from "../setup.js";
import { planned } from "./stub.js";

/** Buckle: the load going on, from none to the load chosen, until the shape gives (GlbRenderer plays it as a load ramp). */
export const BUCKLE = Object.freeze({ id: "fea:buckle", label: "Buckle", kind: "load_ramp" });

export default planned({
  name: "buckling", word: "Buckling", scalesWithLoad: true,
  checks: ["buckling"],
  setupGroups: [heldRows, pushedRows, madeOfRows],
  routine: () => BUCKLE,
});

