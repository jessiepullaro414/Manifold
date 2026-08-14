"""
Generates a KiCad schematic (.kicad_sch) for "Manifold", an Arduino Nano
inspired, high-temp automotive board. Self-contained: all symbols are
embedded generic placeholders (rectangle body, correct pin count/names/
electrical types).

Connectivity model (v2 — drawn to read like a hand-made schematic):
  * REAL WIRES for the main signal flow: 12V power chain, MCU<->J1 signal bus
    (pins aligned row-for-row so every bus wire is a straight line), crystal.
  * POWER SYMBOLS (+5V / +3V3 / GND / VIN) everywhere a power net is touched.
    Power nets NEVER use local labels (mixing local labels with power-symbol
    nets splits them into separate nets in KiCad).
  * LOCAL LABELS only for genuine cross-sheet-style references: SWD/BOOT0 taps,
    UART sharing D0-D1, feedback divider, VDDA/AREF/OSC taps.

Geometry: KiCad symbol space is Y-UP; the schematic sheet is Y-DOWN. A pin
defined at (px, py) in a symbol placed at (x, y) rot 0 lands at (x+px, y-py).
Pin 'at' is the electrical connection point; pin angle points TOWARD the body.
"""
import os
import uuid as uuid_lib

from kiutils.schematic import Schematic
from kiutils.symbol import Symbol, SymbolPin
from kiutils.items.common import (Position, Property, Effects, Font, Stroke,
                                  PageSettings, TitleBlock, Justify)
from kiutils.items.syitems import SyRect, SyPolyLine
from kiutils.items.schitems import (SchematicSymbol, Connection, LocalLabel,
                                    SymbolInstance, Text as SchText, NoConnect)


def U():
    return str(uuid_lib.uuid4())


PITCH = 2.54
LEAD = 2.54
POWER_NETS = {"+5V", "+3V3", "GND", "VIN"}

# KiCad's default schematic grid (50 mil). Every pin offset baked into a
# symbol via layout() below is already an exact multiple of this (PITCH=2.54
# is 2x it, and layout()'s own half-pitch centering math only ever produces
# other multiples of it) - confirmed by inspection, not assumed. The one
# thing NOT guaranteed to land on this grid is the raw (x, y) passed to
# place() at each call site, which are just "looks right on the page"
# numbers (60, 100, 85, RAIL=50.0, etc.), none of which are multiples of
# 1.27. snap() is applied once, at place()'s entry point below - since every
# pin/wire/label/power-symbol position in the file is computed FROM that
# snapped (x, y) plus a grid-clean symbol offset, snapping there alone is
# enough to put the whole sheet on-grid, without having to touch every call
# site. Found via `kicad-cli sch erc`'s endpoint_off_grid check (259 hits
# before this fix): real KiCad schematics rely on exact-grid pin/wire
# endpoints, not just "looks connected in the editor at the default zoom".
GRID = 1.27


def snap(v):
    return round(round(v / GRID) * GRID, 2)

# pin angle points from connection tip toward body (KiCad convention)
SIDE_ANGLE = {'L': 0, 'R': 180, 'T': 270, 'B': 90}
# outward direction of a stub on the SHEET (Y-down) for each side
STUB_DIR = {'L': (-1, 0), 'R': (1, 0), 'T': (0, -1), 'B': (0, 1)}
# label rotation so text reads away from the symbol
LABEL_ANGLE = {'L': 180, 'R': 0, 'T': 90, 'B': 270}


# ---------------------------------------------------------------------------
# Symbol factory
# ---------------------------------------------------------------------------
def layout(sides):
    """sides: {'L'/'R'/'T'/'B': [(number, name, etype), ...]} in top-to-bottom
    (L/R) or left-to-right (T/B) SHEET order. Returns (w, h, pins, side_map)."""
    nL, nR = len(sides.get('L', [])), len(sides.get('R', []))
    nT, nB = len(sides.get('T', [])), len(sides.get('B', []))
    height = max(max(nL, nR, 1) * PITCH + PITCH, PITCH * 2)
    width = max(max(nT, nB, 1) * PITCH + PITCH, PITCH * 2)
    pins, side_map = [], {}

    def add_vertical(entries, x_tip, angle, side):
        n = len(entries)
        for i, (num, name, etype) in enumerate(entries):
            # symbol space is Y-up: first entry gets largest py -> top on sheet
            py = ((n - 1) * PITCH) / 2 - i * PITCH
            pins.append(SymbolPin(electricalType=etype, graphicalStyle="line",
                                  position=Position(round(x_tip, 2), round(py, 2), angle),
                                  length=LEAD, name=name, number=str(num)))
            side_map[str(num)] = side

    def add_horizontal(entries, y_tip, angle, side):
        n = len(entries)
        for i, (num, name, etype) in enumerate(entries):
            px = -((n - 1) * PITCH) / 2 + i * PITCH
            pins.append(SymbolPin(electricalType=etype, graphicalStyle="line",
                                  position=Position(round(px, 2), round(y_tip, 2), angle),
                                  length=LEAD, name=name, number=str(num)))
            side_map[str(num)] = side

    if 'L' in sides:
        add_vertical(sides['L'], -(width / 2 + LEAD), SIDE_ANGLE['L'], 'L')
    if 'R' in sides:
        add_vertical(sides['R'], width / 2 + LEAD, SIDE_ANGLE['R'], 'R')
    if 'T' in sides:
        add_horizontal(sides['T'], height / 2 + LEAD, SIDE_ANGLE['T'], 'T')
    if 'B' in sides:
        add_horizontal(sides['B'], -(height / 2 + LEAD), SIDE_ANGLE['B'], 'B')
    return width, height, pins, side_map


lib_symbols = {}   # lib_id -> (Symbol, side_map, width, height)


def register_symbol(lib_id, ref_prefix, value, footprint, sides,
                    datasheet="~", hide_pin_names=False):
    w, h, pins, side_map = layout(sides)
    sym = Symbol.create_new(id=lib_id, reference=ref_prefix, value=value,
                            footprint=footprint, datasheet=datasheet)
    sym.pinNames = True
    sym.pinNamesOffset = 0.508
    sym.pinNamesHide = hide_pin_names
    sym.hidePinNumbers = False
    sym.properties[0].position = Position(0, h / 2 + 1.8, 0)    # Ref above (sym Y-up)
    sym.properties[1].position = Position(0, -(h / 2 + 1.8), 0)  # Value below
    sym.graphicItems.append(SyRect(
        start=Position(-w / 2, -h / 2), end=Position(w / 2, h / 2),
        stroke=Stroke(width=0.254, type="default")))
    sym.graphicItems[-1].fill.type = "background"
    sym.pins = pins
    lib_symbols[lib_id] = (sym, side_map, w, h)


def register_power_symbol(net, is_gnd):
    lib_id = f"{LIB}:PWR_{net}"
    sym = Symbol.create_new(id=lib_id, reference="#PWR", value=net)
    sym.isPower = True
    sym.pinNames = True
    sym.pinNamesOffset = 0
    sym.pinNamesHide = True
    sym.hidePinNumbers = True
    sym.properties[0].effects.hide = True                      # hide "#PWR" ref
    stroke = Stroke(width=0.254, type="default")
    if is_gnd:
        sym.properties[1].position = Position(0, -4.6, 0)      # value below
        for pts in ([(0, 0), (0, -1.27)],
                    [(-1.27, -1.27), (1.27, -1.27)],
                    [(-0.762, -1.905), (0.762, -1.905)],
                    [(-0.254, -2.54), (0.254, -2.54)]):
            sym.graphicItems.append(SyPolyLine(
                points=[Position(a, b) for a, b in pts], stroke=stroke))
    else:
        sym.properties[1].position = Position(0, 3.9, 0)       # value above bar
        for pts in ([(0, 0), (0, 2.54)],
                    [(-1.016, 2.54), (1.016, 2.54)]):
            sym.graphicItems.append(SyPolyLine(
                points=[Position(a, b) for a, b in pts], stroke=stroke))
    sym.pins = [SymbolPin(electricalType="power_in", graphicalStyle="line",
                          position=Position(0, 0, 90), length=0,
                          name=net, number="1", hide=True)]
    lib_symbols[lib_id] = (sym, {"1": 'T'}, 0, 0)


