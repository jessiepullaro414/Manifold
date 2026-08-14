"""
Builds simple, dimensionally-real placeholder STEP models for parts whose
footprints ship with no 3D model at all (the footprint never had one, like
U2's custom-built footprint). These are NOT manufacturer-exact models (no
chamfers, marking text, exact lead-frame shape) - they're real-dimension
bounding-box-level placeholders, built with cadquery, good enough for a
KiCad 3D-viewer sanity check and clearance reasoning, not for
photorealistic BOM art.

Every dimension below is sourced, not guessed - see each part's own comment.
J2 (Tag-Connect TC2030-IDC-NF) deliberately has NO model here: its real
footprint (checked directly, Connector.pretty's own .kicad_mod) is 6 bare
"connect"-type pads with no component body at all - the physical connector
is a separate, detachable pogo-pin cable head that is never actually
mounted on the assembled board, so there is nothing physical to model.

F1 used to need a placeholder here too (bundled Fuse_Littelfuse-NANO2's own
3D model reference pointed at a file this KiCad install doesn't ship) -
resolved differently as of 2026-07-19: F1 is now a traditional Mini blade
fuse holder (Keystone 3568, bundled in Fuse.pretty), whose 3D model file
genuinely exists in this KiCad install, so no placeholder is needed for it
at all.
"""
import cadquery as cq
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "3dmodels")
os.makedirs(OUT_DIR, exist_ok=True)

BODY_COLOR = cq.Color(0.15, 0.15, 0.15)
TERMINAL_COLOR = cq.Color(0.75, 0.75, 0.78)


def export_assembly(parts, path):
    asm = cq.Assembly()
    for i, (shape, color) in enumerate(parts):
        asm.add(shape, name=f"part{i}", color=color)
    asm.save(path, exportType="STEP")
    print("Wrote", path)


# ---------------------------------------------------------------------------
# U2: LMR33630-Q1 in VQFN-HR-12 ("RNX0012C"). Same real TI package drawing
# already used to build the 2D footprint (build_u2_footprint.py, drawing
# 4225021/C 05/2022): body 1.9-2.1 x 2.9-3.1mm (nominal 2.0 x 3.0), height
# <=1.0mm max, no exposed pad. A flat gray QFN-body box is a reasonable
# placeholder for a package this small - the real part's individual gull-
# wing-free QFN leads are only ~0.2mm proud of the body edge, not visually
# significant at typical 3D-viewer zoom levels the way the fuse's end caps
# or J1's full connector shroud are.
def build_u2():
    L, W, H = 2.0, 3.0, 1.0
    body = cq.Workplane("XY").box(L, W, H)
    export_assembly([(body, BODY_COLOR)],
                     os.path.join(OUT_DIR, "TI_RNX0012C_VQFN-HR-12_2x3mm.step"))


if __name__ == "__main__":
    build_u2()
