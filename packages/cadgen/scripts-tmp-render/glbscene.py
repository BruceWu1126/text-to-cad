"""A small, renderer-neutral GLB reader: nodes, world matrices (posed by a clip at time t),
mesh primitives as numpy arrays, materials with every KHR_materials_* field we export.

Used by the Mitsuba (and any non-glTF) adapter, and by `python glbscene.py FILE.glb` to
inspect what an export carries.
"""
from __future__ import annotations

import json
import struct
import sys
from dataclasses import dataclass, field

import numpy as np

COMPONENT = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


@dataclass
class Prim:
    mesh: int
    index: int
    positions: np.ndarray
    normals: np.ndarray | None
    uvs: np.ndarray | None
    tangents: np.ndarray | None
    indices: np.ndarray
    material: int | None


@dataclass
class Instance:
    node: int
    name: str
    world: np.ndarray  # 4x4, glTF metres, Y up
    prims: list[Prim] = field(default_factory=list)


def quat_to_mat(q):
    x, y, z, w = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def trs(t=None, r=None, s=None):
    m = np.eye(4)
    if r is not None:
        m[:3, :3] = quat_to_mat(r)
    if s is not None:
        m[:3, :3] = m[:3, :3] @ np.diag(s)
    if t is not None:
        m[:3, 3] = t
    return m


