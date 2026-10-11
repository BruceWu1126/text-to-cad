// A host installs cadgen's display tolerance before a STEP model is drawn (the web viewer from
// its server info, the CAD app from its launch). These tests draw as a host, with the one
// cadgen wrote for them (`@text-to-cad/core/lib/surf/fixtures/make_fixtures.py`).
import { installTestDisplayTessellation } from '@text-to-cad/core/lib/surf/testing.js';

installTestDisplayTessellation();