def register_pwr_flag(net):
    """Real KiCad PWR_FLAG equivalent: a power_out pin merged onto `net` via
    the same same-name-global-net mechanism every other power symbol here
    already relies on. VIN and GND both enter this board from OFF-SHEET
    (the battery, via J1) with nothing on this sheet electrically "driving"
    them - ERC's power_pin_not_driven correctly flags that (2 hits) unless
    something asserts "this net IS driven, just not visibly on this sheet",
    which is exactly what a real PWR_FLAG is for. Drawn as a simple upward
    arrow (distinct from the T-bar/GND-fork power symbols) purely so it
    reads as a flag, not another supply source, if anyone opens this in the
    KiCad GUI."""
    lib_id = f"{LIB}:PWR_FLAG_{net}"
    sym = Symbol.create_new(id=lib_id, reference="#FLG", value=net)
    sym.isPower = True
    sym.pinNames = True
    sym.pinNamesOffset = 0
    sym.pinNamesHide = True
    sym.hidePinNumbers = True
    sym.properties[0].effects.hide = True
    sym.properties[1].position = Position(0, 3.9, 0)
    stroke = Stroke(width=0.254, type="default")
    for pts in ([(0, 0), (0, 2.54)], [(0, 2.54), (-0.889, 1.651)],
                [(0, 2.54), (0.889, 1.651)]):
        sym.graphicItems.append(SyPolyLine(
            points=[Position(a, b) for a, b in pts], stroke=stroke))
    sym.pins = [SymbolPin(electricalType="power_out", graphicalStyle="line",
                          position=Position(0, 0, 90), length=0,
                          name=net, number="1", hide=True)]
    lib_symbols[lib_id] = (sym, {"1": 'T'}, 0, 0)


# ---------------------------------------------------------------------------
# Schematic scaffolding
# ---------------------------------------------------------------------------
sch = Schematic.create_new()
sch.paper = PageSettings(paperSize="A2")
# A3 (420x297mm) is what this was originally - grown to A2 (594x420mm)
# because the 36-line NOTES block (see NOTE_LINES far below) genuinely
# doesn't fit in A3's remaining vertical room below the circuitry: at
# NOTES_START_Y=250 and 4mm/line spacing, 36 lines reaches y=390, a full
# 93mm past A3's 297mm height - confirmed by rendering and finding the
# text physically clipped at the page edge, not just eyeballed. Nothing
# else on the sheet needed to move: every existing section already sits
# well inside A2's larger canvas too (rightmost real content ~x=395 of
# A2's 594 width), this purely buys the NOTES block room to breathe.
# Fixed (not regenerated each run) so it always matches the "sheets" entry
# cached in Manifold.kicad_pro - a fresh random UUID here would silently
# desync the two files on every regeneration.
sch.uuid = "84af7bce-25f1-4c6f-a502-1f3cdbdde83d"
sch.titleBlock = TitleBlock(
    title="Manifold - Arduino Nano inspired, high-temp automotive board",
    date="2026-07-10", revision="B",
    company="Generated design spec - verify before fab",
    comments={1: "Wired power/signal flow; AEC-Q100 NXP S32K1 core, SWD (no Arduino IDE)"})

wires, labels, texts, no_connects = [], [], [], []
pin_pos = {}        # (ref, pin_number_str) -> (x, y) on sheet
pwr_count = 0


def add_wire(x1, y1, x2, y2):
    wires.append(Connection(type="wire",
                            points=[Position(round(x1, 2), round(y1, 2)),
                                    Position(round(x2, 2), round(y2, 2))],
                            stroke=Stroke(width=0.0, type="default"), uuid=U()))


def add_label(text, x, y, angle):
    assert text not in POWER_NETS, f"power net {text} must use a power symbol, not a label"
    labels.append(LocalLabel(text=text, position=Position(round(x, 2), round(y, 2), angle),
                             effects=Effects(font=Font(width=1.27, height=1.27)),
                             uuid=U()))


def _instance(lib_id, ref, value, x, y, ref_hidden=False):
    sym, _, _, h = lib_symbols[lib_id]
    inst = SchematicSymbol()
    inst.libId = lib_id
    inst.position = Position(x, y, 0)
    inst.unit = 1
    inst.inBom = not ref.startswith("#")
    inst.onBoard = True
    inst.uuid = U()
    fp = next((p.value for p in sym.properties if p.key == "Footprint"), "")
    ref_eff = Effects(font=Font(width=1.27, height=1.27), hide=ref_hidden)
    val_y = y - sym.properties[1].position.Y   # sheet Y-down flip of value pos
    ref_y = y - sym.properties[0].position.Y
    inst.properties = [
        Property(key="Reference", value=ref, id=0, position=Position(x, ref_y, 0), effects=ref_eff),
        Property(key="Value", value=value, id=1, position=Position(x, val_y, 0),
                 effects=Effects(font=Font(width=1.27, height=1.27))),
        Property(key="Footprint", value=fp, id=2, position=Position(x, y, 0),
                 effects=Effects(font=Font(width=1.27, height=1.27), hide=True)),
    ]
    for pin in sym.pins:
        inst.pins[pin.number] = U()
    sch.schematicSymbols.append(inst)
    sch.symbolInstances.append(SymbolInstance(
        path=f"/{inst.uuid}", reference=ref, unit=1, value=value, footprint=fp))
    return inst


def place_power(net, x, y):
    """Power symbol whose connection point is exactly (x, y)."""
    global pwr_count
    pwr_count += 1
    _instance(f"{LIB}:PWR_{net}", f"#PWR{pwr_count:03d}", net, x, y, ref_hidden=True)


flg_count = 0


def place_pwr_flag(net, x, y):
    """PWR_FLAG instance - joins `net`'s global net by name, same mechanism
    as place_power, doesn't need to sit at any particular existing wire/pin
    coordinate."""
    global flg_count
    flg_count += 1
    _instance(f"{LIB}:PWR_FLAG_{net}", f"#FLG{flg_count:03d}", net, x, y, ref_hidden=True)


