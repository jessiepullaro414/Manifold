"""
Builds a correct VQFN-12-HR (RNX0012C) footprint for U2 (LMR33630-Q1), replacing
the wrong bundled 4x4mm/11-pad placeholder that shipped in build_pcb.py.

Where these numbers came from: TI's own LMR33630-Q1 datasheet (SLVSFP4, package
drawing 4225021/C, 05/2022), pages "RNX0012C PACKAGE OUTLINE" and "RNX0012C
EXAMPLE BOARD LAYOUT" (rendered from lmr33630q1.pdf with PyMuPDF since the
printed dimension-chain text comes out scrambled through pdftotext on this
drawing - garbled enough that hand-transcribing it risked a genuinely wrong,
mis-pitched footprint on a 0.5mm-pitch part). Rather than trust either the
scrambled text OR eyeballed pixel positions off the rendered page image, the
land-pattern page's actual vector paths were pulled straight out of the PDF
with page.get_drawings(), filtered to the drawing's pad-outline blue
(0.0, 0.498, 1.0) stroke color, and each pad's rectangle recovered by
clustering nearby path segments - a computed number straight from the
manufacturer's own vector drawing (at a discovered 20x embedded scale factor,
confirmed by cross-checking against the printed "SCALE: 20X" caption and two
independent printed dimensions: pin12's pad height (1.826mm measured vs 1.825mm
printed) and the PKG-centerline-to-pin12 distance (0.788mm measured vs 0.7875mm
printed) both landed within 0.001mm of the OCR'd label - strong confirmation
the measurement pipeline is right), not a hand-measured guess.

This also corrects a real error carried in this project's memory/notes: the
established "U2 pin mapping" previously included a 13th pin "EP" (exposed
thermal pad). TI's own Pin Functions table (Table 5-1, Section 5) lists only
pins 1-12 with no EP row, and this land pattern page has exactly 12 pad
clusters, not 13 - this VQFN-HR variant has NO exposed pad at all. The EP
belief was wrong and is dropped here.

Real, non-uniform pad layout (confirmed independently three ways: printed
"4X 0.5" pitch labels, the datasheet's own pin table, and this direct vector
measurement) - NOT a simple evenly-spaced 3-pins-per-side QFN:
  - Left column (X=-0.9mm): pins 1,2,3,4 top to bottom, Y = -1.126, -0.475,
    0.175, 0.675mm. Gaps are 0.651/0.650/0.500mm - two wider 0.65mm gaps near
    the top (clearance for pin 12), then standard 0.5mm pitch at the bottom.
  - Right column (X=+0.9mm): pins 11,10,9,8 mirroring the left column exactly.
  - Bottom row (Y=1.400mm): pins 5,6,7 at X = -0.5, 0, +0.5mm.
  - Pin 12 (SW, the switch node): a single, taller pad at X=0, Y=-0.788mm,
    0.25 x 1.826mm - deliberately bigger than the other pads, consistent with
    it carrying the buck converter's full switching current.
Side/bottom pad size: 0.6mm (radial, long axis) x 0.25mm (tangential) - i.e.
horizontal 0.6x0.25mm for the left/right columns, vertical 0.25x0.6mm for the
bottom row (rotated 90 since the radial direction rotates with position).

Verify after running: `kicad-cli pcb upgrade` (or load in the KiCad footprint
editor) on the output file, then re-run build_pcb.py + run_drc.py.
"""
import os
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "footprints", "TI_RNX0012C_VQFN-HR.pretty")
FP_NAME = "TI_RNX0012C_VQFN-HR-12_2x3mm_P0.5mm"
OUT_FILE = os.path.join(OUT_DIR, f"{FP_NAME}.kicad_mod")

# (pad number, x_mm, y_mm, size_x_mm, size_y_mm)
PADS = [
    (1, -0.9, -1.126, 0.6, 0.25),
    (2, -0.9, -0.475, 0.6, 0.25),
    (3, -0.9, 0.175, 0.6, 0.25),
    (4, -0.9, 0.675, 0.6, 0.25),
    (5, -0.5, 1.400, 0.25, 0.6),
    (6, 0.0, 1.400, 0.25, 0.6),
    (7, 0.5, 1.400, 0.25, 0.6),
    (8, 0.9, 0.675, 0.6, 0.25),
    (9, 0.9, 0.175, 0.6, 0.25),
    (10, 0.9, -0.475, 0.6, 0.25),
    (11, 0.9, -1.126, 0.6, 0.25),
    (12, 0.0, -0.788, 0.25, 1.826),
]

