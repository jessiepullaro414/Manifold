"""
Generates Manifold.kicad_pcb from Manifold.kicad_sch: real footprints
(loaded from their actual .kicad_mod files, not reinvented) placed in
non-overlapping, section-grouped positions, with every pad assigned the net
name kicad-cli's own netlist export says it should have.

What this does NOT do: route copper. Placement groups parts sensibly (mirrors
the schematic's sections) so a human has a sane starting point, but this is a
netlist-correct *unrouted* board - same state "Update PCB from Schematic"
leaves you in before you route it yourself.

Requires: Manifold.kicad_sch to exist (run build_schematic.py first) and
kicad-cli to be installed (used for: netlist export as ground truth, and
`pcb upgrade` at the end to guarantee current-format output, same as
build_schematic.py does for the .kicad_sch).
"""
import os
import re
import shutil
import subprocess
import uuid as uuid_module

from kiutils.board import Board
from kiutils.footprint import Footprint
from kiutils.items.common import Net, Position
from kiutils.items.brditems import LayerToken
from kiutils.items.gritems import GrLine, GrArc, GrPoly

HERE = os.path.dirname(os.path.abspath(__file__))
SCH = os.path.join(HERE, "Manifold.kicad_sch")
PCB = os.path.join(HERE, "Manifold.kicad_pcb")
KICAD_FOOTPRINTS = r"C:\Program Files\KiCad\10.0\share\kicad\footprints"
PROJECT_FOOTPRINTS = os.path.join(HERE, "footprints")


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


KICAD_CLI = find_kicad_cli()
if not KICAD_CLI:
    raise SystemExit("kicad-cli not found - needed for netlist export and pcb upgrade")

# ---------------------------------------------------------------------------
# 1. Ground truth: real ref/footprint list from the schematic, real net
#    assignments from kicad-cli's own netlist export (not re-derived from the
#    generator script's internal state, so this catches drift between the two
#    files just like the schematic's own self-checks do).
# ---------------------------------------------------------------------------
from kiutils.schematic import Schematic
from kiutils.utils import sexpr

sch = Schematic.from_sexpr(sexpr.parse_sexp(open(SCH, encoding="utf-8").read()))
parts = {}  # ref -> {"footprint": "lib:name", "value": str, "uuid": str}
for inst in sch.schematicSymbols:
    ref = next(p.value for p in inst.properties if p.key == "Reference")
    if ref.startswith("#"):
        continue  # power-flag symbols aren't physical parts
    fp = next((p.value for p in inst.properties if p.key == "Footprint"), "")
    val = next((p.value for p in inst.properties if p.key == "Value"), "")
    parts[ref] = {"footprint": fp, "value": val, "uuid": inst.uuid}

NETLIST_PATH = os.path.join(os.environ.get("TEMP", HERE), "manifold_netlist_for_pcb.net")
result = subprocess.run([KICAD_CLI, "sch", "export", "netlist", "--format", "kicadsexpr",
                         "--output", NETLIST_PATH, SCH], capture_output=True, text=True)
if result.returncode != 0:
    raise SystemExit(f"netlist export failed: {result.stderr}")

netlist_txt = open(NETLIST_PATH, encoding="utf-8").read()
pad_net = {}  # (ref, pin) -> net_name
net_names = []
for block in re.split(r"\(net\s", netlist_txt)[1:]:
    name = re.search(r'\(name "([^"]+)"\)', block).group(1).lstrip("/")
    nodes = re.findall(r'\(ref "([^"]+)"\)\s*\(pin "([^"]+)"\)', block)
    if len(nodes) < 2:
        continue  # shouldn't happen (schematic validated earlier), skip defensively
    net_names.append(name)
    for ref, pin in nodes:
        pad_net[(ref, pin)] = name

print(f"Loaded {len(parts)} real parts and {len(net_names)} nets from the schematic/netlist.")

# ---------------------------------------------------------------------------
# 2. Footprint loading
# ---------------------------------------------------------------------------
def load_footprint(lib_colon_name):
    lib, _, name = lib_colon_name.partition(":")
    project_path = os.path.join(PROJECT_FOOTPRINTS, f"{lib}.pretty", f"{name}.kicad_mod")
    if os.path.isfile(project_path):
        path = project_path
    else:
        path = os.path.join(KICAD_FOOTPRINTS, f"{lib}.pretty", f"{name}.kicad_mod")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"footprint file not found: {path}")
    fp = Footprint.from_file(path)
    fp.libId = lib_colon_name
    return fp


def footprint_bbox(fp):
    """Bounding box (min/max X/Y) from this footprint's pads AND silkscreen/
    courtyard graphics, in its own local (unplaced) coordinate frame. Pads
    alone undersell the real extent of parts like J1 - a right-angle
    through-hole connector whose body/shroud silkscreen outline sticks out
    well past its pad positions (confirmed via DRC: pad-only bbox produced
    repeated silk-clipped-by-board-edge findings no amount of extra margin
    fully cleared, because the margin was being measured from the wrong box)."""
    xs, ys = [], []
    for pad in fp.pads:
        hw, hh = pad.size.X / 2, pad.size.Y / 2
        xs += [pad.position.X - hw, pad.position.X + hw]
        ys += [pad.position.Y - hh, pad.position.Y + hh]
    for item in fp.graphicItems:
        if hasattr(item, "start") and hasattr(item, "end"):
            xs += [item.start.X, item.end.X]
            ys += [item.start.Y, item.end.Y]
        elif hasattr(item, "coordinates"):
            xs += [p.X for p in item.coordinates]
            ys += [p.Y for p in item.coordinates]
        elif hasattr(item, "center"):
            r = ((item.end.X - item.center.X) ** 2 + (item.end.Y - item.center.Y) ** 2) ** 0.5
            xs += [item.center.X - r, item.center.X + r]
            ys += [item.center.Y - r, item.center.Y + r]
    if not xs:
        return (-2, -2, 2, 2)
    return (min(xs), min(ys), max(xs), max(ys))


def local_to_board_bbox(lx0, ly0, lx1, ly1, x, y, angle):
    """Local footprint-space bbox -> absolute board-space bbox, using the
    same local->board position transform established elsewhere in this file
    for a 90deg rotation: (lx, ly) -> (ly, -lx) relative to the footprint's
    own placement (x, y). Needed for the Value-label lane assignment below,
    which has to compare DIFFERENT footprints' absolute extents against each
    other - unlike ref_label_pos/the pad-angle fix, which only ever needed a
    single part's own local frame."""
    corners = [(lx0, ly0), (lx1, ly0), (lx1, ly1), (lx0, ly1)]
    if angle == 90:
        corners = [(ly, -lx) for lx, ly in corners]
    xs = [x + cx for cx, cy in corners]
    ys = [y + cy for cx, cy in corners]
    return min(xs), min(ys), max(xs), max(ys)