def place(lib_id, ref, value, x, y, conn=None):
    """Place a part. conn maps pin number -> one of:
         ('wire',)                   no stub; a wire will be drawn to the pin later
         ('label', NAME[, stub_len]) stub outward + local label
         ('pwr', NET[, stub_len])    stub (+riser if horizontal) + power symbol
         ('nc',)                     no-connect flag directly on the pin - for a
                                     pin whose real electrical type ISN'T
                                     no_connect (e.g. a genuine, driveable
                                     output like U2's PG) but is deliberately
                                     left unused in this design; a no-connect
                                     flag documents that intent to ERC
                                     correctly, unlike a same-named ('label',
                                     'NC_...') stub, which ERC reads as a
                                     dangling/orphaned label since nothing
                                     else in the design should ever share
                                     that name
       default: ('label', <pin name>, LEAD)"""
    x, y = snap(x), snap(y)
    conn = {str(k): v for k, v in (conn or {}).items()}
    sym, side_map, w, h = lib_symbols[lib_id]
    _instance(lib_id, ref, value, x, y)

    for pin in sym.pins:
        num = pin.number
        px = round(x + pin.position.X, 2)
        py = round(y - pin.position.Y, 2)   # Y-flip: symbol Y-up -> sheet Y-down
        pin_pos[(ref, num)] = (px, py)
        # A pin whose own electrical type is "no_connect" (e.g. U1's spare
        # MCU GPIOs) needs a real no-connect flag, not a stub+label - the
        # earlier stub+label default gave each one a same-named
        # "RESERVED_N" label that's unique in the whole design by
        # definition (nothing else should ever connect to a spare pin),
        # which ERC correctly reads as a dangling/orphaned label (25 hits).
        # A no-connect marker is the actual, correct way to say "yes, this
        # is deliberately unconnected" - unless a conn override explicitly
        # asks to wire this specific pin anyway, honor that instead.
        if pin.electricalType == "no_connect" and num not in conn:
            no_connects.append(NoConnect(position=Position(px, py), uuid=U()))
            continue
        mode = conn.get(num, ('label', pin.name, LEAD))
        kind = mode[0]
        if kind == 'wire':
            continue
        if kind == 'nc':
            no_connects.append(NoConnect(position=Position(px, py), uuid=U()))
            continue
        side = side_map[num]
        dx, dy = STUB_DIR[side]
        stub = mode[2] if len(mode) > 2 else LEAD
        ex, ey = round(px + dx * stub, 2), round(py + dy * stub, 2)
        add_wire(px, py, ex, ey)
        if kind == 'label':
            name = mode[1]
            if name in ("~", ""):
                raise ValueError(f"{ref}.{num}: generic pin needs a net in conn")
            add_label(name, ex, ey, LABEL_ANGLE[side])
        elif kind == 'pwr':
            net = mode[1]
            if side in ('T', 'B'):
                place_power(net, ex, ey)
            else:
                rise = PITCH if net == "GND" else -PITCH   # sheet Y-down: up = -Y
                add_wire(ex, ey, ex, ey + rise)
                place_power(net, ex, ey + rise)


def off(lib_id, num):
    """Sheet-space offset of a pin from its symbol origin."""
    sym, _, _, _ = lib_symbols[lib_id]
    for p in sym.pins:
        if p.number == str(num):
            return p.position.X, -p.position.Y
    raise KeyError(num)


def wire_pins(refA, pinA, refB, pinB, label=None, label_x=None):
    (x1, y1), (x2, y2) = pin_pos[(refA, str(pinA))], pin_pos[(refB, str(pinB))]
    assert abs(y1 - y2) < 0.01 or abs(x1 - x2) < 0.01, \
        f"{refA}.{pinA} -> {refB}.{pinB} not aligned: ({x1},{y1}) vs ({x2},{y2})"
    add_wire(x1, y1, x2, y2)
    if label:
        lx = label_x if label_x is not None else (x1 + x2) / 2
        add_label(label, lx, y1, 0)


def section_text(s, x, y):
    # Left-justified, not KiCad's bare-text default of CENTER: a long
    # section title (this file's longest is 48 chars) at font height 2.0
    # renders wide enough that a center anchor near the left edge of the
    # sheet pushes real text into negative X, clipped clean off the page
    # in any export - confirmed directly (kicad-cli sch export pdf, then
    # rendered to PNG - "POWER INPUT, REVERSE-BATTERY..." came out
    # truncated to "...PUT, REVERSE-BATTERY..."). Left-justify makes the
    # anchor the text's own start, independent of string length, so this
    # doesn't need per-title x-tuning and can't regress if a title changes.
    texts.append(SchText(text=s, position=Position(x, y, 0),
                         effects=Effects(font=Font(height=2.0, width=2.0,
                                                   thickness=0.35, bold=True),
                                          justify=Justify(horizontally="left"))))


# ---------------------------------------------------------------------------
# Symbol definitions
# ---------------------------------------------------------------------------
LIB = "Manifold"
def P(num, name, etype):
    return (num, name, etype)

for net in ("+5V", "+3V3", "VIN"):
    register_power_symbol(net, is_gnd=False)
register_power_symbol("GND", is_gnd=True)
register_pwr_flag("VIN")
register_pwr_flag("GND")

# Traditional automotive Mini blade fuse, not an SMD fuse (2026-07-19, user
# request: "easier for the user" to source/replace at any auto parts store).
# Two real parts: this footprint is the Keystone 3568 THT fuse HOLDER
# (bundled directly in KiCad's own Fuse.pretty, 3D model included - unlike
# the earlier NANO2 footprint, no missing-model workaround needed here) -
# the actual replaceable fuse element (Littelfuse 297 series MINI, 2A,
# SAE J2077/ISO 8820-3 compliant) is a separate physical part that plugs
# into this holder, not soldered down, so it isn't itself a PCB footprint.
# 2A, not 1A: traditional blade-style automotive fuses don't come in a 1A
# rating (2A is the lowest standard rating in this real fuse family) -
# confirmed fine against Q1's real 2.5A@100C headroom (see PMV37ENEA note).
register_symbol(f"{LIB}:Fuse", "F", "2A holder for Littelfuse 297-series MINI blade fuse",
                "Fuse:Fuseholder_Blade_Mini_Keystone_3568",
                {'L': [P(1, "~", "passive")], 'R': [P(2, "~", "passive")]},
                hide_pin_names=True)
register_symbol(f"{LIB}:TVS_V", "D", "SMCJ33A", "Diode_SMD:D_SMC",
                {'T': [P(1, "~", "passive")], 'B': [P(2, "~", "passive")]},
                hide_pin_names=True)
register_symbol(f"{LIB}:C_V", "C", "100nF", "Capacitor_SMD:C_0603_1608Metric",
                {'T': [P(1, "~", "passive")], 'B': [P(2, "~", "passive")]},
                hide_pin_names=True)
register_symbol(f"{LIB}:L_H", "L", "10uH", "Inductor_SMD:L_1210_3225Metric",
                {'L': [P(1, "~", "passive")], 'R': [P(2, "~", "passive")]},
                hide_pin_names=True)
register_symbol(f"{LIB}:R_V", "R", "10k", "Resistor_SMD:R_0603_1608Metric",
                {'T': [P(1, "~", "passive")], 'B': [P(2, "~", "passive")]},
                hide_pin_names=True)
# Real footprint has 4 pads, not 2: standard 3225 4-pad crystal packages wire
# pin1+pin3 as one crystal terminal and pin2+pin4 as the other (redundant
# pads for mechanical stability/lower ESR, not separate case-ground pins) -
# so pins 3/4 need to be defined and tied to the same nets as 1/2, or KiCad's
# "Update PCB from Schematic" reports them as having no matching schematic pin.
register_symbol(f"{LIB}:XTAL", "Y", "8MHz", "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm",
                {'R': [P(1, "OSC_IN", "passive"), P(2, "OSC_OUT", "passive")],
                 'L': [P(3, "OSC_IN", "passive"), P(4, "OSC_OUT", "passive")]},
                hide_pin_names=True)
# Real part, real pinout (Nexperia PMV37ENEA SOT-23/TO-236AB, verified
# against the actual datasheet's pinning table): pin1=Gate, pin2=Source,
# pin3=Drain - same package AND same pinout as the PMV230ENEA this replaces,
# so no footprint/placement changes needed, just a part swap. Replaced
# because PMV230ENEA's rating (1.5A@25C, derating to 0.9A@100C - BELOW F1's
# 1A fuse rating at engine-bay ambient) was a genuine problem, not just a
# margin note: a fuse can't protect a part that overheats at its own rated
# current. PMV37ENEA's RDSon (37-49mOhm typ/max) is ~4.5x lower than
# PMV230ENEA's (176-222mOhm), giving 3.5A@25C / 2.5A@100C - comfortably
# above F1's 1A rating with real margin at high temperature, same 60V VDS
# (no TVS/clamping rework needed), AEC-Q101 qualified, -55 to 175C range
# (wider than before). One minor, honestly-noted mismatch: PMV37ENEA's Vth
# max (2.7V) is slightly above LM74700-Q1's own stated preference ("2V to
# 2.5V maximum") for its light-load regulation scheme - not a hard limit,
# worth a bench check at light load before final part lock.
register_symbol(f"{LIB}:MOSFET_N", "Q", "PMV37ENEA automotive (AEC-Q101)", "Package_TO_SOT_SMD:SOT-23",
                {'L': [P(2, "S", "passive")], 'R': [P(3, "D", "passive")],
                 'B': [P(1, "G", "input")]})