class GLB:
    def __init__(self, path: str):
        data = open(path, "rb").read()
        magic, version, length = struct.unpack_from("<4sII", data, 0)
        assert magic == b"glTF", "not a GLB"
        at = 12
        self.json = None
        self.bin = b""
        while at < length:
            clen, ctype = struct.unpack_from("<II", data, at)
            chunk = data[at + 8: at + 8 + clen]
            if ctype == 0x4E4F534A:
                self.json = json.loads(chunk)
            elif ctype == 0x004E4942:
                self.bin = chunk
            at += 8 + clen
        g = self.json
        self.nodes = g.get("nodes", [])
        self.meshes = g.get("meshes", [])
        self.materials = g.get("materials", [])
        self.animations = g.get("animations", [])
        self.extensions_used = g.get("extensionsUsed", [])
        self._acc_cache: dict[int, np.ndarray] = {}
        self._prim_cache: dict[tuple[int, int], Prim] = {}

    def accessor(self, i: int) -> np.ndarray:
        if i in self._acc_cache:
            return self._acc_cache[i]
        a = self.json["accessors"][i]
        bv = self.json["bufferViews"][a["bufferView"]]
        dt = np.dtype(COMPONENT[a["componentType"]])
        n, w = a["count"], WIDTH[a["type"]]
        off = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        stride = bv.get("byteStride")
        if stride and stride != dt.itemsize * w:
            raw = np.frombuffer(self.bin, dtype=np.uint8, count=stride * (n - 1) + dt.itemsize * w, offset=off)
            out = np.lib.stride_tricks.as_strided(raw, shape=(n, dt.itemsize * w), strides=(stride, 1)).copy()
            arr = out.view(dt).reshape(n, w)
        else:
            arr = np.frombuffer(self.bin, dtype=dt, count=n * w, offset=off).reshape(n, w)
        if a.get("normalized"):
            arr = arr.astype(np.float32) / np.iinfo(dt).max
        arr = arr if w > 1 else arr.reshape(n)
        self._acc_cache[i] = arr
        return arr

    def prim(self, mi: int, pi: int) -> Prim:
        key = (mi, pi)
        if key not in self._prim_cache:
            p = self.meshes[mi]["primitives"][pi]
            at = p["attributes"]
            get = lambda k: self.accessor(at[k]) if k in at else None  # noqa: E731
            idx = self.accessor(p["indices"]).astype(np.uint32) if "indices" in p else np.arange(len(get("POSITION")), dtype=np.uint32)
            self._prim_cache[key] = Prim(mi, pi, get("POSITION"), get("NORMAL"), get("TEXCOORD_0"), get("TANGENT"),
                                         idx.reshape(-1, 3), p.get("material"))
        return self._prim_cache[key]

    # --- animation -------------------------------------------------------------------
    def clip_names(self):
        return [a.get("name", f"clip{i}") for i, a in enumerate(self.animations)]

    def clip_duration(self, name: str) -> float:
        a = self._clip(name)
        return max(float(self.accessor(s["input"]).max()) for s in a["samplers"])

    def _clip(self, name):
        for a in self.animations:
            if a.get("name") == name:
                return a
        raise KeyError(name)

    def sample(self, name: str | None, t: float) -> dict[int, dict[str, np.ndarray]]:
        """Per node, the TRS overrides the clip puts it at, time t (seconds, clamped)."""
        if not name:
            return {}
        a = self._clip(name)
        out: dict[int, dict[str, np.ndarray]] = {}
        for ch in a["channels"]:
            s = a["samplers"][ch["sampler"]]
            times = self.accessor(s["input"]).astype(np.float64)
            vals = self.accessor(s["output"]).astype(np.float64)
            path = ch["target"]["path"]
            interp = s.get("interpolation", "LINEAR")
            tt = min(max(t, times[0]), times[-1])
            k = int(np.clip(np.searchsorted(times, tt, side="right") - 1, 0, len(times) - 2)) if len(times) > 1 else 0
            if interp == "CUBICSPLINE":
                vals = vals.reshape(len(times), 3, -1)  # in-tangent, value, out-tangent
                if len(times) == 1:
                    v = vals[0, 1]
                else:
                    t0, t1 = times[k], times[k + 1]
                    d = t1 - t0
                    u = (tt - t0) / d
                    p0, m0 = vals[k, 1], vals[k, 2] * d
                    p1, m1 = vals[k + 1, 1], vals[k + 1, 0] * d
                    u2, u3 = u * u, u * u * u
                    v = (2 * u3 - 3 * u2 + 1) * p0 + (u3 - 2 * u2 + u) * m0 + (-2 * u3 + 3 * u2) * p1 + (u3 - u2) * m1
            else:
                if len(times) == 1:
                    v = vals[0]
                else:
                    t0, t1 = times[k], times[k + 1]
                    u = 0.0 if t1 == t0 else (tt - t0) / (t1 - t0)
                    v = vals[k] * (1 - u) + vals[k + 1] * u if interp == "LINEAR" else vals[k]
            if path == "rotation":
                v = v / np.linalg.norm(v)
            out.setdefault(ch["target"]["node"], {})[path] = np.asarray(v).reshape(-1)
        return out

    # --- scene --------------------------------------------------------------------
    def local(self, i: int, overrides) -> np.ndarray:
        n = self.nodes[i]
        o = overrides.get(i, {})
        if "matrix" in n and not o:
            return np.array(n["matrix"], dtype=np.float64).reshape(4, 4).T
        return trs(o.get("translation", n.get("translation")), o.get("rotation", n.get("rotation")),
                   o.get("scale", n.get("scale")))

    def instances(self, clip: str | None = None, t: float = 0.0) -> list[Instance]:
        overrides = self.sample(clip, t)
        scene = self.json.get("scenes", [{}])[self.json.get("scene", 0)]
        out: list[Instance] = []
        stack = [(r, np.eye(4)) for r in scene.get("nodes", [])]
        while stack:
            i, parent = stack.pop()
            world = parent @ self.local(i, overrides)
            n = self.nodes[i]
            if "mesh" in n:
                inst = Instance(i, n.get("name", f"node{i}"), world)
                inst.prims = [self.prim(n["mesh"], pi) for pi in range(len(self.meshes[n["mesh"]]["primitives"]))]
                out.append(inst)
            for c in n.get("children", []):
                stack.append((c, world))
        return out

    def bounds(self, clip=None, t=0.0):
        lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
        for inst in self.instances(clip, t):
            for p in inst.prims:
                w = (inst.world[:3, :3] @ p.positions.T).T + inst.world[:3, 3]
                lo, hi = np.minimum(lo, w.min(0)), np.maximum(hi, w.max(0))
        return lo, hi

    def material(self, i: int | None) -> dict:
        """Flattened look: every field we export, glTF defaults filled in."""
        m = self.materials[i] if i is not None else {}
        pbr = m.get("pbrMetallicRoughness", {})
        ext = m.get("extensions", {})
        look = {
            "name": m.get("name", f"mat{i}"),
            "base_color": pbr.get("baseColorFactor", [1, 1, 1, 1]),
            "metallic": pbr.get("metallicFactor", 1.0),
            "roughness": pbr.get("roughnessFactor", 1.0),
            "emissive": m.get("emissiveFactor", [0, 0, 0]),
            "alpha_mode": m.get("alphaMode", "OPAQUE"),
            "double_sided": m.get("doubleSided", False),
            "ior": ext.get("KHR_materials_ior", {}).get("ior", 1.5),
            "transmission": ext.get("KHR_materials_transmission", {}).get("transmissionFactor", 0.0),
            "thickness": ext.get("KHR_materials_volume", {}).get("thicknessFactor", 0.0),
            "attenuation_color": ext.get("KHR_materials_volume", {}).get("attenuationColor", [1, 1, 1]),
            "attenuation_distance": ext.get("KHR_materials_volume", {}).get("attenuationDistance", float("inf")),
            "clearcoat": ext.get("KHR_materials_clearcoat", {}).get("clearcoatFactor", 0.0),
            "clearcoat_roughness": ext.get("KHR_materials_clearcoat", {}).get("clearcoatRoughnessFactor", 0.0),
            "anisotropy": ext.get("KHR_materials_anisotropy", {}).get("anisotropyStrength", 0.0),
            "anisotropy_rotation": ext.get("KHR_materials_anisotropy", {}).get("anisotropyRotation", 0.0),
            "emissive_strength": ext.get("KHR_materials_emissive_strength", {}).get("emissiveStrength", 1.0),
            "textures": [k for k in list(pbr) + list(m) if k.endswith("Texture")],
            "extensions": sorted(ext),
        }
        return look


