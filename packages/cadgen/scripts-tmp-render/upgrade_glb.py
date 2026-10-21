"""Render looks as a GLB rewrite: one renderer-neutral step, so every renderer's own glTF path
sees the same upgraded materials. Overrides are written in the engine's Material fields
(color, metallic, roughness, ior, transmission, transmission_color, transmission_depth, coat,
coat_roughness, anisotropy, emissive, emissive_strength) and translated to glTF here exactly
as cadgen's GLB export does.

Rules, per model:
  movement  - Ruby is a transmissive corundum (ior 1.77, red absorption); the chronograph works
              carry colours only, so each takes the finish of the catalogue material that has
              its colour elsewhere in the same movement.
  case      - Sapphire is transmissive (ior 1.77), not alpha; the glass crystal opaque-alpha too.
  motorbike - finishes by part material (paint with clear coat, chrome, rubber, cast aluminium,
              lenses as glass, a lit headlight), keeping every authored colour.

usage: python upgrade_glb.py MODEL IN.glb OUT.glb
"""
import json
import struct
import sys

LOOKS_BY_INDEX = {
    "motorbike": {
        0: dict(roughness=0.32, coat=1.0, coat_roughness=0.04),                 # frame: gloss black paint
        1: dict(roughness=0.38, coat=1.0, coat_roughness=0.03),                 # cream paint
        2: dict(roughness=0.38, coat=1.0, coat_roughness=0.03),                 # cream paint (panels)
        3: dict(metallic=1.0, roughness=0.06, color=[0.86, 0.87, 0.89, 1]),     # chrome
        4: dict(roughness=0.78),                                                # rubber
        5: dict(metallic=1.0, roughness=0.28),                                  # machined steel
        6: dict(metallic=1.0, roughness=0.3, coat=0.6, coat_roughness=0.05),    # lacquered cast rim
        7: dict(roughness=0.02, transmission=1.0, ior=1.5, color=[0.97, 0.98, 1.0, 1],
                emissive=[1.0, 0.92, 0.78], emissive_strength=6.0),             # lit headlight lens
        8: dict(roughness=0.12, transmission=0.85, ior=1.49, transmission_color=[1.0, 0.45, 0.05],
                transmission_depth=0.004),                                      # amber lens
        9: dict(metallic=1.0, roughness=0.45),                                  # cast aluminium
        10: dict(metallic=1.0, roughness=0.5),                                  # dark cast covers
        11: dict(roughness=0.4),                                                # black satin
        12: dict(roughness=0.25, coat=0.7, coat_roughness=0.05),                # yellow powder coat
        13: dict(roughness=0.55),                                               # leather
        14: dict(roughness=0.12, transmission=0.85, ior=1.49, transmission_color=[0.9, 0.04, 0.04],
                 transmission_depth=0.004),                                     # red lens
        15: dict(metallic=1.0, roughness=0.0, color=[0.9, 0.91, 0.93, 1]),      # mirror
    },
}

LOOKS_BY_NAME = {
    "movement": {"Ruby": dict(roughness=0.02, transmission=1.0, ior=1.77, metallic=0.0,
                              transmission_color=[0.9, 0.05, 0.22], transmission_depth=0.0006)},
    "case": {"Sapphire": dict(roughness=0.01, transmission=1.0, ior=1.77, metallic=0.0, color=[0.98, 0.99, 1.0, 1]),
             "Glass, soda-lime": dict(roughness=0.01)},
}


def to_gltf(m: dict, look: dict) -> list[str]:
    """Apply a Material-field look to a glTF material in place; returns the extensions used."""
    pbr = m.setdefault("pbrMetallicRoughness", {})
    ext = m.setdefault("extensions", {})
    used = []
    if "color" in look:
        pbr["baseColorFactor"] = look["color"]
    for k, g in (("metallic", "metallicFactor"), ("roughness", "roughnessFactor")):
        if k in look:
            pbr[g] = look[k]
    if look.get("transmission"):
        ext["KHR_materials_transmission"] = {"transmissionFactor": look["transmission"]}
        # transmission replaces alpha: a transmissive part is opaque in alpha
        c = pbr.get("baseColorFactor", [1, 1, 1, 1])
        pbr["baseColorFactor"] = [c[0], c[1], c[2], 1.0]
        m["alphaMode"] = "OPAQUE"
        m.pop("alphaCutoff", None)
        if "transmission_color" in look:
            ext["KHR_materials_volume"] = {"thicknessFactor": look.get("transmission_depth", 0.001) * 2,
                                           "attenuationColor": look["transmission_color"],
                                           "attenuationDistance": look.get("transmission_depth", 0.001)}
    if "ior" in look:
        ext["KHR_materials_ior"] = {"ior": look["ior"]}
    if look.get("coat"):
        ext["KHR_materials_clearcoat"] = {"clearcoatFactor": look["coat"],
                                          "clearcoatRoughnessFactor": look.get("coat_roughness", 0.0)}
    if "anisotropy" in look:
        ext["KHR_materials_anisotropy"] = {"anisotropyStrength": look["anisotropy"],
                                           "anisotropyRotation": look.get("anisotropy_rotation", 0.0)}
    if "emissive" in look:
        m["emissiveFactor"] = look["emissive"]
        if look.get("emissive_strength", 1.0) != 1.0:
            ext["KHR_materials_emissive_strength"] = {"emissiveStrength": look["emissive_strength"]}
    if not ext:
        m.pop("extensions")
    return list(ext)