# Real part: TI LM74700-Q1 "Low IQ Reverse Battery Protection Ideal Diode
# Controller" - this replaces LM74670-Q1, which turned out to be the wrong
# part (that one's for alternator/rectifier diode emulation, not reverse-
# battery protection at a power input). Real package: DBV, 6-pin SOT-23.
# Real pinout verified against TI's actual datasheet pin table.
register_symbol(f"{LIB}:IC_IdealDiode", "U", "LM74700-Q1", "Package_TO_SOT_SMD:SOT-23-6",
                {'L': [P(1, "VCAP", "passive"), P(2, "GND", "power_in"),
                       P(3, "EN", "input")],
                 'R': [P(4, "CATHODE", "input"), P(5, "GATE", "output"),
                       P(6, "ANODE", "input")]},
                datasheet="https://www.ti.com/lit/ds/symlink/lm74700-q1.pdf")
# Real part: TI LMR33630-Q1. Real package is VQFN-HR-12 (RNX0012C, 2x3x1mm),
# NOT HTSSOP-16 - that was a mistake caught during PCB footprint research;
# LMR33630 was never offered in HTSSOP-16 at all. Real 12-pin table verified
# against TI's actual datasheet Table 5-1 (pins 1/11=PGND, 2/10=VIN symmetric
# for EMI cancellation, 3=NC-tie-to-SW-per-datasheet, 4=BOOT, 5=VCC(internal
# 5V, do not load), 6=AGND, 7=FB, 8=PG(unused here), 9=EN, 12=SW). NO exposed
# pad on this package - an earlier version of this file had a 13th "EP" pin
# tied to GND, which was wrong: TI's own Pin Functions table has no EP row at
# all for the VQFN-HR variant, and the real land pattern (measured directly
# out of the datasheet's vector artwork, see build_u2_footprint.py) has
# exactly 12 pad clusters, not 13.
# Footprint: custom-built from TI's real package drawing (4225021/C 05/2022),
# not the bundled 4x4mm/11-pad placeholder - see build_u2_footprint.py for how
# the land pattern was derived and footprints/TI_RNX0012C_VQFN-HR.pretty for
# the result.
register_symbol(f"{LIB}:IC_Buck", "U", "LMR33630-Q1", "TI_RNX0012C_VQFN-HR:TI_RNX0012C_VQFN-HR-12_2x3mm_P0.5mm",
                {'L': [P(2, "VIN", "power_in"), P(10, "VIN", "power_in"),
                       P(1, "PGND", "power_in"), P(11, "PGND", "power_in")],
                 'R': [P(12, "SW", "output"), P(4, "BOOT", "passive"),
                       P(7, "FB", "input"), P(9, "EN", "input")],
                 'T': [P(5, "VCC", "power_out"), P(8, "PG", "output")],
                 'B': [P(6, "AGND", "power_in"), P(3, "NC", "passive")]},
                datasheet="https://www.ti.com/lit/ds/symlink/lmr33630-q1.pdf")
# Real part: TI TLV733P-Q1, AEC-Q100. Real package DBV, 5-pin SOT-23 (not the
# earlier assumed 3-pin package). Real pinout verified against TI's datasheet.
register_symbol(f"{LIB}:IC_LDO33", "U", "TLV733P-Q1", "Package_TO_SOT_SMD:SOT-23-5",
                {'L': [P(1, "IN", "power_in")], 'R': [P(5, "OUT", "power_out")],
                 'B': [P(2, "GND", "power_in"), P(3, "EN", "input"),
                       P(4, "NC", "no_connect")]},
                datasheet="https://www.ti.com/lit/ds/symlink/tlv733p-q1.pdf")

# Real, VERIFIED S32K144 64-pin LQFP pinout - extracted directly from NXP's
# own S32K1xx Series Reference Manual (Rev 8, 06/2018): the real pin table
# isn't in the manual's visible text at all, it's a spreadsheet
# ("S32K144_IO_Signal_Description_Input_Multiplexing.xlsx") embedded as a
# PDF file ATTACHMENT inside the Reference Manual - which is exactly why
# every earlier attempt at this (this session and prior ones) came up empty
# fetching the "pin table" as visible PDF text. Pulled out with pypdf's
# attachment API and read directly.
#
# THE PREVIOUS LQFP48 PLACEHOLDER WAS ACTUALLY WRONG, NOT JUST UNVERIFIED:
# cross-checked three ways against the Reference Manual (S32K144's own pin
# sheet, sibling S32K142's sheet, and the RM's package table) - the S32K144
# does not come in a 48-pin LQFP package AT ALL. Its real minimum package is
# 64-pin LQFP (58 GPIO pins + VDD x2/VSS x2/VDDA/VREFH). 48-pin LQFP is a
# real NXP package, but it belongs to the smaller S32K116/S32K118
# sub-family (Cortex-M0+ core, not the M4F this board was chosen for).
#
# Of the 58 real GPIO pins, 5 keep their real DEFAULT (reset-state)
# function rather than being repurposed as plain D/A pins - matching how
# this board already uses them: pin 11 (PTB7/EXTAL) and 12 (PTB6/XTAL) for
# the crystal, pin 62 (PTC4/SWD_CLK) and 64 (PTA4/SWD_DIO) for the SWD
# header, pin 63 (PTA5/RESET_b) for reset. VREFH (pin 9 - the real ADC
# voltage-reference pin) goes to J1's AREF, not a placeholder GPIO: AREF on
# a real board should reach the actual reference input pin, not just
# something with a similar name.
#
# The remaining 53 free-choice GPIO pins cover D0-D19 (20, up from the old
# placeholder's 14) and A0-A7 (8, chosen from the 27 that have a real
# ADCn_SEx alternate function, so "analog in" is a genuine capability here,
# not just a label) - D14-D19 are new, using J1's 6 previously-spare pins to
# use all 35 of J1's positions instead of leaving 6 unconnected (see
# J1_LEFT below). The other 25 free GPIO pins aren't wired to anything on
# this board (J1 has no more room for them) and are marked no_connect with
# their REAL pin numbers - genuine silicon, just not broken out here.
MCU_D_PINS = [1, 2, 3, 4, 5, 6, 13, 14, 15, 16, 17, 18, 19, 22]  # D0-D13
MCU_D_EXTRA_PINS = [23, 24, 35, 36, 39, 55]                       # D14-D19
MCU_A_PINS = [20, 21, 25, 26, 27, 28, 29, 30]                     # A0-A7, all ADCn_SEx-capable
MCU_RIGHT = ([P(pin, f"D{i}", "bidirectional") for i, pin in enumerate(MCU_D_PINS)] +
             [P(pin, f"A{i}", "bidirectional") for i, pin in enumerate(MCU_A_PINS)] +
             [P(63, "RESET", "input"), P(9, "AREF", "passive")])
MCU_LEFT = ([P(11, "OSC_IN", "passive"), P(12, "OSC_OUT", "passive"),
             P(64, "SWDIO", "bidirectional"), P(62, "SWCLK", "input")] +
            [P(pin, f"D{14 + i}", "bidirectional") for i, pin in enumerate(MCU_D_EXTRA_PINS)])
