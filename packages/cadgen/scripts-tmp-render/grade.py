"""One grade for every renderer's linear output: Blender's own OCIO config, AgX view, a look.
So a Mitsuba (or any linear) frame is tone mapped exactly as Blender maps its own."""
from pathlib import Path

import numpy as np
import PyOpenColorIO as OCIO

CONFIG = Path(__file__).resolve().parent.parent / "app/Blender.app/Contents/Resources/5.2/datafiles/colormanagement/config.ocio"
_proc = {}


def processor(look="AgX - Medium High Contrast", view="AgX", src="Linear Rec.709"):
    key = (look, view, src)
    if key not in _proc:
        cfg = OCIO.Config.CreateFromFile(str(CONFIG))
        pipe = OCIO.LegacyViewingPipeline()
        pipe.setDisplayViewTransform(OCIO.DisplayViewTransform(src=src, display="sRGB", view=view))
        if look:
            pipe.setLooksOverrideEnabled(True)
            pipe.setLooksOverride(look)
        _proc[key] = pipe.getProcessor(cfg).getDefaultCPUProcessor()
    return _proc[key]


def grade(linear: np.ndarray, exposure: float = 0.0, **kw) -> np.ndarray:
    img = np.ascontiguousarray(linear[..., :3].astype(np.float32) * (2.0 ** exposure))
    processor(**kw).applyRGB(img)
    return (np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8)
