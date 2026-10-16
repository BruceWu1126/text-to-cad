import assert from "node:assert/strict";
import test from "node:test";

import { ROBOT_TOOL, ROBOT_TOOL_MODES } from "./tools.js";

test("a robot's one tool is Select: the tool in hand is never saved, so there is nothing to restore into", () => {
  assert.equal(ROBOT_TOOL_MODES.defaultMode, ROBOT_TOOL.SELECT);
  assert.deepEqual(Object.values(ROBOT_TOOL), [ROBOT_TOOL.SELECT]);
  assert.equal(ROBOT_TOOL_MODES.normalize("pose"), ROBOT_TOOL.SELECT, "Position is Select's filter, not a tool");
  assert.equal(ROBOT_TOOL_MODES.normalize("annotate"), ROBOT_TOOL.SELECT, "a tool a robot has none of is Select");
  assert.equal("restore" in ROBOT_TOOL_MODES, false);
});