# Every real pin not spoken for above (25 of them) still has to appear on the
# symbol - a real 64-pin part has 64 real pads, and KiCad's "Update PCB from
# Schematic" rightly complains if the symbol doesn't account for all of
# them. Marked no_connect with their real pin numbers, split across
# top/bottom/left just to keep any one side from getting absurdly long -
# the split is cosmetic, not functional.
MCU_NC_TOP = [31, 32, 33, 34, 37, 38, 42, 43, 44]
MCU_NC_BOTTOM = [45, 46, 47, 48, 49, 50, 51, 52]
MCU_NC_LEFT = [53, 54, 56, 57, 58, 59, 60, 61]
register_symbol(f"{LIB}:MCU_STM32", "U", "NXP S32K144 automotive (AEC-Q100)",
                "Package_QFP:LQFP-64_10x10mm_P0.5mm",
                {'L': MCU_LEFT + [P(n, f"RESERVED_{n}", "no_connect") for n in MCU_NC_LEFT],
                 'R': MCU_RIGHT,
                 'T': [P(7, "VDD", "power_in"), P(41, "VDD", "power_in"),
                       P(8, "VDDA", "power_in")]
                      + [P(n, f"RESERVED_{n}", "no_connect") for n in MCU_NC_TOP],
                 'B': [P(10, "VSS", "power_in"), P(40, "VSS", "power_in")]
                      + [P(n, f"RESERVED_{n}", "no_connect") for n in MCU_NC_BOTTOM]},
                datasheet="https://www.nxp.com/products/processors-and-microcontrollers/s32-automotive-platform/s32k-auto-general-purpose-mcus:S32K-MCUS")

J1_LEFT = ([P(i + 2, f"D{i}", "bidirectional") for i in range(14)] +
           [P(i + 22, f"A{i}", "bidirectional") for i in range(8)] +
           [P(20, "RESET", "input"), P(21, "AREF", "passive")] +
           [P(i + 30, f"D{14 + i}", "bidirectional") for i in range(6)])
# Picked part: TE Connectivity AMPSEAL 776180-1 (aka "1-776180-1" per the
# SnapEDA download) - 35-position, 3-row, 4mm pitch, right-angle through-hole
# PCB header, sealable, -40..125C / IP67. 35 positions, ALL 35 used (D0-D19,
# A0-A7, RESET, AREF, VIN, +5V, +3V3, GND x2) - the 6 that used to be spare
# now carry D14-D19, now that U1's real 64-pin pinout has 20 real GPIO
# pins to spare for them instead of the old 48-pin placeholder's 14.
# Footprint downloaded from SnapEDA, structurally sanity-checked
# (35 numbered pads 1-35, no dupes/gaps, proper 12/11/12 staggered 3-row
# pattern, 2 non-plated mounting holes) - still worth a full dimensional
# check against TE's own datasheet drawing before trusting it for fab, since
# it's community-contributed, not TE-official. Copied into this project's
# footprints/ dir (see fp-lib-table, library nickname "TE_AMPSEAL_776180-1")
# instead of depending on the personal Downloads-folder path it first
# registered under, so the project stays portable.
register_symbol(f"{LIB}:CONN_IO", "J", "TE AMPSEAL 776180-1 (35-pos, R/A)",
                "TE_AMPSEAL_776180-1:TE_1-776180-1",
                {'L': J1_LEFT,
                 # +5V/+3V3 are "passive" here, not "power_out": J1 is a
                 # CONNECTOR exposing these rails to an external device, not
                 # a source generating them - typing them power_out claimed
                 # the connector itself was a second driver on nets already
                 # driven by U4's real LDO output, which ERC correctly
                 # read as two conflicting power outputs shorted together
                 # (pin_to_pin violation) even though electrically it's
                 # just one source and one pass-through exposure point.
                 'R': [P(1, "VIN", "passive"), P(16, "+5V", "passive"),
                       P(17, "+3V3", "passive"), P(18, "GND", "power_in"),
                       P(19, "GND", "power_in")]},
                hide_pin_names=True)
# SWD (not AVR ICSP) - same physical Tag-Connect footprint, different signals.
# Pin 4 was BOOT0 back when this was an STM32 - no confirmed equivalent pin
# exists on the NXP S32K1 family, so it's now a spare/NC on the header rather
# than a guessed connection.
register_symbol(f"{LIB}:CONN_SWD", "J", "SWD (Tag-Connect)",
                "Connector:Tag-Connect_TC2030-IDC-NL_2x03_P1.27mm_Vertical",
                {'L': [P(1, "SWDIO", "bidirectional"), P(3, "SWCLK", "input"),
                       P(5, "RST", "input")],
                 'R': [P(2, "VCC", "power_in"), P(4, "NC", "no_connect"),
                       P(6, "GND", "power_in")]},
                hide_pin_names=True)
register_symbol(f"{LIB}:CONN_UART", "J", "UART header",
                "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical",
                {'L': [P(1, "VCC", "power_in"), P(2, "GND", "power_in"),
                       P(3, "TX", "bidirectional"), P(4, "RX", "bidirectional")]},
                hide_pin_names=True)

# ---------------------------------------------------------------------------
# Placement + wiring
# ---------------------------------------------------------------------------
RAIL = 50.0

section_text("POWER INPUT, REVERSE-BATTERY + TRANSIENT PROTECTION", 30, 28)
section_text("5V BUCK + 3.3V LDO", 200, 28)
section_text("MCU CORE (NXP S32K1, 3.3V LOGIC)", 75, 140)
section_text("NANO-PINOUT I/O", 235, 140)
section_text("DECOUPLING / MISC", 320, 28)

# --- 12V rail, left to right ---
# Grade key on Value strings: Q100 G1 = AEC-Q100 Grade 1 (ICs, -40..125C);
# Q101 = AEC-Q101 (discrete semis); Q200 = AEC-Q200 (passives). Fuses and
# connectors are qualified under other automotive standards, not AEC-Qxxx.
place(f"{LIB}:Fuse", "F1", "Mini blade holder, 2A Littelfuse 297 fuse (SAE J2077/ISO 8820-3)", 50, RAIL,
      conn={'1': ('pwr', 'VIN'), '2': ('wire',)})
place(f"{LIB}:MOSFET_N", "Q1", "PMV37ENEA automotive (AEC-Q101)", 85, RAIL,
      conn={'2': ('wire',), '3': ('wire',),
            '1': ('label', 'GATE_DRV', 7.62)})
# LM74700-Q1: real pins verified against TI's datasheet. ANODE=battery-side
# (same net as Q1's Source), CATHODE=downstream-side (same net as Q1's
# Drain), EN tied to ANODE for always-on, VCAP needs an external charge-pump
# cap (C10) across VCAP and ANODE per the datasheet's own instruction. Value
# confirmed against TI's real design guidance (section 10.1.1.2.3): "VCAP:
# Minimum 0.1uF is required; recommended value of VCAP(uF) >= 10 x
# CISS(MOSFET)(uF)". Q1 (PMV37ENEA)'s real Ciss is 450pF (10x that is only
# 4.5nF), far below the flat 0.1uF minimum, so the minimum governs here -
# matches TI's own 12V-battery-protection example circuit (Figure 10-2),
# which uses exactly 0.1uF. Re-verified after swapping Q1 from PMV230ENEA
# to PMV37ENEA (see that symbol's registration comment) - still 0.1uF either
# way, since both parts' Ciss is tiny next to the flat minimum.
place(f"{LIB}:IC_IdealDiode", "U3", "LM74700-Q1 (AEC-Q100 G1)", 85, 85,
      conn={'6': ('label', 'VIN_FUSED', 5.08),
            # GND's stub MUST differ from pin 3's (both on the 'L' side, pin 3
            # directly below pin 2) - the GND riser continues one more PITCH
            # in the same direction pin 3's own stub already reaches, and a
            # matching length would land both on the same point, silently
            # shorting EN's "VIN_FUSED" label onto GND. Confirmed via netlist
            # diff during verification - don't reuse 5.08 here.
            '2': ('pwr', 'GND', 7.62),
            '5': ('label', 'GATE_DRV', 5.08), '4': ('label', 'VIN_PROT', 5.08),
            '3': ('label', 'VIN_FUSED', 5.08), '1': ('label', 'VCAP', 5.08)})
