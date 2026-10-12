/**
 * Turbulent flow (lite), Tier 3: steady RANS (k-omega SST, wall functions, incompressible), the step up from
 * Flow (`cfd`) past the laminar range. It writes what Flow writes (its wall shear the wall function's) and
 * never says Flow's "unreliable above Re", which is the laminar solver's warning.
 */
import { FLOW } from "./cfd.js";
import { planned } from "./stub.js";

export default planned({ ...FLOW, name: "cfd_turbulent", word: "Turbulent flow", limitWord: "RANS", checks: ["pressure_drop", "velocity", "stress", "displacement"] });