# ---------------------------------------------------------------------------
# 3. Board scaffold
# ---------------------------------------------------------------------------
board = Board.create_new()

# 4-layer stackup: F.Cu(0)/B.Cu(31) come from create_new(); insert two inner
# copper layers at the ordinals KiCad expects them at (1, 2 - immediately
# after F.Cu). Sensible for this board regardless of the "make it smaller"
# ask: In1 as a solid GND pour and In2 as a +5V/+3V3 pour is standard
# practice for a board with a switching buck regulator on it (shorter return
# paths, better EMI) and this board already has one. Board Setup's exact
# dielectric thickness/material is left at KiCad's defaults - set those in
# the GUI if you have fab-specific requirements.
board.layers.insert(1, LayerToken(ordinal=1, name='In1.Cu', type='signal'))
board.layers.insert(2, LayerToken(ordinal=2, name='In2.Cu', type='signal'))

net_registry = {}  # name -> number
def net_number(name):
    if name not in net_registry:
        n = len(net_registry) + 1
        net_registry[name] = n
        board.nets.append(Net(number=n, name=name))
    return net_registry[name]


# ---------------------------------------------------------------------------
# 4. Placement: a single skyline bin-pack across ALL 27 small parts at once,
#    stacked directly against J1. J1 (the AMPSEAL connector) is ~20-40x
#    bigger than everything else on the board, so it - not the small parts -
#    sets the real width floor; the goal is to not waste space beyond that.
#
#    An earlier version split parts into two independent blocks ("actives"
#    column + "caps" grid) sized by a pre-computed proportional-area target,
#    then tried to drop small headers into whatever rectangular gap that
#    pairing left. That structurally can't reach true density: caps are
#    small and uniform and pack far more efficiently per mm^2 than actives,
#    so even a "correct" area-proportional width split left the caps block
#    shorter than the actives column, and the leftover rectangle only fit
#    parts that happened to match its exact shape - real open space stayed
#    open, which is exactly what got flagged. A single shared skyline (a
#    running height profile across the full width) lets ANY part drop into
#    ANY low spot in the profile, not just its own block's slice, and every
#    part (not just small headers) is tried at 0deg and 90deg to find the
#    tightest fit - so nothing is exempt from being moved or rotated to
#    close a gap.
# ---------------------------------------------------------------------------
MARGIN = 2.0    # tight but leaves courtyard clearance - 1.27mm (50 mil) turned
                # out too tight and produced a real courtyard-overlap DRC
                # error between two parts; this is a placement draft, not a
                # final hand-routed layout, so still err small, not generous

ORDER = [   # reading order only; the packer decides actual placement
    "F1", "Q1", "U3", "D1", "C1", "C10",                   # power input/protection
    "U2", "L1", "C2", "R2", "R3", "C11", "C12",            # 5V buck
    "U4", "C3",                                            # 3.3V LDO
    "U1", "Y1", "C4", "C5", "L2", "R1",                    # MCU core
    "C6", "C7", "C8", "C9",                                # decoupling/misc
    "J2", "J3",                                            # programming headers
]


def skyline_pack(refs, max_width, margin, sort_key=None, initial_skyline=None):
    """Skyline bottom-left bin-packing: maintains a height profile (a list of
    contiguous (x, width, height) segments) across max_width. Parts are
    placed largest-area-first; each part is tried both unrotated and rotated
    90deg, and goes wherever it yields the lowest resulting top edge (ties
    broken by leftmost x). This is the standard algorithm for "fill a fixed
    width as short as possible" - unlike shelf-packing into pre-sized blocks,
    every part can land in any gap the current profile has, so small parts
    naturally backfill the low spots next to big ones instead of being
    confined to a same-size-peers-only block. Returns (placed_dict,
    rotated_set, used_width, used_height).

    initial_skyline, if given, seeds the starting height profile instead of
    a flat 0 - used to reserve real keepout space (e.g. the two corner
    mounting holes) before any part is placed, so the packer naturally
    routes around it instead of needing a separate post-hoc overlap check."""
    sized = []
    for ref in refs:
        fp = load_footprint(parts[ref]["footprint"])
        x0, y0, x1, y1 = footprint_bbox(fp)
        sized.append((ref, x1 - x0, y1 - y0, x0, y0, x1, y1))
    sized.sort(key=sort_key or (lambda t: t[1] * t[2]), reverse=True)  # default: largest area first

    skyline = initial_skyline if initial_skyline is not None else [(0.0, max_width, 0.0)]

    def profile_height(x, w):
        h = 0.0
        for sx, sw, sh in skyline:
            if sx + sw <= x + 1e-9 or sx >= x + w - 1e-9:
                continue
            h = max(h, sh)
        return h

    def best_position(w):
        best = None
        # Try every segment's LEFT edge as a candidate, plus (per segment)
        # the position that right-justifies the item against that segment's
        # RIGHT edge - the plain left-edge-only version missed placements
        # where snugging an item up against a taller neighbor from the right
        # fits better than starting at the nearest lower step to its left.
        candidates = set()
        for sx, sw, sh in skyline:
            candidates.add(sx)
            candidates.add(sx + sw - w)
        for x in candidates:
            if x < -1e-9 or x + w > max_width + 1e-9:
                continue
            y = profile_height(x, w)
            if best is None or (y, x) < (best[0], best[1]):
                best = (y, x)
        return best

    def update_skyline(x, w, top):
        x_end = x + w
        segs = []
        for sx, sw, sh in skyline:
            s_end = sx + sw
            if s_end <= x + 1e-9 or sx >= x_end - 1e-9:
                segs.append((sx, sw, sh))
                continue
            if sx < x:
                segs.append((sx, x - sx, sh))
            if s_end > x_end:
                segs.append((x_end, s_end - x_end, sh))
        segs.append((x, w, top))
        segs.sort(key=lambda t: t[0])
        merged = []
        for seg in segs:
            if merged and abs(merged[-1][0] + merged[-1][1] - seg[0]) < 1e-6 \
                    and abs(merged[-1][2] - seg[2]) < 1e-6:
                merged[-1] = (merged[-1][0], merged[-1][1] + seg[1], merged[-1][2])
            else:
                merged.append(seg)
        return merged

    placed = {}
    rotated = set()
    for ref, w, h, x0, y0, x1, y1 in sized:
        # margin is folded into the reserved width/height so it lands as
        # trailing clearance on the right/top of every placed part - two
        # neighbors placed side by side or stacked always end up >= margin
        # apart, in either orientation.
        options = []
        pos0 = best_position(w + margin)
        if pos0 is not None:
            y, x = pos0
            options.append((y + h + margin, x, False))
        pos90 = best_position(h + margin)
        if pos90 is not None:
            y, x = pos90
            options.append((y + w + margin, x, True))
        if not options:
            raise RuntimeError(f"skyline_pack: {ref} ({w:.1f}x{h:.1f}mm) doesn't "
                                f"fit in max_width={max_width:.1f}mm even alone")
        options.sort(key=lambda o: (o[0], o[1]))
        top, x, is_rot = options[0]
        if is_rot:
            rw, rh = h + margin, w + margin
            skyline = update_skyline(x, rw, top)
            # KiCad's "at x y 90" rotates local (x,y) -> (y,-x) - confirmed
            # empirically against real kicad-cli DRC output (an earlier
            # (-y,x) assumption placed a rotated part's pads on the OPPOSITE
            # side from where KiCad actually renders them, which the bbox
            # math didn't catch but real pad-to-pad clearance DRC did: a
            # rotated header's far pads landed almost touching a neighboring
            # IC's pads instead of facing away from it). Bbox corners
            # (x0,y0)-(x1,y1) map to (y0,-x1)-(y1,-x0), so local origin is
            # (y0,-x1).
            placed[ref] = (x - y0, (top - rh) + x1)
            rotated.add(ref)
        else:
            rw, rh = w + margin, h + margin
            skyline = update_skyline(x, rw, top)
            placed[ref] = (x - x0, (top - rh) - y0)

    used_w, used_h = 0.0, 0.0
    for ref, w, h, x0, y0, x1, y1 in sized:
        px, py = placed[ref]
        if ref in rotated:
            used_w = max(used_w, px + y1)
            used_h = max(used_h, py - x0)
        else:
            used_w = max(used_w, px + x1)
            used_h = max(used_h, py + y1)
    return placed, rotated, used_w, used_h