place(f"{LIB}:C_V", "C10", "100nF charge-pump cap (AEC-Q200)", 60, 100,
      conn={'1': ('label', 'VCAP'), '2': ('label', 'VIN_FUSED')})
place(f"{LIB}:TVS_V", "D1", "SMCJ33A automotive (AEC-Q101)", 113, 70,
      conn={'1': ('label', 'VIN_PROT'), '2': ('pwr', 'GND')})
place(f"{LIB}:C_V", "C1", "10uF X7R (AEC-Q200)", 150, 70,
      conn={'1': ('label', 'VIN_PROT'), '2': ('pwr', 'GND')})

# VIN and GND both come from off-sheet (the battery, via J1) with nothing
# HERE electrically "driving" them, per ERC - a real PWR_FLAG (not a supply
# symbol) tells ERC that's expected, not a missing connection. A bare,
# unwired flag isn't enough though (tried first, got "pin not connected" -
# same-name global-net merging isn't sufficient for ERC's own connectivity
# graph, unlike KiCad's actual netlist export): it has to be wired to a
# real point on the target net, same as any other symbol. Placed directly
# above/below its anchor pin (same X) so the wire is a plain vertical
# segment, matching this sheet's straight-wire style everywhere else.
vin_x, vin_y = pin_pos[("F1", "1")]
place_pwr_flag("VIN", vin_x, snap(vin_y - 10))
add_wire(vin_x, snap(vin_y - 10), vin_x, vin_y)
gnd_x, gnd_y = pin_pos[("D1", "2")]
place_pwr_flag("GND", gnd_x, snap(gnd_y + 10))
add_wire(gnd_x, snap(gnd_y + 10), gnd_x, gnd_y)

# LMR33630-Q1 real VQFN-12 pinout verified against TI's datasheet: symmetric
# VIN/PGND pairs for EMI cancellation, NC(3) tied to SW per the datasheet's
# own instruction, BOOT needs a cap to SW, VCC (internal 5V rail, don't load)
# needs its own decoupling cap, PG (power-good) unused here.
y_u2 = RAIL - off(f"{LIB}:IC_Buck", 2)[1]      # align U2 pin 2 (VIN) with the rail
place(f"{LIB}:IC_Buck", "U2", "LMR33630-Q1 (AEC-Q100 G1)", 170, y_u2,
      conn={'2': ('wire',), '10': ('label', 'VIN_PROT', 5.08),
            '1': ('pwr', 'GND', 5.08), '11': ('pwr', 'GND', 5.08),
            '12': ('wire',), '4': ('label', 'BOOT_CAP'),
            '7': ('label', 'FB'), '9': ('label', 'VIN_PROT', 5.08),
            '5': ('label', 'VCC_INT'), '8': ('nc',),
            '6': ('pwr', 'GND'), '3': ('label', 'SW')})
place(f"{LIB}:C_V", "C11", "100nF BOOT cap (AEC-Q200)", 195, 90,
      conn={'1': ('label', 'BOOT_CAP'), '2': ('label', 'SW')})
place(f"{LIB}:C_V", "C12", "1uF VCC decouple (AEC-Q200)", 218, 90,
      conn={'1': ('label', 'VCC_INT'), '2': ('pwr', 'GND')})
y_l1 = pin_pos[("U2", "12")][1] - off(f"{LIB}:L_H", 1)[1]
place(f"{LIB}:L_H", "L1", "10uH power (AEC-Q200)", 205, y_l1,
      conn={'1': ('wire',), '2': ('pwr', '+5V')})
place(f"{LIB}:C_V", "C2", "22uF X7R (AEC-Q200)", 232, 70,
      conn={'1': ('pwr', '+5V'), '2': ('pwr', 'GND')})
# FB divider - real bug found in final review, not just an unverified
# placeholder: TI's own datasheet (Section 8.2.2.3) gives Vout = VREF x
# (RFBT/RFBB + 1), VREF = 1V nominal, and TI's own worked 5V example uses
# RFBT=100k/RFBB=24.9k. The values this board had (R2=10k, R3=3.3k) solve
# to 1V x (10k/3.3k + 1) = ~4.03V, not 5V - a real 1V-low output, not a
# rounding nitpick: J1 exposes this rail as a Nano-compatible +5V pin for
# EXTERNAL peripherals, so mislabeling ~4V as +5V would under-power or
# malfunction anything expecting real 5V plugged into it, and it eats
# most of U4's LDO dropout margin too. Fixed by solving for RFBB with
# RFBT held at 10k: 10k / (5V/1V - 1) = 2.5k, standard E96 value 2.49k
# (suspiciously exactly TI's own 24.9k scaled by 10x - 3.3k was likely a
# typo/substitution for 2.49k at some point, not a deliberate choice).
place(f"{LIB}:R_V", "R2", "10k (AEC-Q200)", 155, 128,
      conn={'1': ('pwr', '+5V'), '2': ('label', 'FB')})
place(f"{LIB}:R_V", "R3", "2.49k (AEC-Q200)", 155, 148,
      conn={'1': ('label', 'FB'), '2': ('pwr', 'GND')})

# +5V stays on the board (buck output) purely so J1 can still offer a Nano-style
# 5V pin for external peripherals - the MCU itself now runs entirely on +3V3,
# which the LDO below produces and which becomes the PRIMARY/load-bearing rail.
# TLV733P-Q1 real pinout verified against TI's datasheet (DBV, 5-pin SOT-23).
place(f"{LIB}:IC_LDO33", "U4", "TLV733P-Q1 (AEC-Q100 G1)", 275, RAIL,
      conn={'1': ('pwr', '+5V'), '5': ('pwr', '+3V3'), '2': ('pwr', 'GND'),
            '3': ('pwr', '+5V')})
place(f"{LIB}:C_V", "C3", "1uF X7R (AEC-Q200)", 305, 70,
      conn={'1': ('pwr', '+3V3'), '2': ('pwr', 'GND')})

# rail wires
wire_pins("F1", 2, "Q1", 2, label="VIN_FUSED")
wire_pins("Q1", 3, "U2", 2, label="VIN_PROT")
wire_pins("U2", 12, "L1", 1, label="SW")

# --- MCU core + I/O bus ---
# STM32 core logic/supply is +3V3 (NOT +5V like the ATmega328P this replaces) -
# that's the headline consequence of the AEC-Q100 Grade 1 swap: no automotive-
# qualified 5V-native Arduino-ish MCU is broadly available, so this is now a
# 3.3V-logic board. +5V survives only as a Nano-compatibility pin on J1 (fed
# straight from the buck, same as before) for powering 5V peripherals - it is
# no longer connected to the MCU at all. See README for what this means for
# 5V shields/peripherals expecting true 5V logic on D0-D13/A0-A7.
place(f"{LIB}:MCU_STM32", "U1", "NXP S32K144 automotive (AEC-Q100)", 120, 195,
      conn={'11': ('wire',), '12': ('wire',),
            '64': ('label', 'SWDIO', 7.62), '62': ('label', 'SWCLK', 7.62),
            '7': ('pwr', '+3V3', 2.54), '41': ('pwr', '+3V3', 7.62),
            '8': ('label', 'VDDA', 12.7),
            '10': ('pwr', 'GND', 2.54), '40': ('pwr', 'GND', 7.62)})
            # Every MCU_RIGHT pin (D0-D13/A0-A7/RESET/AREF) and MCU_LEFT's
            # D14-D19 are deliberately left OUT of this conn dict, falling
            # through to place()'s own default: ('label', pin.name, LEAD) -
            # each pin gets a stub labeled with its own name (P(...)'s name
            # field already IS "D0"/"A0"/"RESET"/"AREF"/"D14" etc), so it
            # connects to J1's identically-named pin by label match, not a
            # drawn wire. This became necessary (not just a style choice)
            # once J1_LEFT grew past MCU_RIGHT's length: J1_LEFT now has 30
            # entries (added D14-D19) but MCU_RIGHT still has 24, so the two
            # symbols' pins no longer land at matching sheet coordinates
            # row-for-row (layout() centers each side on its OWN pin count) -
            # wire_pins()'s same-x-or-same-y alignment assert fails the
            # moment the two sides have different lengths. Labels don't
            # care about geometric alignment at all, so they sidestep this
            # entirely - same mechanism this file already uses for power
            # nets and D14-D19's own MCU-side connection.
