"""Materials for the Moonwatch model entries.

The original authored named PBR "finishes" (`materials=` on the decorator,
assigned by leaf label). Here each finish is a `cadgen.engine.Material` set on
the part itself: the catalogue's name and density where a catalogue material is
the metal or glass in question, with the finish's own metallic/roughness and its
old name as the description; otherwise a `Material` of its own. Every finish
keeps `color=None`, so a part stays the colour its builder authored (the STEP
document remained the source of base colour then too). The old clearcoat
factors have no field in `Material` and are dropped.

Assignments use unique authored leaf labels, so factory edits cannot silently
retarget a finish through occurrence order.
"""

from __future__ import annotations

from dataclasses import replace

from cadgen.engine import Material
from cadgen.engine import materials as catalogue


def _finish(base: Material, description: str, metallic: float, roughness: float) -> Material:
    """A catalogue material in one finish: its name and density, the finish's look."""
    return replace(base, color=None, metallic=metallic, roughness=roughness, description=description)


FINISHES = {
    # old finish name: material (catalogue entry or own)
    "anglage_mirror": _finish(catalogue.brass, "Anglage Mirror", 0.9, 0.14),
    "bezel_anodized": _finish(catalogue.aluminium, "Bezel Anodized", 0.35, 0.5),
    "blued_screw": _finish(catalogue.steel, "Blued Screw", 0.9, 0.14),
    "brass_part": _finish(catalogue.brass, "Brass Part", 0.9, 0.38),
    "brushed_steel": _finish(catalogue.stainless_steel, "Brushed Steel", 0.9, 0.5),
    "case_mirror_polish": _finish(catalogue.stainless_steel, "Case Mirror Polish", 0.95, 0.08),
    "copper_plate": _finish(catalogue.brass, "Copper Plate", 0.9, 0.34),  # copper-plated brass bridges
    "gilt_wheel": _finish(catalogue.brass, "Gilt Wheel", 0.95, 0.24),  # gilded brass wheels
    "glass": _finish(catalogue.glass, "Glass", 0.0, 0.05),
    "glass_back": Material("Sapphire", 3980.0, metallic=0.0, roughness=0.03, description="Glass Back"),
    "grained_lever": _finish(catalogue.steel, "Grained Lever", 0.95, 0.34),
    "matte_print": Material("Matte Print", metallic=0.0, roughness=0.85, description="printed ink, no density"),
    "polished_steel": _finish(catalogue.stainless_steel, "Polished Steel", 0.92, 0.12),
    "rubber": Material("NBR", 1000.0, metallic=0.0, roughness=0.9, description="Rubber (nitrile gaskets)"),
    "ruby_jewel": Material("Ruby", 3980.0, metallic=0.0, roughness=0.05, description="Ruby Jewel (synthetic corundum)"),
    "satin_steel": _finish(catalogue.stainless_steel, "Satin Steel", 0.95, 0.3),
}

