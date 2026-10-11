// A host installs cadgen's display tolerance before a STEP model is drawn (the web viewer from
// its server info, the CAD app from its launch). These tests draw as a host, with the
// level-of-detail tests' ladder (`@text-to-cad/core/lib/surf/testing.js`).
import { installTestTessellationLadder } from '@text-to-cad/core/lib/surf/testing.js';

installTestTessellationLadder();