place(f"{LIB}:CONN_IO", "J1", "TE AMPSEAL 776180-1 (35-pos, R/A)", 250, 195,
      conn={'1': ('pwr', 'VIN', 2.54), '16': ('pwr', '+5V', 5.08),
            '17': ('pwr', '+3V3', 7.62), '18': ('pwr', 'GND', 5.08),
            '19': ('pwr', 'GND', 2.54)})
            # J1_LEFT (D0-D19/A0-A7/RESET/AREF) is likewise left out here,
            # same default-label reasoning as U1 above.

# crystal: Y1 right pins aligned row-for-row with MCU left-side OSC pins.
# 8MHz is a placeholder value - whether this family even needs an external
# crystal (vs. running off its internal oscillator) and what frequency it
# supports depends on the exact part/clock config; confirm before ordering.
y_y1 = pin_pos[("U1", "11")][1] - off(f"{LIB}:XTAL", 1)[1]
place(f"{LIB}:XTAL", "Y1", "8MHz (AEC-Q200)", 75, y_y1,
      conn={'1': ('wire',), '2': ('wire',),
            '3': ('label', 'OSC_IN'), '4': ('label', 'OSC_OUT')})
wire_pins("Y1", 1, "U1", 11, label="OSC_IN")
wire_pins("Y1", 2, "U1", 12, label="OSC_OUT")
place(f"{LIB}:C_V", "C4", "18pF (AEC-Q200)", 55, y_y1 + 40,
      conn={'1': ('label', 'OSC_IN'), '2': ('pwr', 'GND')})
place(f"{LIB}:C_V", "C5", "18pF (AEC-Q200)", 78, y_y1 + 40,
      conn={'1': ('label', 'OSC_OUT'), '2': ('pwr', 'GND')})

# --- decoupling / misc, top right --- (VDD/VDDA now +3V3, not +5V)
place(f"{LIB}:C_V", "C6", "100nF (AEC-Q200)", 320, 60,
      conn={'1': ('pwr', '+3V3'), '2': ('pwr', 'GND')})
place(f"{LIB}:C_V", "C7", "100nF (AEC-Q200)", 340, 60,
      conn={'1': ('pwr', '+3V3'), '2': ('pwr', 'GND')})
place(f"{LIB}:L_H", "L2", "ferrite bead (AEC-Q200)", 368, 60,
      conn={'1': ('pwr', '+3V3'), '2': ('label', 'VDDA')})
place(f"{LIB}:C_V", "C8", "1uF (AEC-Q200)", 395, 60,
      conn={'1': ('label', 'VDDA'), '2': ('pwr', 'GND')})
place(f"{LIB}:R_V", "R1", "10k pull-up (AEC-Q200)", 320, 105,
      conn={'1': ('pwr', '+3V3'), '2': ('label', 'RESET')})
place(f"{LIB}:C_V", "C9", "100nF (AEC-Q200)", 350, 105,
      conn={'1': ('label', 'AREF'), '2': ('pwr', 'GND')})

# --- programming: SWD (not AVR ICSP) + UART, both now 3.3V-referenced ---
# pin4 (was BOOT0 on the STM32 version) is a spare/NC on this header now -
# no confirmed S32K1 equivalent, see the MCU symbol's own note.
place(f"{LIB}:CONN_SWD", "J2", "SWD Tag-Connect", 330, 160,
      conn={'1': ('label', 'SWDIO'), '3': ('label', 'SWCLK'), '5': ('label', 'RESET'),
            '2': ('pwr', '+3V3', 5.08),
            '6': ('pwr', 'GND', 5.08)})
place(f"{LIB}:CONN_UART", "J3", "UART header", 330, 205,
      conn={'1': ('pwr', '+3V3', 5.08), '2': ('pwr', 'GND', 7.62),
            '3': ('label', 'D1'), '4': ('label', 'D0')})

NOTE_LINES = [
    "NOTES:",
    "1. Power nets (+5V, +3V3, GND, VIN) use power symbols throughout; signal nets use",
    "   drawn wires, with local labels only where pins are legitimately shared.",
    "2. U2/U3/U4/Q1 pin numbers are REAL, verified against actual manufacturer",
    "   datasheets this session (TI for U2/U3/U4, Nexperia for Q1) - not generic",
    "   placeholders. U3 was also corrected from LM74670-Q1 (wrong part - that one's",
    "   for alternator rectification) to LM74700-Q1 (correct: reverse-battery).",
    "3. U1 pin numbers are REAL, verified against NXP's own S32K1xx Reference",
    "   Manual (64-pin LQFP - the 48-pin LQFP package considered earlier does not",
    "   exist for this chip family; 64-pin is the real minimum). Chip family/",
    "   package/AEC-Q100 qualification are also real and verified.",
    "4. Value fields tag each part's automotive qualification: Q100 G1 = AEC-Q100",
    "   Grade 1 (ICs, -40..125C) - Q101 = AEC-Q101 (discretes) - Q200 = AEC-Q200",
    "   (passives). Fuses/connectors use other automotive standards, not AEC-Qxxx.",
    "5. U1 is a real AEC-Q100-qualified automotive MCU (NXP S32K144, Arm Cortex-M4F)",
    "   - but this means NO Arduino IDE support at all (not even STM32duino-style):",
    "   NXP's own docs confirm the 'Arduino pin layout' is physical only. Programmed",
    "   via NXP's S32 Design Studio + SDK over SWD (J2), not an Arduino sketch flow.",
    "6. D0-D13/A0-A7 are OUR OWN Nano-style alias mapping for a custom board, not",
    "   a real chip's silicon pinout - confirm exact GPIO/pin numbers against the",
    "   real S32K1xx Reference Manual before layout.",
    "7. IMPORTANT: MCU logic is 3.3V, not 5V. D0-D13/A0-A7 on J1 will not drive",
    "   true 5V logic - only J1's dedicated +5V pin (unconnected to the MCU) is",
    "   actually 5V. See README before wiring up 5V-logic shields/peripherals.",
    "8. U2's real package is VQFN-HR-12 2x3x1mm ('RNX0012C', no exposed pad) - a",
    "   custom footprint built from TI's own package drawing (4225021/C 05/2022),",
    "   see build_u2_footprint.py, replaces the earlier wrong 4x4mm/11-pad guess.",
    "9. Q1 is PMV37ENEA (swapped from PMV230ENEA): same SOT-23 pinout/package,",
    "   but 2.5A at 100C ambient vs the old part's 0.9A - clears F1's 1A fuse",
    "   rating with real margin at engine-bay temperatures.",
    "10. ERC is clean except 4 EXPECTED findings (was 388 before a full pass -",
    "    see kicad-file-generation-gotchas memory for how): +5V/VDDA/U2-VIN each",
    "    show 'not driven' because their real source is on the far side of an",
    "    LC filter/ferrite/FET - ERC can't trace power through passives, this",
    "    is a known tool limitation, not a wiring bug. J2 SWCLK shows 'not",
    "    driven' because it's genuinely driven off-sheet, by the debug probe.",
]
for i, line in enumerate(NOTE_LINES):
    # Same center-justify-clips-long-text-off-the-page bug as section_text
    # above, same fix - these lines run up to 79 chars, even more prone to
    # it than the section titles were.
    texts.append(SchText(text=line, position=Position(30, 250 + i * 4.0, 0),
                         effects=Effects(font=Font(height=1.6, width=1.6),
                                          justify=Justify(horizontally="left"))))