BODY_X = 2.0
BODY_Y = 3.0
COURTYARD_MARGIN = 0.25


def u():
    return str(uuid.uuid4())


def build():
    os.makedirs(OUT_DIR, exist_ok=True)
    lines = []
    lines.append(f'(footprint "{FP_NAME}"')
    lines.append('\t(version 20260206)')
    lines.append('\t(generator "build_u2_footprint.py")')
    lines.append('\t(generator_version "10.0")')
    lines.append('\t(layer "F.Cu")')
    lines.append(f'\t(uuid "{u()}")')
    lines.append('\t(descr "TI LMR33630-Q1, VQFN-HR-12 (RNX0012C), 2x3mm body, 0.5mm pitch, non-uniform pad spacing per TI package drawing 4225021/C 05/2022 - no exposed pad on this package variant")')
    lines.append('\t(tags "TI VQFN-HR RNX0012C LMR33630")')
    lines.append(f'\t(property "Reference" "REF**" (at 0 -2.2 0) (layer "F.SilkS") (uuid "{u()}")')
    lines.append('\t\t(effects (font (size 1 1) (thickness 0.15)))')
    lines.append('\t)')
    lines.append(f'\t(property "Value" "{FP_NAME}" (at 0 2.2 0) (layer "F.Fab") (uuid "{u()}")')
    lines.append('\t\t(effects (font (size 1 1) (thickness 0.15)))')
    lines.append('\t)')

    # Body outline on F.Fab
    hx, hy = BODY_X / 2, BODY_Y / 2
    corners = [(-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy), (-hx, -hy)]
    for (x0, y0), (x1, y1) in zip(corners, corners[1:]):
        lines.append(f'\t(fp_line (start {x0} {y0}) (end {x1} {y1}) (stroke (width 0.1) (type solid)) (layer "F.Fab") (uuid "{u()}"))')

    # Courtyard
    cx, cy = hx + COURTYARD_MARGIN, hy + COURTYARD_MARGIN
    ccorners = [(-cx, -cy), (cx, -cy), (cx, cy), (-cx, cy), (-cx, -cy)]
    for (x0, y0), (x1, y1) in zip(ccorners, ccorners[1:]):
        lines.append(f'\t(fp_line (start {x0} {y0}) (end {x1} {y1}) (stroke (width 0.05) (type solid)) (layer "F.CrtYd") (uuid "{u()}"))')

    # Pin-1 marker: small silkscreen line clear of pin 1's pad, near the top-left corner
    lines.append(f'\t(fp_line (start {-hx} {-hy - 0.3}) (end {-hx + 0.3} {-hy - 0.3}) (stroke (width 0.12) (type solid)) (layer "F.SilkS") (uuid "{u()}"))')
    lines.append(f'\t(fp_line (start {-hx} {-hy - 0.3}) (end {-hx} {-hy}) (stroke (width 0.12) (type solid)) (layer "F.SilkS") (uuid "{u()}"))')

    for num, x, y, sx, sy in PADS:
        lines.append(
            f'\t(pad "{num}" smd roundrect (at {x} {y}) (size {sx} {sy}) '
            f'(layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.25) (uuid "{u()}"))'
        )

    # Real-dimension placeholder body (see build_3d_models.py) - this custom
    # footprint never had a 3D model at all until now, since it's hand-built
    # from TI's package drawing rather than downloaded from a library that
    # would normally ship one alongside it.
    lines.append(
        '\t(model "${KIPRJMOD}/3dmodels/TI_RNX0012C_VQFN-HR-12_2x3mm.step"\n'
        '\t\t(offset (xyz 0 0 0))\n'
        '\t\t(scale (xyz 1 1 1))\n'
        '\t\t(rotate (xyz 0 0 0))\n'
        '\t)'
    )
    lines.append(')')
    content = "\n".join(lines) + "\n"
    with open(OUT_FILE, "w", newline="\n") as f:
        f.write(content)
    print("Wrote", OUT_FILE)


if __name__ == "__main__":
    build()