MODEL_MATERIAL_ASSIGNMENTS = {
    "case": [
        {"targets": ["#bezel_insert"], "material": "bezel_anodized"},
        {"targets": ["#case_middle", "#caseback"], "material": "brushed_steel"},
        {"targets": ["#case_polish:lug_ne", "#case_polish:lug_nw", "#case_polish:lug_se",
                     "#case_polish:lug_sw", "#case_polish:bezel", "#case_polish:band_lower"],
         "material": "case_mirror_polish"},
        {"targets": ["#crystal"], "material": "glass"},
        {"targets": ["#caseback_sapphire"], "material": "glass_back"},
        {"targets": ["#tachymeter_scale"], "material": "matte_print"},
        {"targets": ["#crown", "#crown_tube", "#pusher_cap:2oclock", "#pusher_tube:2oclock",
                     "#pusher_cap:4oclock", "#pusher_tube:4oclock", "#bezel_ring"],
         "material": "polished_steel"},
        {"targets": ["#caseback_o_ring", "#crown_o_ring", "#pusher_o_ring:2oclock",
                     "#pusher_o_ring:4oclock", "#crystal_gasket"],
         "material": "rubber"},
        {"targets": ["#pusher_spring:2oclock", "#pusher_spring:4oclock", "#spring_bar:12",
                     "#spring_bar:6", "#caseback_retaining_ring"],
         "material": "satin_steel"},
    ],
    "keyless_works": [
        {"targets": ["#screw:setting_lever_spring:1", "#screw:setting_lever_spring:2"],
         "material": "blued_screw"},
        {"targets": ["#minute_wheel", "#hour_wheel"], "material": "gilt_wheel"},
        {"targets": ["#setting_lever", "#yoke", "#setting_lever_spring"], "material": "grained_lever"},
        {"targets": ["#stem", "#minute_wheel_pinion", "#winding_pinion", "#sliding_pinion",
                     "#cannon_pinion"],
         "material": "polished_steel"},
    ],
    "movement_base": [
        {"targets": ["#barrel_bridge:anglage:0", "#barrel_bridge:anglage:1",
                     "#barrel_bridge:anglage:2", "#barrel_bridge:anglage:3",
                     "#barrel_bridge:anglage:4", "#train_bridge:anglage:0",
                     "#train_bridge:anglage:1", "#pallet_bridge:anglage"],
         "material": "anglage_mirror"},
        {"targets": ["#screw:stud_holder", "#screw:barrel_bridge:left", "#screw:barrel_bridge:right",
                     "#screw:barrel_bridge:top", "#screw:train_bridge:foot",
                     "#screw:train_bridge:fourth", "#screw:train_bridge:center", "#screw:ratchet",
                     "#screw:crown_wheel", "#screw:click", "#screw:click_spring",
                     "#screw:pallet_bridge", "#screw:balance_cock"],
         "material": "blued_screw"},
        {"targets": ["#barrel_drum", "#shock_chaton", "#barrel_lid", "#chaton:center",
                     "#chaton:third", "#chaton:fourth", "#chaton:escape", "#chaton:pallet"],
         "material": "brass_part"},
        {"targets": ["#main_plate", "#barrel_bridge", "#barrel_bridge:stripe_shadow:0",
                     "#barrel_bridge:stripe_shadow:1", "#barrel_bridge:stripe_shadow:2",
                     "#barrel_bridge:stripe_shadow:3", "#barrel_bridge:stripe_shadow:4",
                     "#barrel_bridge:stripe_shadow:5", "#train_bridge",
                     "#train_bridge:stripe_shadow:0", "#train_bridge:stripe_shadow:1",
                     "#train_bridge:stripe_shadow:2", "#pallet_bridge",
                     "#pallet_bridge:stripe_shadow:0", "#pallet_bridge:stripe_shadow:1",
                     "#balance_cock", "#balance_cock:stripe_shadow:0",
                     "#balance_cock:stripe_shadow:1"],
         "material": "copper_plate"},
        {"targets": ["#center_wheel", "#third_wheel", "#fourth_wheel", "#ratchet_wheel",
                     "#escape_wheel", "#balance_wheel", *[f"#timing_screw:{i}" for i in range(16)]],
         "material": "gilt_wheel"},
        {"targets": ["#click", "#click_spring", "#pallet_fork"], "material": "grained_lever"},
        {"targets": ["#stud_holder", "#shock_bezel", "#shock_lyre_spring", "#regulator",
                     "#barrel_arbor", "#center_pinion", "#third_pinion", "#fourth_pinion",
                     "#escape_pinion", "#crown_wheel", "#crown_wheel_core", "#pallet_arbor",
                     "#balance_staff"],
         "material": "polished_steel"},
        {"targets": ["#shock_cap_jewel", "#plate_jewel:center", "#plate_jewel:third",
                     "#bridge_jewel:barrel", "#plate_jewel:fourth", "#bridge_jewel:center",
                     "#bridge_jewel:third", "#bridge_jewel:fourth", "#bridge_jewel:escape",
                     "#plate_jewel:escape", "#plate_jewel:pallet", "#pallet_stone:entry",
                     "#pallet_stone:exit", "#plate_jewel:balance", "#bridge_jewel:pallet",
                     "#balance_lower_cap_jewel", "#impulse_jewel"],
         "material": "ruby_jewel"},
        {"targets": ["#mainspring", "#hairspring", "#hairspring_stud"], "material": "satin_steel"},
    ],
}


def _leaves(shape):
    children = list(getattr(shape, "children", ()) or ())
    if not children:
        yield shape
    for child in children:
        yield from _leaves(child)


def apply_materials(parts, model: str):
    """Set each assigned leaf's material (the old ``materials=`` pass) on the finished
    parts, before they are composed; returns the parts, and raises naming any label an
    assignment names that no leaf carries."""
    wanted = {target.lstrip("#"): FINISHES[rule["material"]]
              for rule in MODEL_MATERIAL_ASSIGNMENTS[model] for target in rule["targets"]}
    found = set()
    for leaf in (leaf for part in parts for leaf in _leaves(part)):
        material = wanted.get(leaf.label)
        if material is not None:
            leaf.material = material
            found.add(leaf.label)
    missing = sorted(set(wanted) - found)
    if missing:
        raise ValueError(f"{model}: no part is labelled {', '.join(missing)}")
    return parts
