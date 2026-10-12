/**
 * Fast gas flow (lite), Tier 3: steady compressible flow of an ideal gas, the step up from Flow (`cfd`) for a
 * gas past about Mach 0.3, where its density changes with its speed. Beside Flow's fields (the wall pressure
 * here above the outlet's) it writes the Mach number (the speed over the local speed of sound) and the gas
 * temperature, and it also judges the highest Mach number.
 */
import { FLOW } from "./cfd.js";
import { planned } from "./stub.js";

export default planned({
  ...FLOW, name: "cfd_compressible", word: "Fast gas flow", limitWord: "Ideal gas",
  checks: ["pressure_drop", "velocity", "mach", "stress", "displacement"],
});