def summary(path: str) -> None:
    g = GLB(path)
    insts = g.instances()
    tris = sum(len(p.indices) for i in insts for p in i.prims)
    uniq = sum(len(g.prim(mi, pi).indices) for mi, m in enumerate(g.meshes) for pi in range(len(m["primitives"])))
    lo, hi = g.bounds()
    print(f"{path}\n  extensionsUsed: {g.extensions_used}")
    print(f"  nodes {len(g.nodes)}  meshes {len(g.meshes)}  instances {len(insts)}  tris placed {tris:,}  unique {uniq:,}")
    print(f"  bounds (m): {np.round(lo, 4)} .. {np.round(hi, 4)}  size {np.round(hi - lo, 4)}")
    attrs = set()
    for m in g.meshes:
        for p in m["primitives"]:
            attrs |= set(p["attributes"])
    print(f"  attributes: {sorted(attrs)}")
    for a in g.animations:
        print(f"  clip {a.get('name')}: {len(a['channels'])} channels, duration {g.clip_duration(a.get('name')):.3f}s, "
              f"interp {sorted({s.get('interpolation', 'LINEAR') for s in a['samplers']})}, extras {a.get('extras')}")
    print(f"  materials {len(g.materials)}:")
    for i in range(len(g.materials)):
        L = g.material(i)
        bits = [f"rgb={np.round(L['base_color'][:3], 3).tolist()}", f"a={L['base_color'][3]:.2f}", f"M={L['metallic']:.2f}", f"R={L['roughness']:.2f}"]
        for k in ("ior", "transmission", "thickness", "clearcoat", "clearcoat_roughness", "anisotropy", "emissive_strength"):
            dflt = {"ior": 1.5, "emissive_strength": 1.0}.get(k, 0.0)
            if abs(L[k] - dflt) > 1e-6:
                bits.append(f"{k}={L[k]:.3g}")
        if L["attenuation_distance"] != float("inf"):
            bits.append(f"att={np.round(L['attenuation_color'], 3).tolist()}@{L['attenuation_distance']:.3g}")
        if any(L["emissive"]):
            bits.append(f"emis={np.round(L['emissive'], 3).tolist()}")
        if L["alpha_mode"] != "OPAQUE":
            bits.append(L["alpha_mode"])
        print(f"    [{i}] {L['name'][:38]:38s} {' '.join(bits)}")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        summary(p)
