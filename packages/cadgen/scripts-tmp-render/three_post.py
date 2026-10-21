"""Post for the three.js frames: half-float linear -> OIDN (colour only) -> Blender's OCIO AgX grade."""
import json, sys, time
from pathlib import Path
import numpy as np
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parent))
from grade import grade
import pyoidn
src, dst = Path(sys.argv[1]), Path(sys.argv[2])
single = sys.argv[3] if len(sys.argv) > 3 else None
meta = json.loads((src / "meta.json").read_text())
W, H = meta["W"], meta["H"]
dst.mkdir(parents=True, exist_ok=True)
post = []
for p in sorted(src.glob("*.f16")):
    t = time.time()
    img = np.frombuffer(p.read_bytes(), np.float16).reshape(H, W, 3).astype(np.float32)
    img = np.nan_to_num(img, nan=0.0, posinf=0.0)
    den = pyoidn.denoise(np.ascontiguousarray(img), hdr=True, quality="high")
    name = single if single else p.stem + ".png"
    Image.fromarray(grade(den)).save(dst / name)
    post.append(round(time.time() - t, 3))
meta["post_s"] = post
meta["max_linear"] = float(img.max())
(src / "meta.json").write_text(json.dumps(meta, indent=1))
print("POST", len(post), "frames, mean", round(sum(post) / len(post), 3), "s; last frame max linear", round(float(img.max()), 3))