def best_skyline_pack(refs, max_width, margin, initial_skyline=None):
    """skyline_pack is a greedy heuristic - its result depends on the order
    parts are considered in, and "largest area first" isn't always the order
    that packs shortest (e.g. a few very elongated parts, sorted by area
    alone, can land before smaller-but-taller parts that would have made a
    better anchor for the profile). Cheap to just try several reasonable
    orderings for ~27 parts and keep whichever one actually produces the
    smallest board: largest-area, longest-side, tallest, and widest first
    are all standard bin-packing tiebreak strategies, each better suited to
    different part-shape mixes."""
    strategies = {
        "area-desc": lambda t: t[1] * t[2],
        "max-side-desc": lambda t: max(t[1], t[2]),
        "height-desc": lambda t: t[2],
        "width-desc": lambda t: t[1],
        "perimeter-desc": lambda t: t[1] + t[2],
    }
    best_name, best_result = None, None
    for name, key in strategies.items():
        result = skyline_pack(refs, max_width, margin, sort_key=key, initial_skyline=initial_skyline)
        used_w, used_h = result[2], result[3]
        if best_result is None or (used_h, used_w) < (best_result[3], best_result[2]):
            best_name, best_result = name, result
    print(f"skyline pack: tried {len(strategies)} orderings, best was "
          f"'{best_name}' ({best_result[2]:.1f}x{best_result[3]:.1f}mm used)")
    return best_result


j1_fp_probe = load_footprint(parts["J1"]["footprint"])
jx0, jy0, jx1, jy1 = footprint_bbox(j1_fp_probe)
j1_width, j1_height = jx1 - jx0, jy1 - jy0

# J1 is a right-angle, edge/panel-mount connector (TE AMPSEAL 776180-1): its
# real F.Fab artwork includes a genuine "PCB EDGE" fabrication marker (a
# vendor-drawn reference line + text, confirmed in the downloaded footprint
# at local Y=13.5) showing where the PHYSICAL BOARD is meant to actually end
# - everything beyond that line is the connector's mating shroud, designed
# to overhang past the board into free air so a harness can plug in from
# outside an enclosure, not sit on top of more PCB material. footprint_bbox()
# (used for j1_height above) deliberately includes the FULL mechanical
# silhouette (needed elsewhere to keep other silkscreen/parts clear of the
# overhang), so it extends well past this line (to y=36.1) - using it
# directly for board_height would oversize the board, giving the connector
# a shelf of solid PCB under its overhang where there should be a real
# empty cutout. J1_PCB_EDGE_Y is the board-sizing-only value; j1_height
# stays the full mechanical extent for every other use.
J1_PCB_EDGE_Y = 13.5
j1_board_height = J1_PCB_EDGE_Y - jy0

BOARD_MARGIN = 3.0
J1_GAP = 3.0
# Board WIDTH is already fixed by J1 regardless of how tightly the small
# parts pack - it's by far the widest thing on the board - so the packer
# targets that full width and uses it to cut HEIGHT, the only dimension
# actually still free to shrink.
#
# The packed grid only needs ONE margin's worth of inset (its own left edge
# at BOARD_MARGIN) - it does NOT need a second BOARD_MARGIN reserved on the
# right on top of that, because board_width is computed as
# max(used_w, j1_width) + 2*BOARD_MARGIN regardless: as long as used_w stays
# <= j1_width, using the full j1_width here is completely free (the board
# was already going to be at least that wide because of J1) - it was an
# earlier bug to hand the packer j1_width - 2*BOARD_MARGIN, quietly
# reserving 6mm of width it could have used for free. A width sweep
# (73/77.4/79/81/84/88/95mm) confirms j1_width itself is the actual
# area-minimizing choice for this part mix - going wider still trims a
# little more height but grows board_width faster than that saves, so this
# isn't an arbitrary choice, it's the measured optimum.
pack_width = j1_width

# Two real screw-mount holes (user request: mount the board in a case),
# placed in the two top corners - "the other side of the J1 plug" from J1's
# own edge, since J1 already anchors that end. Reserved as real keepout
# space IN THE PACKER (an elevated starting skyline at each end, rather
# than placing components freely and discovering the collision afterward
# via DRC) - the same lesson as J1's own overhang/PCB-EDGE fix: a keepout
# needs to be a real input the placer respects, not a hope that nothing
# lands there.
MOUNTING_HOLE_FP = "MountingHole:MountingHole_3.2mm_M3"
MOUNTING_HOLE_INSET = 7.0    # hole center, from the board's own left/top or right/top edge
MOUNTING_HOLE_CLEARANCE = 5.0  # keepout radius around the hole center beyond its own courtyard
_hole_keepout = (MOUNTING_HOLE_INSET - BOARD_MARGIN) + MOUNTING_HOLE_CLEARANCE
initial_skyline = [
    (0.0, _hole_keepout, _hole_keepout),
    (_hole_keepout, pack_width - 2 * _hole_keepout, 0.0),
    (pack_width - _hole_keepout, _hole_keepout, _hole_keepout),
]

