"""
Routes Manifold.kicad_pcb with FreeRouting, then adds GND/+3V3 zone
pours on top of the finished routing.

Four steps, each independently verifiable:
  1. Export a Specctra .dsn from the board using KiCad's OWN Python API
     (pcbnew.ExportSpecctraDSN) - the real exporter built into pcbnew, not a
     reimplementation. This runs under KiCad's *bundled* Python interpreter
     (which has the `pcbnew` module), not the Python running this script.
  2. Run FreeRouting headless (java -jar, no GUI) on the .dsn to produce a
     routed .ses session file.
  3. Import that .ses back into the board with KiCad's own
     pcbnew.ImportSpecctraSES and save.
  4. Add GND (In1.Cu) and +3V3 (In2.Cu) zone pours and fill them - done
     AFTER routing, not before, so they're purely additive copper on an
     already-100%-connected-by-traces board rather than something any net's
     connectivity actually depends on (see add_and_fill_zones' own comment
     for why doing this before routing backfired).

Requires:
  - tools/freerouting-2.2.4.jar (see README - Java + FreeRouting setup)
  - KiCad's bundled Python at KICAD_PYTHON below (ships with any KiCad
    install; distinct from the system Python used to run this file)

This does NOT run kicad-cli DRC itself - run run_drc.py afterward to verify
the routed result (clearance, unrouted-net count, etc).
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PCB = os.path.join(HERE, "Manifold.kicad_pcb")
DSN = os.path.join(HERE, "Manifold.dsn")
SES = os.path.join(HERE, "Manifold.ses")
FREEROUTING_JAR = os.path.join(HERE, "tools", "freerouting-2.2.4.jar")

KICAD_PYTHON = r"C:\Program Files\KiCad\10.0\bin\python.exe"
JAVA_CANDIDATES = [
    r"C:\Program Files\Eclipse Adoptium\jre-25.0.3.9-hotspot\bin\java.exe",
]

# Autorouter effort: cap passes so a bad/congested board fails fast instead of
# spinning forever, rather than trying to tune "good enough" up front.
# `-oit 0` disables FreeRouting's "stop early if the score hasn't improved
# much in the last 10 passes" behavior, so it keeps trying up to MAX_PASSES
# instead of settling for a plateau - cheap insurance on a small board like
# this one, and matters more now that no zones exist yet at routing time
# (see add_and_fill_zones below) - this is genuinely just ordinary trace
# routing, same regime that's reliably reached 0 unrouted before.
MAX_PASSES = 60
OPTIMIZATION_IMPROVEMENT_THRESHOLD = 0


def find_java():
    import shutil
    exe = shutil.which("java")
    if exe:
        return exe
    for candidate in JAVA_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    raise SystemExit("java not found - see README for the Java + FreeRouting setup")


def run_kicad_python(label, script):
    result = subprocess.run([KICAD_PYTHON, "-c", script], capture_output=True, text=True)
    print(result.stdout.strip())
    if result.returncode != 0:
        print(result.stderr.strip(), file=sys.stderr)
        raise SystemExit(f"{label} failed (exit {result.returncode})")


def export_dsn():
    if not os.path.isfile(KICAD_PYTHON):
        raise SystemExit(f"KiCad's bundled Python not found at {KICAD_PYTHON}")
    # repr() on every embedded path, not an f-string splice - Windows paths
    # contain sequences like "\Users" that a plain (non-raw) generated string
    # literal misreads as a unicode escape (\U...); repr() escapes correctly
    # no matter where the path lands in the generated script text.
    run_kicad_python("DSN export", f'''
import pcbnew
board = pcbnew.LoadBoard({PCB!r})
ok = pcbnew.ExportSpecctraDSN(board, {DSN!r})
print("DSN export:", "OK" if ok else "FAILED", "->", {DSN!r})
if not ok:
    raise SystemExit(1)
''')


def run_freerouting():
    java = find_java()
    if not os.path.isfile(FREEROUTING_JAR):
        raise SystemExit(f"FreeRouting jar not found at {FREEROUTING_JAR} - see README")
    cmd = [java, "-jar", FREEROUTING_JAR, "-de", DSN, "-do", SES,
           "-mp", str(MAX_PASSES), "-oit", str(OPTIMIZATION_IMPROVEMENT_THRESHOLD),
           "--gui.enabled=false"]
    print("Running:", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    # FreeRouting logs to stdout even on success - keep the tail, it's where
    # the final pass/route-completion summary shows up.
    print("\n".join(result.stdout.strip().splitlines()[-40:]))
    if result.returncode != 0:
        print(result.stderr.strip()[-2000:], file=sys.stderr)
        raise SystemExit(f"FreeRouting failed (exit {result.returncode})")
    if not os.path.isfile(SES):
        raise SystemExit("FreeRouting exited OK but did not produce a .ses file")


def import_ses():
    run_kicad_python("SES import", f'''
import pcbnew
board = pcbnew.LoadBoard({PCB!r})
ok = pcbnew.ImportSpecctraSES(board, {SES!r})
print("SES import:", "OK" if ok else "FAILED")
if not ok:
    raise SystemExit(1)
board.Save({PCB!r})
print("Saved routed board to", {PCB!r})
''')


ZONE_INSET_MM = 0.5   # clearance from Edge.Cuts


def add_and_fill_zones():
    # Deliberately done AFTER routing is complete, not before: an earlier
    # version added the GND/+3V3 zone OUTLINES in build_pcb.py, before
    # routing, so FreeRouting's DSN "plane" mechanism would treat In1.Cu/
    # In2.Cu as pre-claimed and route other signals around them. That
    # backfired two ways: (1) it made this already-tight board (0.5mm-pitch
    # LQFP64, ~0.5mm-pitch VQFN12) noticeably harder to fully autoroute -
    # a solid zone removes a WHOLE layer's normal "just drop a via here"
    # flexibility everywhere, not only where the plane's own net needs it;
    # (2) worse, FreeRouting would sometimes report "100% routed" while
    # `run_drc.py` still found real "missing connection" findings between a
    # pad and the plane - it had skipped drawing an explicit trace/via to
    # some pads, ASSUMING the (not-yet-actually-computed) plane fill would
    # cover them, but the REAL fill (computed afterward, accounting for
    # clearance to nearby copper) didn't always reach every such pad.
    #
    # Fix: route everything as ordinary traces first (same reliable, simple
    # regime that got 0 unconnected items before any zones existed), THEN
    # add the zones as purely ADDITIVE copper on top of an already-100%-
    # connected board. Any GND/+3V3 trace or via the zone happens to
    # overlap just becomes redundant (harmless) rather than being the ONLY
    # connection - so there's no longer any way for a pad to end up
    # "connected only via a plane that doesn't quite reach it."
    #
    # Solid (not thermal-relief) pad connections, same reasoning as before:
    # no spoke geometry to fail, and this board isn't hand-soldered at a
    # scale where thermal relief's easier-rework benefit matters more than
    # connection reliability.
    #
    # ONE ZONE PER SUBPROCESS CALL, not both in one script: filling two
    # zones on two different layers in the SAME pcbnew process reliably
    # SEGFAULTS on the subsequent board.Save() (confirmed directly - two
    # zones filled and saved together crashes every time, one zone at a
    # time with identical settings never does) - looks like a real
    # threading/state bug in this KiCad build's zone filler under
    # scripting, not something to route around by tweaking zone settings.
    # Two independent load-add-fill-save cycles, each in its own process,
    # sidesteps it entirely: the second cycle loads the file the first
    # cycle already saved (with its zone intact) and adds/fills only the
    # new one.
    for net_name, layer_name in [("GND", "In1_Cu"), ("+3V3", "In2_Cu")]:
        run_kicad_python(f"Add + fill {net_name} zone ({layer_name})", f'''
import pcbnew
board = pcbnew.LoadBoard({PCB!r})
bbox = board.GetBoardEdgesBoundingBox()
inset = pcbnew.FromMM({ZONE_INSET_MM})
x0, y0 = bbox.GetLeft() + inset, bbox.GetTop() + inset
x1, y1 = bbox.GetRight() - inset, bbox.GetBottom() - inset

net = board.FindNet({net_name!r})
if net is None:
    raise SystemExit(f"net {net_name!r} not found on board")
zone = pcbnew.ZONE(board)
zone.SetLayer(pcbnew.{layer_name})
zone.SetNet(net)
zone.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
zone.SetLocalClearance(pcbnew.FromMM(0.2))
zone.SetMinThickness(pcbnew.FromMM(0.2))
outline = pcbnew.SHAPE_POLY_SET()
outline.NewOutline()
for x, y in [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]:
    outline.Append(pcbnew.VECTOR2I(int(x), int(y)))
zone.SetOutline(outline)
board.Add(zone)

filler = pcbnew.ZONE_FILLER(board)
filler.Fill(board.Zones())
board.Save({PCB!r})
print("Added + filled", {net_name!r}, "zone on", {layer_name!r}, "- saved to", {PCB!r})
''')


if __name__ == "__main__":
    export_dsn()
    run_freerouting()
    import_ses()
    add_and_fill_zones()
    print("\nDone. Run: python run_drc.py")
