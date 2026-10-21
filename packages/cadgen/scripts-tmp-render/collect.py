"""Print every run's timing in one table (from the *.timing.json and three meta.json files)."""
import json
import statistics as st
import sys
from pathlib import Path

R = Path(sys.argv[1])
rows = []
for p in sorted(R.glob("**/*.timing.json")):
    d = json.loads(p.read_text())
    t = d.get("times") or []
    warm = t[1:] if len(t) > 1 else t
    rows.append((d["shot"], d["engine"], f"{d.get('width')}x{d.get('height')}", d.get("samples") or d.get("spp"),
                 len(t), t[0] if t else None, round(st.median(warm), 2) if warm else None, d.get("total_s"), str(p.relative_to(R))))
for p in sorted(R.glob("frames/three_*/meta.json")):
    d = json.loads(p.read_text())
    ts = [x["ms"] / 1000 for x in d["times"]]
    post = d.get("post_s") or []
    tot = [a + (post[i] if i < len(post) else 0) for i, a in enumerate(ts)]
    warm = tot[1:] if len(tot) > 1 else tot
    rows.append((d["name"], "three-webgpu", f"{d['W']}x{d['H']}", d["spp"], len(ts), round(tot[0], 2) if tot else None,
                 round(st.median(warm), 2) if warm else None, round(d.get("total_s", 0) + sum(post), 1), str(p.relative_to(R))))
w = "{:<18} {:<22} {:>10} {:>5} {:>4} {:>8} {:>8} {:>8}  {}"
print(w.format("shot", "engine", "size", "spp", "n", "first_s", "med_s", "total_s", "file"))
for r in rows:
    print(w.format(*[("" if x is None else x) for x in r]))