placed_rel, rotated_refs, used_w, used_h = best_skyline_pack(
    ORDER, max_width=pack_width, margin=MARGIN, initial_skyline=initial_skyline)
placed = {ref: (x + BOARD_MARGIN, y + BOARD_MARGIN) for ref, (x, y) in placed_rel.items()}
if rotated_refs:
    print(f"skyline pack: rotated 90deg to fill gaps: {sorted(rotated_refs)}")

# J1 right below the packed grid. J1's real silkscreen/mechanical outline
# extends past its pad bbox (confirmed by a silk_edge_clearance DRC hit at
# tighter spacing before this accounted for it), so it gets more clearance
# than the small-part grid does.
placed["J1"] = (BOARD_MARGIN - jx0, used_h + BOARD_MARGIN + J1_GAP - jy0)

board_width = max(used_w, j1_width) + 2 * BOARD_MARGIN
# No trailing "+ BOARD_MARGIN" here (unlike board_width) - j1_board_height
# already ends exactly at J1's real "PCB EDGE" marker, which IS the true
# board edge by definition, not a bbox to add clearance beyond.
board_height = used_h + BOARD_MARGIN + J1_GAP + j1_board_height

# Board.create_new() defaults to an A4 landscape page (297x210mm), and every
# coordinate up to here was computed as if (0, 0) were the board's own
# corner - which is exactly what KiCad's page origin (top-left) is too, so
# the ~83x62mm board ends up jammed into the page's top-left corner instead
# of sitting anywhere near the middle of it. Centering is a single shift
# applied once, here, to every already-computed placement (including J1),
# rather than reworking the packer itself to target a different origin -
# the packer's own internal geometry (skyline profile, margins) is still
# relative to a (0, 0) corner, it's just that "corner" that moves.
PAGE_W, PAGE_H = 297.0, 210.0
BOARD_OFFSET_X = round((PAGE_W - board_width) / 2, 2)
BOARD_OFFSET_Y = round((PAGE_H - board_height) / 2, 2)
placed = {ref: (round(x + BOARD_OFFSET_X, 2), round(y + BOARD_OFFSET_Y, 2))
          for ref, (x, y) in placed.items()}

# ---------------------------------------------------------------------------
# 5. Build footprint instances: real part, real pads, real nets, real position
# ---------------------------------------------------------------------------
ref_label_pos = {}  # ref -> local (dx, dy) for the Reference silkscreen text,
                     # computed from each part's own real bbox - see the note
val_layout = {}      # ref -> (abs_bbox, angle) for lane-assigning Value text
                     # AFTER every part is placed - see the note further down
                     # by board.to_file() below for why this has to be patched
                     # back into the file after writing it, not set here.
for ref, info in parts.items():
    fp = load_footprint(info["footprint"])
    x, y = placed[ref]
    angle = 90 if ref in rotated_refs else 0
    fp.position = Position(round(x, 3), round(y, 3), angle)
    fp.path = f"/{info['uuid']}"
    # A pad's own local (at x y [angle]) angle isn't summed with the parent
    # footprint's rotation for DRC/rendering purposes the way you'd expect -
    # confirmed directly (isolated minimal-footprint test, both via kiutils
    # and a 100% hand-written .kicad_pcb, no kiutils involved at all): a
    # rotated footprint's pads get their POSITION rotated correctly but keep
    # their ORIGINAL (unrotated) SHAPE unless the pad's own angle is set to
    # match. Only surfaced now because every previously-rotated part (R2, R3,
    # J3, Y1) happens to have near-square pads, where an unrotated-vs-rotated
    # shape swap is invisible - U2's 0.5mm-pitch, 0.6x0.25mm pads are the
    # first asymmetric-enough pads on this board to turn it into a real
    # solder-mask-bridge/clearance DRC failure. Fix: give every pad the same
    # explicit angle as its parent footprint, not just the footprint itself.
    if angle:
        for pad in fp.pads:
            pad.position.angle = angle
    lx0, ly0, lx1, ly1 = footprint_bbox(fp)
    # This offset is a LOCAL-frame point that KiCad rotates along with the
    # footprint when placing it (position-only rotation, which - unlike pad
    # shape above - has always worked correctly). "0.5mm above the local top
    # edge" only lands clear of the part in board space when angle==0: for a
    # 90deg-rotated part, local +Y maps to board -X (not board -Y), so the
    # same (0, ly0-0.5) offset instead lands the label to the SIDE of the
    # rotated part, in-line with whatever pads sit in that band - exactly the
    # silk_over_copper hit found on U2's rotated instance. Using the inverse
    # of the established local->board 90deg transform ((x,y)->(y,-x), so the
    # inverse is (X,Y)->(-Y,X)) for the same "just clear of the board-space
    # top edge" target point gives (lx1+0.5, 0) instead.
    if angle == 90:
        ref_label_pos[ref] = (round(lx1 + 0.5, 2), 0.0)
    else:
        ref_label_pos[ref] = (0.0, round(ly0 - 0.5, 2))
    val_layout[ref] = (local_to_board_bbox(lx0, ly0, lx1, ly1, x, y, angle),
                       angle, (lx0, ly0, lx1, ly1))
    for item in fp.graphicItems:
        if getattr(item, "type", None) == "reference":
            item.text = ref
        elif getattr(item, "type", None) == "value":
            item.text = info["value"]
    # The graphicItems text above is only the silkscreen drawing - KiCad's own
    # tools (DRC reports, BOM, the 3D viewer) read the Reference/Value from
    # this separate properties dict instead. Without this, every part showed
    # up everywhere as "REF**" - confirmed via the DRC JSON and the 3D render.
    fp.properties["Reference"] = ref
    fp.properties["Value"] = info["value"]
    # Many bundled footprints (mostly the small passives) carry a
    # "KiLib_Generator" property - KiCad's own footprint-wizard metadata,
    # e.g. "SMD_2terminal_chip_molded" - which unlike Description/Datasheet
    # does NOT default to hidden when left bare: it renders visibly on
    # F.Fab at (0, 0, 0) same as the original Reference bug did, and was
    # the real remaining cause of the text pile-up after fixing Value (this
    # only surfaced once Value stopped drowning it out). It's pure library
    # metadata, not documentation this board needs, so just drop it rather
    # than also lane-positioning a third kind of label.
    fp.properties.pop("KiLib_Generator", None)
    # U1 used to be special-cased here and left fully unwired: its schematic
    # pin numbers were a generic 48-pin placeholder that didn't reflect real
    # silicon, so mapping them onto real footprint pads by number coincidence
    # would have produced confidently WRONG connections. That's resolved now
    # - U1's pin numbers are the real, NXP-Reference-Manual-verified S32K144
    # 64-pin LQFP pinout (see build_schematic.py), so U1 gets normal net
    # assignment like every other part, no special case needed.
    unmatched = []
    for pad in fp.pads:
        # str() the pad number: most bundled footprints use quoted pad numbers
        # (parsed as str), but at least one real download (the AMPSEAL
        # connector) uses KiCad's legacy unquoted syntax, which kiutils parses
        # as a Python int - silently failing every lookup for that footprint
        # otherwise, since schematic pin numbers are always strings.
        key = (ref, str(pad.number))
        if key in pad_net:
            name = pad_net[key]
            pad.net = Net(number=net_number(name), name=name)
        else:
            unmatched.append(pad.number)
    if unmatched:
        print(f"  {ref}: {len(unmatched)} pad(s) with no schematic net "
              f"(spare/mechanical, e.g. J1's unused AMPSEAL cavities or a "
              f"true NC pin): {unmatched}")
    board.footprints.append(fp)

