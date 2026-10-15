# Parts: footprint libraries, `fp-lib-table`, and reading a footprint

A part on the board is a footprint from a library: copper pads with numbers, a courtyard,
silkscreen, a 3D model's name. There is no symbol and no schematic; the script says which
net each pad number is on. So two things matter: that the library is declared, and that you
know the footprint's pads before you wire them.

## `fp-lib-table`

KiCad's project library table, a file named exactly `fp-lib-table`, beside the script. A
build starts KiCad with an empty settings directory, so this table is the only one read:
every library the board places from must be a row of it, stock libraries included.

```text
(fp_lib_table
  (version 7)
  (lib (name "Resistor_SMD") (type "KiCad") (uri "${KICAD10_FOOTPRINT_DIR}/Resistor_SMD.pretty") (options "") (descr ""))
  (lib (name "Capacitor_SMD") (type "KiCad") (uri "${KICAD10_FOOTPRINT_DIR}/Capacitor_SMD.pretty") (options "") (descr ""))
  (lib (name "Package_TO_SOT_SMD") (type "KiCad") (uri "${KICAD10_FOOTPRINT_DIR}/Package_TO_SOT_SMD.pretty") (options "") (descr ""))
  (lib (name "MountingHole") (type "KiCad") (uri "${KICAD10_FOOTPRINT_DIR}/MountingHole.pretty") (options "") (descr ""))
  (lib (name "my") (type "KiCad") (uri "${KIPRJMOD}/my.pretty") (options "") (descr "this board's own footprints"))
)
```

- `name` is the nickname the script uses before the colon: `"Resistor_SMD:R_0603_1608Metric"`.
  Keep it equal to the library's folder name, as KiCad's own table does.
- `${KICAD10_FOOTPRINT_DIR}` is KiCad's stock footprint folder, resolved by KiCad itself even
  with an empty settings directory. The name is the nightly's today; KiCad renames it per
  major release, so it is written here and in the skill and nowhere else.
- `${KIPRJMOD}` is the folder the table is in: the board's own libraries live beside it.
- A row whose folder does not exist loads with status `LLS_ERROR` and the build reports the
  nickname; a footprint whose nickname has no row fails with KiCad's
  `footprint 'Nickname:Name' not found`.
- Each loaded library costs about 0.3 s and 60 MB per build: list the libraries the board
  uses, not the whole stock set.

The table and any `.pretty` folders are the script's companions, written by you, never by a
build.

## Stock libraries

The nightly's footprint package puts one `<Library>.pretty` folder per library, with one
`<Name>.kicad_mod` file per footprint, in:

- Ubuntu: `/usr/share/kicad-nightly/footprints/`
- macOS: `/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints/`
- Windows: `C:\Program Files\KiCad\<version>\share\kicad\footprints\`

Listing the folder is the fastest way to find names: `ls .../footprints | grep -i sot`,
`ls .../footprints/Package_TO_SOT_SMD.pretty`. The libraries most boards use:
`Resistor_SMD`, `Capacitor_SMD`, `Inductor_SMD`, `LED_SMD`, `Diode_SMD`, `Package_TO_SOT_SMD`,
`Package_SO`, `Package_QFP`, `Package_DFN_QFN`, `Package_BGA`, `Crystal`, `Button_Switch_SMD`,
`Connector_PinHeader_2.54mm`, `Connector_JST`, `Connector_USB`, `TestPoint`, `MountingHole`,
`Fiducial`. Never guess a footprint name: find it in the folder or by browsing the library
(below), then copy it exactly.

## Reading a footprint's pads

Before wiring a part, read its pads: their numbers (which the nets go on), positions
(which decide where tracks leave) and types. The same server a build uses answers, from a
script beside the board's `fp-lib-table`
([board API: browsing libraries](board-api.md#browsing-libraries)):

```python
footprint = kicad.get_library_footprint("Package_TO_SOT_SMD:SOT-23-5")
for pad in footprint.pads:
    print(pad.number, round(to_mm(pad.position.x), 3), round(to_mm(pad.position.y), 3), pad.pad_type)
```

Positions are relative to the footprint's origin, before placement; once placed with
`place_footprint_from_library`, `fp.definition.pads` carries absolute positions. Map pad
numbers to the datasheet's pin functions yourself and keep that mapping in the script, as a
dict next to the part, so the netlist is read in one place:

```python
U1 = {"1": "EN", "2": "GND", "3": "VIN", "4": "NC", "5": "VOUT"}   # SOT-23-5 regulator, pin numbers from the datasheet
```

A footprint whose pad numbering differs from the datasheet's pin numbering (common for
connectors and multi-pad parts: a shield's several pads share one number) is where boards go
wrong: check the footprint's own description (`fp.definition` carries it in the file; the
`.kicad_mod` text has `descr`) against the part.

## The board's own footprints

For a part KiCad's libraries lack, keep a library beside the script, `my.pretty/` with one
`<Name>.kicad_mod` per footprint, and declare it with a `${KIPRJMOD}` row. The file is
KiCad's footprint format: draw it in KiCad's footprint editor and save it into that folder,
take the manufacturer's KiCad footprint, or convert a vendor's (parts from LCSC convert with
`easyeda2kicad --full --lcsc_id C2837938`; step.parts listings often come with KiCad files).
Check every imported footprint's pad numbers and sizes against the datasheet's land pattern
before you trust it, and read its pads the same way as a stock one.

## Values and fields

A footprint carries its reference and value (`fp.reference_field.text.value`,
`fp.value_field.text.value`) and its attributes: `fp.attributes.do_not_populate`,
`exclude_from_bill_of_materials`, `exclude_from_position_files`. There is no BOM export from
a board alone (a BOM is a schematic's), so keep the part list in the script as data, with
each part's value, footprint and part number (an `LCSC` number for JLCPCB, an `MPN` and
manufacturer for other assemblers), and write the BOM from it when the fab needs one
([manufacturing](manufacturing.md)). Choose parts the fab stocks: for JLCPCB its "basic"
parts carry no setup fee.