def look_of(m):
    pbr = m.get("pbrMetallicRoughness", {})
    return dict(color=pbr.get("baseColorFactor", [1, 1, 1, 1]), metallic=pbr.get("metallicFactor", 1.0),
                roughness=pbr.get("roughnessFactor", 1.0), ext=m.get("extensions", {}))


def upgrade(model: str, src: str, dst: str) -> None:
    data = open(src, "rb").read()
    jlen = struct.unpack_from("<I", data, 12)[0]
    g = json.loads(data[20:20 + jlen])
    rest = data[20 + jlen:]
    mats = g.get("materials", [])
    used = set(g.get("extensionsUsed", []))
    changed = []
    # Render normalisation, always (2026-10-21 corpus): (1) transmission wins over alpha -- the
    # export also fades a transmissive part by alpha for the viewer, which a path tracer would
    # apply twice (a 5 %-alpha sapphire is 95 % absent); (2) KHR_materials_volume thickness is
    # written in millimetres in a metre file (a 4.3 mm crystal reads 4.3 m): scaled to metres.
    for i, m in enumerate(mats):
        ext = m.get("extensions", {})
        if ext.get("KHR_materials_transmission", {}).get("transmissionFactor", 0) > 0:
            pbr = m.setdefault("pbrMetallicRoughness", {})
            c = pbr.get("baseColorFactor", [1, 1, 1, 1])
            if m.get("alphaMode", "OPAQUE") != "OPAQUE" or c[3] < 1:
                pbr["baseColorFactor"] = [c[0], c[1], c[2], 1.0]
                m["alphaMode"] = "OPAQUE"
                changed.append((i, m.get("name", "") + " alpha->opaque"))
        vol = ext.get("KHR_materials_volume")
        if vol and vol.get("thicknessFactor", 0) > 0:
            vol["thicknessFactor"] = vol["thicknessFactor"] / 1000.0
            changed.append((i, m.get("name", "") + " thickness mm->m"))
    by_index = LOOKS_BY_INDEX.get(model, {})
    by_name = LOOKS_BY_NAME.get(model, {})
    if model == "movement":
        # colour-only materials (named matN) take the finish of a named material of their colour
        named = [m for m in mats if m.get("name") and not m["name"].startswith("mat")]
        for i, m in enumerate(mats):
            if not m.get("name", "mat").startswith("mat"):
                continue
            c = look_of(m)["color"]
            twin = next((n for n in named if max(abs(a - b) for a, b in zip(look_of(n)["color"], c)) < 0.006), None)
            if twin is not None:
                tl = look_of(twin)
                m["pbrMetallicRoughness"] = {"baseColorFactor": c, "metallicFactor": tl["metallic"],
                                             "roughnessFactor": tl["roughness"]}
                if tl["ext"]:
                    m["extensions"] = json.loads(json.dumps(tl["ext"]))
                    used |= set(tl["ext"])
                m["name"] = f"{twin['name']} (by colour)"
                changed.append((i, m["name"]))
    for i, m in enumerate(mats):
        base = m.get("name", "").replace(" (by colour)", "")
        look = by_index.get(i) or by_name.get(base)
        if look:
            used |= set(to_gltf(m, look))
            changed.append((i, base))
    g["extensionsUsed"] = sorted(used)
    js = json.dumps(g, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    out = struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(js) + len(rest)) + struct.pack("<II", len(js), 0x4E4F534A) + js + rest
    open(dst, "wb").write(out)
    print(f"{model}: {len(changed)} material changes -> {dst}\n  extensionsUsed {g['extensionsUsed']}")


if __name__ == "__main__":
    upgrade(*sys.argv[1:4])