# Two case-mounting screw holes, top-left/top-right corners (see
# initial_skyline above for why the packer already left real room for
# these rather than placing them and hoping nothing collided). Not a
# schematic part - no net - but it turns out it DOES need the same bare-
# Reference-property fix as every real part below (see the big comment
# by ref_label_pos's patch loop): the bundled footprint's own Reference
# is already sensibly positioned at (0, -4.15) in the library file, but
# `fp.properties["Reference"] = mh_ref` overwrites that with a position-
# less bare string (kiutils limitation), so it silently fell back to
# KiCad's own (0,0,0) default - dead center on the part, on top of its
# own NPTH pad - confirmed via a real silk_over_copper DRC hit. Fixed by
# feeding the SAME real offset the bundled footprint already chose into
# ref_label_pos, so the existing patch loop picks these up too.
for mh_ref, mh_x in (("MH1", BOARD_OFFSET_X + MOUNTING_HOLE_INSET),
                      ("MH2", BOARD_OFFSET_X + board_width - MOUNTING_HOLE_INSET)):
    mh_fp = load_footprint(MOUNTING_HOLE_FP)
    mh_fp.position = Position(round(mh_x, 3), round(BOARD_OFFSET_Y + MOUNTING_HOLE_INSET, 3), 0)
    mh_fp.path = f"/{uuid_module.uuid4()}"
    mh_fp.properties["Reference"] = mh_ref
    ref_label_pos[mh_ref] = (0.0, -4.15)
    board.footprints.append(mh_fp)

# ---------------------------------------------------------------------------
# 5b. Value-label lane assignment - a real readability bug, not cosmetic:
# KiCad's own default position for a bare "Value" property (used below,
# same reasoning as the Reference patch) is a single fixed offset applied
# identically to every part. That's fine for a loosely-spaced board, but on
# this one's densely skyline-packed strip (many parts only ~2-4mm apart),
# every part's Value string - "SMCJ33A automotive (AEC-Q101)", "22uF X7R
# (AEC-Q200)", etc, all much WIDER than that spacing even at a small font -
# ends up smeared across its neighbors, illegible. Unlike the Reference fix
# (clearing a part's OWN bbox is enough, since References are short),
# Value text needs to be checked against NEIGHBORING parts too - a real
# label-placement problem, not just "clear my own pads".
#
# Fixed with a small font (0.5mm, vs Reference's 0.8mm - Value strings are
# much longer) plus greedy horizontal-interval "lane" assignment: sort
# parts left-to-right by their Value label's own center X, and place each
# one's label in the lowest lane whose last-placed label doesn't overlap
# it; each successive lane sits further above the part (more vertical
# clearance), so two parts can share a lane only if their (estimated) text
# widths don't actually collide.
VALUE_FONT = 0.5
VALUE_CHAR_W = 0.62 * VALUE_FONT   # empirical width-per-char for KiCad's default vector font at this size
VALUE_MARGIN = 0.6                 # minimum horizontal gap between two labels' estimated extents
LANE_STEP = 1.7                    # vertical spacing between lanes (font height + clearance)

val_order = sorted(val_layout.keys(),
                    key=lambda r: (val_layout[r][0][0] + val_layout[r][0][2]) / 2)
lane_right_edge = []  # lane index -> rightmost X reached so far in that lane
val_label_pos = {}    # ref -> local (dx, dy) for the Value text, patched in below
for ref in val_order:
    (bx0, by0, bx1, by1), angle, (lx0, ly0, lx1, ly1) = val_layout[ref]
    cx = (bx0 + bx1) / 2
    half_w = len(parts[ref]["value"]) * VALUE_CHAR_W / 2
    lane = next((i for i, edge in enumerate(lane_right_edge)
                 if cx - half_w > edge + VALUE_MARGIN), len(lane_right_edge))
    if lane == len(lane_right_edge):
        lane_right_edge.append(cx + half_w)
    else:
        lane_right_edge[lane] = cx + half_w
    clearance = 0.5 + lane * LANE_STEP
    # Same local->board rotation handling as ref_label_pos: for a 90deg part,
    # local +Y maps to board -X, so "clear of the board-space top edge"
    # needs the local X coordinate offset instead (mirrors the Reference fix
    # exactly, see its own comment above for the full derivation).
    if angle == 90:
        val_label_pos[ref] = (round(lx1 + clearance, 2), 0.0)
    else:
        val_label_pos[ref] = (0.0, round(ly0 - clearance, 2))

# ---------------------------------------------------------------------------
# 6. Board outline: rounded rectangle on Edge.Cuts (user request - a case-
#    mounted board looks and handles better without sharp corners, and it's
#    a real stress-concentration reduction too, not just cosmetic).
# ---------------------------------------------------------------------------
ox, oy = BOARD_OFFSET_X, BOARD_OFFSET_Y
ex, ey = BOARD_OFFSET_X + board_width, BOARD_OFFSET_Y + board_height
CORNER_RADIUS = 3.0
# Comfortably inside both the packed grid's own margin and the mounting-hole
# keepout (MOUNTING_HOLE_INSET=7 minus this radius still clears the hole's
# own courtyard - checked via DRC after generation, not just by eye).
R = CORNER_RADIUS
K = R * (1 - 2 ** -0.5)  # straight-line inset of a 90deg arc's own midpoint from its square corner