# ---------------------------------------------------------------------------
sch.libSymbols = [entry[0] for entry in lib_symbols.values()]
sch.graphicalItems = wires
sch.labels = labels
sch.texts = texts
sch.noConnects = no_connects

OUT_SCH = r"C:\Users\root\Project\manifold-pcb\Manifold.kicad_sch"
os.makedirs(os.path.dirname(OUT_SCH), exist_ok=True)
sch.to_file(OUT_SCH)
print("Wrote", OUT_SCH)

# The schematic embeds every f"{LIB}:*" symbol inline (sch.libSymbols above),
# which is enough for KiCad to open/render/net-list this file on its own -
# but a real project also needs a sym-lib-table entry pointing the nickname
# at an actual library FILE, or KiCad's "Add Symbol" browser (and kicad-cli's
# own ERC, run standalone) can't resolve where it lives at all ("lib_symbol_
# issues", 70 hits before this fix - one per placed symbol, since every part
# in this design is custom). Fix: export the same Symbol objects already
# embedded in the schematic to a real, standalone {LIB}.kicad_sym via
# kiutils' SymbolLib, and register it in sym-lib-table - same pattern this
# project already uses for footprints (fp-lib-table + .pretty dirs).
from kiutils.symbol import SymbolLib
HERE = os.path.dirname(OUT_SCH)
SYM_LIB_FILE = os.path.join(HERE, f"{LIB}.kicad_sym")
symlib = SymbolLib(symbols=[entry[0] for entry in lib_symbols.values()])
symlib.to_file(SYM_LIB_FILE)
print("Wrote", SYM_LIB_FILE)

SYM_LIB_TABLE = os.path.join(HERE, "sym-lib-table")
with open(SYM_LIB_TABLE, "w", encoding="utf-8") as f:
    f.write(
        '(sym_lib_table\n'
        '\t(version 7)\n'
        f'\t(lib (name "{LIB}") (type "KiCad") (uri "${{KIPRJMOD}}/{LIB}.kicad_sym") '
        '(options "") (descr "Manifold project-local symbol library - '
        'regenerated by build_schematic.py, do not hand-edit"))\n'
        ')\n'
    )
print("Wrote", SYM_LIB_TABLE)

# --- validation: syntax round-trip + geometry ------------------------------
from kiutils.utils import sexpr
rep = Schematic.from_sexpr(sexpr.parse_sexp(open(OUT_SCH, encoding="utf-8").read()))
print(f"Round-trip OK: {len(rep.schematicSymbols)} symbols, "
      f"{len(rep.libSymbols)} lib symbols, {len(rep.labels)} labels, "
      f"{len(rep.graphicalItems)} wires")


def on_segment(p, a, b, tol=0.01):
    (px, py), (ax, ay), (bx, by) = p, a, b
    if abs(ax - bx) < tol:   # vertical
        return abs(px - ax) < tol and min(ay, by) - tol <= py <= max(ay, by) + tol
    if abs(ay - by) < tol:   # horizontal
        return abs(py - ay) < tol and min(ax, bx) - tol <= px <= max(ax, bx) + tol
    return False


segs = [((w.points[0].X, w.points[0].Y), (w.points[1].X, w.points[1].Y))
        for w in rep.graphicalItems]
bad = [l.text for l in rep.labels
       if not any(on_segment((l.position.X, l.position.Y), a, b) for a, b in segs)]
assert not bad, f"labels not on any wire: {bad}"

ends = {p for s in segs for p in s}
nc_ends = {(round(nc.position.X, 2), round(nc.position.Y, 2)) for nc in rep.noConnects}
lib = {s.libId: s for s in rep.libSymbols}
orphans = []
for inst in rep.schematicSymbols:
    for pin in lib[inst.libId].pins:
        pos = (round(inst.position.X + pin.position.X, 2),
               round(inst.position.Y - pin.position.Y, 2))
        # A pin terminated by a no-connect marker (either because its own
        # electrical type is no_connect, or because it's a real pin
        # deliberately left unused via a ('nc',) conn override) is not an
        # orphan.
        if pos in nc_ends:
            continue
        if pos not in ends and not pin.hide:
            orphans.append(f"{inst.properties[0].value}.{pin.number}")
assert not orphans, f"pins with no wire: {orphans}"
print("Geometry OK: every label sits on a wire, every visible pin touches a wire end")

# A GND pwr connection on an L/R-side pin adds an extra riser wire one PITCH
# further in the same direction, so its power symbol can land exactly on a
# neighboring pin's own stub endpoint if that pin uses a matching stub length
# - silently shorting that pin's real net onto GND (found the hard way via
# kicad-cli netlist diff: an entire net vanished, swallowed into GND). Check
# every coordinate that carries a label or a power-symbol pin maps to exactly
# one net name.
coord_net = {}
collisions = []
for l in rep.labels:
    key = (round(l.position.X, 2), round(l.position.Y, 2))
    if key in coord_net and coord_net[key] != l.text:
        collisions.append((key, coord_net[key], l.text))
    coord_net[key] = l.text
for inst in rep.schematicSymbols:
    if not inst.libId.startswith(f"{LIB}:PWR_"):
        continue
    net = inst.properties[1].value  # power symbol's Value IS its net name
    key = (round(inst.position.X, 2), round(inst.position.Y, 2))
    if key in coord_net and coord_net[key] != net:
        collisions.append((key, coord_net[key], net))
    coord_net[key] = net
assert not collisions, (
    f"two different nets land on the same coordinate (a GND riser probably "
    f"collided with a neighboring pin's stub - give one of them a different "
    f"stub length): {collisions}")
print("Net-collision check OK: no two different nets share a coordinate")

# --- upgrade to KiCad's current native format -------------------------------
# kiutils writes the older v6-era schema (version 20211014, generator "kiutils",
# unquoted uuids, no generator_version) - real KiCad 10 uses a newer dated
# schema (as of writing: 20260306, generator "eeschema", generator_version
# "10.0", quoted uuids). KiCad opens the older format fine but warns
# "created by an older version... converted when saved", and that warning
# reappears every time this script regenerates the file, undoing a manual
# save-to-upgrade in the GUI. Fix at the source: shell out to KiCad's own
# `sch upgrade` (not hand-replicate the schema - confirmed identical netlist
# before/after, see verification history) so the delivered file is always
# current-format already.
import shutil, subprocess

def find_kicad_cli():
    exe = shutil.which("kicad-cli")
    if exe:
        return exe
    for candidate in [
        r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe",
        r"C:\Program Files\KiCad\9.0\bin\kicad-cli.exe",
        r"C:\Program Files\KiCad\8.0\bin\kicad-cli.exe",
    ]:
        if os.path.isfile(candidate):
            return candidate
    return None

kicad_cli = find_kicad_cli()
if kicad_cli:
    result = subprocess.run([kicad_cli, "sch", "upgrade", OUT_SCH],
                            capture_output=True, text=True)
    if result.returncode == 0:
        print("Upgraded to current KiCad format:", result.stdout.strip())
    else:
        print("WARNING: kicad-cli sch upgrade failed, file left in kiutils' "
              "format (KiCad will still open it, just with the old-format "
              "warning):", result.stderr.strip())
else:
    print("NOTE: kicad-cli not found - file left in kiutils' format. KiCad "
          "will open it fine but show an 'older version' warning until you "
          "save it once from the GUI (or install KiCad here and rerun).")
