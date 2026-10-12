import assert from "node:assert/strict";
import test from "node:test";

import { stepArtifactStatusMessage } from "./stepArtifactStatus.js";
import { buildViewerMeshAlert } from "./viewerAlerts.js";

test("the viewer card words a failed artifact from the one message table", () => {
  const artifact = { ok: false, error: "missing_source_path" };
  const entry = { file: "part.step", kind: "part", artifact };
  assert.equal(stepArtifactStatusMessage(artifact), "Generated GLB metadata is missing its source path.");
  assert.equal(buildViewerMeshAlert(entry, false, "", null)?.message, `“part.step”: ${stepArtifactStatusMessage(artifact)}`);
});