def _arc(start, mid, end):
    board.graphicItems.append(GrArc(
        start=Position(round(start[0], 3), round(start[1], 3)),
        mid=Position(round(mid[0], 3), round(mid[1], 3)),
        end=Position(round(end[0], 3), round(end[1], 3)),
        layer="Edge.Cuts", width=0.1))

def _line(p1, p2):
    board.graphicItems.append(GrLine(
        start=Position(round(p1[0], 3), round(p1[1], 3)),
        end=Position(round(p2[0], 3), round(p2[1], 3)),
        layer="Edge.Cuts", width=0.1))

# Clockwise from top edge: top -> TR corner -> right edge -> BR corner ->
# bottom edge -> BL corner -> left edge -> TL corner -> (back to top start).
_line((ox + R, oy), (ex - R, oy))                       # top
_arc((ex - R, oy), (ex - K, oy + K), (ex, oy + R))       # TR corner
_line((ex, oy + R), (ex, ey - R))                        # right
_arc((ex, ey - R), (ex - K, ey - K), (ex - R, ey))       # BR corner
_line((ex - R, ey), (ox + R, ey))                        # bottom
_arc((ox + R, ey), (ox + K, ey - K), (ox, ey - R))       # BL corner
_line((ox, ey - R), (ox, oy + R))                        # left
_arc((ox, oy + R), (ox + K, oy + K), (ox + R, oy))       # TL corner

# ---------------------------------------------------------------------------
# 6b. Back-silkscreen logo. B.SilkS is otherwise completely empty - nothing
#     is mounted on the back of this board - so there's no density tradeoff
#     here the way there would be on the front, and no need to dodge any
#     front-side layout at all (opposite faces of the board don't interact).
#     Centered on the whole board, same as logo_cx - simplest option since
#     literally anywhere on B.SilkS is equally "clear". (Previously centered
#     on J1's footprint bbox "shadow" below its pad rows, back when the
#     board extended that far - once board_height was corrected to stop at
#     J1's real "PCB EDGE" fab marker instead of its full mechanical
#     envelope, that shadow region isn't part of the board at all anymore,
#     so that anchor point stopped making sense.)
#
#     Source: both.kicad_sym, a bitmap2component-generated schematic symbol
#     (real vector-traced artwork - 29 polylines / 1457 points, ~81.5x60.4mm
#     in the symbol's own units, not something this script invents).
#
#     Two kiutils/gr_poly pitfalls, both confirmed against a REAL gr_poly
#     from pcbnew.PCB_SHAPE(SHAPE_T_POLY) (not guessed) - same underlying
#     lesson as gr_text's segfault, different token:
#     1. uuid is not optional - kiutils' `tstamp` field writes an UNQUOTED
#        `(tstamp xxxx-xxxx...)`, but real KiCad wants a quoted
#        `(uuid "xxxx-xxxx...")`, and kicad-cli SEGFAULTS loading a gr_poly
#        missing one entirely. Patched post-write below, same string-replace
#        pattern as the Reference-label fix.
#     2. Shapes on a back layer are NOT auto-mirrored by KiCad - only TEXT
#        glyphs get a `mirror` flag (see gr_text's `Justify`); a plain
#        polygon's points are used as literal board coordinates, so artwork
#        drawn in front-view coordinates reads backwards once the physical
#        board is flipped over. Mirrored by hand below (negate X around the
#        shape's own center before placing) - same visual-correctness fix as
#        the text's Justify(mirror=True), just done on the point data since
#        polygons have no equivalent flag.
# ---------------------------------------------------------------------------
def load_logo_polylines(path):
    text = open(path, encoding="utf-8").read()
    polylines = []
    for block in re.findall(r'\(polyline\s*\(pts(.*?)\)\s*\(stroke', text, re.S):
        pts = [(float(m.group(1)), float(m.group(2)))
               for m in re.finditer(r'\(xy ([\-0-9.]+) ([\-0-9.]+)\)', block)]
        if pts:
            polylines.append(pts)
    return polylines

# A back-side logo only needs to dodge PLATED holes (thru_hole/np_thru_hole
# pads punch through the entire board, unlike smd pads which live on one
# face only) - not every front-side footprint. Gathered from the actual
# placed board.footprints (all already appended by this point in the
# script), in absolute board coordinates, inflated by a small clearance.
# Needed once board_height stopped including J1's overhang "shadow" (see
# J1_PCB_EDGE_Y above) - that region used to be free real estate for this
# logo; on the shorter, correctly-sized board, J1's own pin field now
# occupies real board area right up to the true edge, so a fixed anchor
# point (board center, or J1's old shadow) can land right on top of it.
PAD_CLEARANCE_MM = 1.0
thru_hole_boxes = []
for _fp in board.footprints:
    _fx, _fy = _fp.position.X, _fp.position.Y
    _fangle = _fp.position.angle or 0
    for _pad in _fp.pads:
        if _pad.type not in ("thru_hole", "np_thru_hole"):
            continue
        _lx, _ly = _pad.position.X, _pad.position.Y
        _hw, _hh = _pad.size.X / 2, _pad.size.Y / 2
        if _fangle == 90:
            _ax, _ay = _fx + _ly, _fy - _lx
            _hw, _hh = _hh, _hw
        else:
            _ax, _ay = _fx + _lx, _fy + _ly
        thru_hole_boxes.append((_ax - _hw - PAD_CLEARANCE_MM, _ay - _hh - PAD_CLEARANCE_MM,
                                 _ax + _hw + PAD_CLEARANCE_MM, _ay + _hh + PAD_CLEARANCE_MM))


def _overlaps(bx0, by0, bx1, by1, boxes):
    return any(bx0 < ox1 and bx1 > ox0 and by0 < oy1 and by1 > oy0 for ox0, oy0, ox1, oy1 in boxes)


LOGO_PATH = os.path.join(HERE, "both.kicad_sym")
logo_uuids = []
if os.path.isfile(LOGO_PATH):
    logo_polylines = load_logo_polylines(LOGO_PATH)
    all_x = [x for poly in logo_polylines for x, y in poly]
    all_y = [y for poly in logo_polylines for x, y in poly]
    lx0, lx1, ly0, ly1 = min(all_x), max(all_x), min(all_y), max(all_y)
    lcx, lcy = (lx0 + lx1) / 2, (ly0 + ly1) / 2
    board_cx = BOARD_OFFSET_X + board_width / 2
    board_cy = BOARD_OFFSET_Y + board_height / 2
    EDGE_CLEARANCE_MM = 1.0  # keep the logo's own silkscreen off the board edge too

    def _find_logo_spot(height_mm):
        """Scan both X and Y (not just Y at a fixed board-center X) for a
        position clear of every thru-hole pad: J2/J3 (thru-hole) sit near
        the top of this board while J1's pin field occupies almost the
        full width near the bottom, so the board-center column is blocked
        both above and below with no vertical gap tall enough for the logo
        at its original size - a real free pocket only exists off-center
        (right of J2/J3, above J1). Returns (cx, cy, half_w, half_h) for
        the found spot (falling back to dead-center if truly nothing
        clears, which the caller's shrinking retry loop is meant to avoid
        needing)."""
        scale = height_mm / (ly1 - ly0)
        half_w, half_h = (lx1 - lx0) * scale / 2, height_mm / 2
        candidates = []
        x = BOARD_OFFSET_X + half_w + EDGE_CLEARANCE_MM
        x_end = BOARD_OFFSET_X + board_width - half_w - EDGE_CLEARANCE_MM
        while x <= x_end:
            y = BOARD_OFFSET_Y + half_h + EDGE_CLEARANCE_MM
            y_end = BOARD_OFFSET_Y + board_height - half_h - EDGE_CLEARANCE_MM
            while y <= y_end:
                if not _overlaps(x - half_w, y - half_h, x + half_w, y + half_h, thru_hole_boxes):
                    candidates.append((x, y))
                y += 0.5
            x += 0.5
        if candidates:
            return min(candidates, key=lambda c: (c[0] - board_cx) ** 2 + (c[1] - board_cy) ** 2) + (half_w, half_h)
        return (board_cx, board_cy, half_w, half_h)

    # Start at the old fixed size and shrink until an actually-clear pocket
    # is found, rather than assuming any particular size will fit - this
    # board's remaining free space depends on the exact pad layout, not a
    # fraction of board_height alone.
    LOGO_HEIGHT_MM = min(20.0, board_height * 0.55)
    for _ in range(30):
        logo_cx, logo_cy, logo_half_w, logo_half_h = _find_logo_spot(LOGO_HEIGHT_MM)
        if (logo_cx, logo_cy) != (board_cx, board_cy) or not thru_hole_boxes:
            break
        LOGO_HEIGHT_MM *= 0.9
    scale = LOGO_HEIGHT_MM / (ly1 - ly0)
    for poly in logo_polylines:
        # Two DIFFERENT flips, easy to conflate: (a) both.kicad_sym is a
        # schematic SYMBOL file, and symbol space is Y-up while PCB board
        # space is Y-down (the exact same convention gotcha build_schematic.py
        # documents for placing symbol pins) - not flipping Y here left the
        # first attempt upside down. (b) the back-layer mirror (negate X)
        # for physical readability, same reasoning as gr_text's mirror flag.
        # Both apply together, not either/or.
        coords = [Position(round(-(x - lcx) * scale + logo_cx, 3),
                            round(-(y - lcy) * scale + logo_cy, 3))
                  for x, y in poly]
        poly_uuid = str(uuid_module.uuid4())
        logo_uuids.append(poly_uuid)
        board.graphicItems.append(GrPoly(
            layer="B.SilkS", coordinates=coords, width=0.05, fill="yes",
            tstamp=poly_uuid))
    print(f"Added {len(logo_polylines)}-polygon logo to B.SilkS from {LOGO_PATH}")
else:
    print(f"NOTE: {LOGO_PATH} not found - skipping logo")

print(f"Board outline: {board_width:.1f} x {board_height:.1f} mm, "
      f"{len(board.footprints)} footprints, {len(net_registry)} nets")

# ---------------------------------------------------------------------------
# 7. Verification on the IN-MEMORY board (before writing/upgrading): overlap
#    check and net pin-count check. Done here rather than by re-parsing the
#    written file, because kiutils' own reader can't parse the abbreviated
#    "(net 0)" token KiCad's real writer uses for unconnected pads once the
#    file has gone through `pcb upgrade` - a kiutils reader limitation, not a
#    problem with the file itself (same issue hit and worked around in
#    build_schematic.py). The in-memory `board.footprints` is authoritative
#    for what was actually generated either way.
# ---------------------------------------------------------------------------
MECHANICAL_FOOTPRINT_COUNT = 2  # MH1/MH2 case-mounting holes - not schematic parts
assert len(board.footprints) == len(parts) + MECHANICAL_FOOTPRINT_COUNT, \
    (f"footprint count mismatch: {len(board.footprints)} vs {len(parts)} parts "
     f"+ {MECHANICAL_FOOTPRINT_COUNT} mechanical")

boxes = []
for fp in board.footprints:
    x0, y0, x1, y1 = footprint_bbox(fp)
    if fp.position.angle == 90:
        # Same KiCad rotation used at placement time: local (x,y) -> (y,-x),
        # so the local bbox corners map to (y0,-x1)-(y1,-x0). Skipping this
        # for rotated parts (as an earlier version did) produces a false
        # overlap-or-clear here - it checks the UNROTATED footprint against
        # neighbors placed relative to its actual ROTATED footprint. (An
        # earlier version of this transform used the wrong rotation
        # direction (-y,x) - it looked internally consistent, since both the
        # placer and this check used the same wrong formula, but it didn't
        # match KiCad's real rotation, so real DRC caught pad clearance
        # violations this bbox check couldn't see either way.)
        x0, y0, x1, y1 = y0, -x1, y1, -x0
    boxes.append((fp.path, fp.position.X + x0, fp.position.Y + y0,
                 fp.position.X + x1, fp.position.Y + y1))
overlaps = []
for i in range(len(boxes)):
    for j in range(i + 1, len(boxes)):
        _, ax0, ay0, ax1, ay1 = boxes[i]
        _, bx0, by0, bx1, by1 = boxes[j]
        if ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1:
            overlaps.append((boxes[i][0], boxes[j][0]))
assert not overlaps, f"overlapping footprint bounding boxes: {overlaps}"
print("Placement OK: no overlapping footprint bounding boxes")

pcb_net_pins = {}
seen_logical_pins = set()
for fp in board.footprints:
    ref = fp.properties.get("Reference")
    for pad in fp.pads:
        if pad.net and pad.net.name:
            # Some real footprints (e.g. F1's Keystone 3568 Mini blade fuse
            # holder) have TWO physical pads sharing the same pad NUMBER per
            # terminal (redundant solder joints for mechanical strength) -
            # that's one logical connection point, same as the schematic's
            # one pin, so dedupe by (ref, pad.number) rather than counting
            # every physical pad instance.
            logical_pin = (ref, pad.number)
            if logical_pin in seen_logical_pins:
                continue
            seen_logical_pins.add(logical_pin)
            pcb_net_pins.setdefault(pad.net.name, 0)
            pcb_net_pins[pad.net.name] += 1
sch_net_pins = {}
for (ref, pin), name in pad_net.items():
    sch_net_pins[name] = sch_net_pins.get(name, 0) + 1
mismatches = {n: (sch_net_pins[n], pcb_net_pins.get(n, 0)) for n in sch_net_pins
              if sch_net_pins[n] != pcb_net_pins.get(n, 0)}
assert not mismatches, f"net pin-count mismatches (schematic vs PCB): {mismatches}"
print(f"Net check OK: all {len(sch_net_pins)} nets have matching pin "
      f"counts between schematic and PCB")

board.to_file(PCB)
print("Wrote", PCB)

# kiutils' Footprint.properties is a plain {name: value} str dict (see
# gotchas doc) - it has no field for a property's own position/layer/font, so
# to_file() can only write a bare `(property "Reference" "REF")` token for
# every part, with the position stripped out even if the original library
# footprint had one. KiCad's own `pcb upgrade` below fills in whatever's
# missing on a bare property using ITS OWN built-in default for that
# property NAME - Reference defaults to (at 0 0 0) on F.SilkS at 1.27mm -
# which lands every single reference designator dead center on the part, on
# top of its own pads, no matter how small the part is. That's the actual
# cause of every silk_over_copper/silk_overlap DRC finding on this board
# (confirmed: Value/Datasheet/Description bare properties DON'T show up in
# those violations, because KiCad's default for THOSE property names is an
# F.Fab/hidden placement, not visible silkscreen - it's specifically
# Reference's default that's the problem). Patched here, before the upgrade
# step, by giving each Reference its own real position (just clear of that
# part's own bbox, computed above) and a font size that actually fits at 2mm
# part spacing - exact string match (not upgrade-tolerant regex) because this
# runs BEFORE upgrade, while the bare one-line form kiutils writes is still
# exactly known.
text = open(PCB, encoding="utf-8").read()
for ref, (dx, dy) in ref_label_pos.items():
    old = f'(property "Reference" "{ref}")'
    new = (f'(property "Reference" "{ref}" (at {dx} {dy} 0) (layer "F.SilkS") '
           f'(effects (font (size 0.8 0.8) (thickness 0.12))))')
    count = text.count(old)
    assert count == 1, f"expected exactly 1 bare Reference property for {ref}, found {count}"
    text = text.replace(old, new, 1)

# Same bare-property patch as Reference above, but Value text is NOT unique
# per part (several caps share "100nF (AEC-Q200)", for instance) - matching
# and replacing by value-string content alone, in val_order (sorted by lane
# position, not file position), would consume the FIRST matching occurrence
# in the FILE regardless of which part's computed position it's supposed to
# get, silently assigning the wrong (dx, dy) to the wrong part whenever two
# parts share a value. Iterating val_layout in its own insertion order
# instead - which matches parts.items()'s order, which is the same order
# footprints were appended to the board and therefore the same order they
# appear in the file - keeps each sequential .replace(old, new, 1) call
# consuming occurrences left-to-right in true file order, so it lines up
# with the intended part even when several bare properties are textually
# identical.
for ref in val_layout:
    dx, dy = val_label_pos[ref]
    value = parts[ref]["value"]
    old = f'(property "Value" "{value}")'
    new = (f'(property "Value" "{value}" (at {dx} {dy} 0) (layer "F.Fab") '
           f'(effects (font (size {VALUE_FONT} {VALUE_FONT}) (thickness 0.08))))')
    assert old in text, f"expected a bare Value property for {ref} ({value!r})"
    text = text.replace(old, new, 1)

# Same kiutils-output-doesn't-match-real-KiCad problem, different token: each
# back-silkscreen logo polygon's uuid was set via GrPoly's `tstamp` field,
# which kiutils writes as a bare, UNQUOTED `(tstamp xxxx)` - not the quoted
# `(uuid "xxxx")` a real KiCad-written gr_poly has. Same fix, same reasoning
# as the Reference patch above: exact string replace, not regex.
for poly_uuid in logo_uuids:
    old_tstamp = f"(tstamp {poly_uuid})"
    new_uuid = f'(uuid "{poly_uuid}")'
    count = text.count(old_tstamp)
    assert count == 1, f"expected exactly 1 logo-polygon tstamp token for {poly_uuid}, found {count}"
    text = text.replace(old_tstamp, new_uuid, 1)

open(PCB, "w", encoding="utf-8").write(text)
print(f"Repositioned {len(ref_label_pos)} Reference labels clear of their own footprints")

# ---------------------------------------------------------------------------
# 8. Upgrade to current KiCad format (same reasoning as build_schematic.py),
#    then run a real DRC via kicad-cli (KiCad's own engine, not kiutils).
# ---------------------------------------------------------------------------
result = subprocess.run([KICAD_CLI, "pcb", "upgrade", PCB], capture_output=True, text=True)
print("Upgraded to current KiCad format:" if result.returncode == 0 else "WARNING: upgrade failed:",
      (result.stdout or result.stderr).strip())

drc_path = os.path.join(os.environ.get("TEMP", HERE), "manifold_pcb_drc.json")
result = subprocess.run([KICAD_CLI, "pcb", "drc", "--format", "json",
                         "--output", drc_path, "--exit-code-violations", PCB],
                        capture_output=True, text=True)
import json
drc = json.load(open(drc_path, encoding="utf-8"))
violations = drc.get("violations", [])
by_type = {}
for v in violations:
    t = v.get("type", "unknown")
    by_type[t] = by_type.get(t, 0) + 1
print("DRC violation summary (unrouted board - 'unconnected_items' is EXPECTED "
      "for every net, everything else is worth a look):")
for t, count in sorted(by_type.items()):
    print(f"  {t}: {count}")
unexpected = {t: c for t, c in by_type.items() if t != "unconnected_items"}
if unexpected:
    print("NOTE: non-routing DRC findings present, see", drc_path, "for details:", unexpected)
else:
    print("No unexpected DRC findings (only unrouted-net warnings, as expected).")
