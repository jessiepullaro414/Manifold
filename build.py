#!/usr/bin/env python3
"""
build.py - real, top-level build orchestrator for Manifold, run from this
project's own root.

Runs the real, existing, already-proven pipeline in the order it actually
has to happen: schematic generation -> PCB placement -> autorouting -> DRC.
Same real scripts documented in README.md, just chained together - this
doesn't reimplement anything, it calls build_schematic.py/build_pcb.py/
route_board.py/run_drc.py exactly as you'd run them by hand.

Usage:
    python build.py                 # full pipeline
    python build.py --skip-route    # schematic+PCB+DRC only, skip the
                                     # real (slow, ~1-2 min) FreeRouting
                                     # autorouting pass
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def run_step(name, cmd):
    print(f"\n=== {name} ===")
    print("  $", " ".join(cmd))
    result = subprocess.run(cmd, cwd=HERE)
    if result.returncode != 0:
        print(f"FAILED: {name} (exit code {result.returncode})")
        return False
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-route", action="store_true",
                         help="Skip the real (slow) FreeRouting autorouting pass")
    args = parser.parse_args()

    ok = True
    ok = ok and run_step("Schematic generation", [sys.executable, "build_schematic.py"])
    ok = ok and run_step("PCB placement", [sys.executable, "build_pcb.py"])

    if not args.skip_route:
        # Real, slow step (FreeRouting) - the same real tool that actually
        # routed this board. Requires tools/freerouting-2.2.4.jar (not
        # committed - see README's "Setup" section) and a JRE on PATH.
        ok = ok and run_step("Autorouting (FreeRouting)", [sys.executable, "route_board.py"])
    else:
        print("\n=== Autorouting: SKIPPED (--skip-route) ===")

    ok = ok and run_step("DRC verification", [sys.executable, "run_drc.py"])

    print("\n" + "=" * 40)
    print("BUILD SUMMARY")
    print("=" * 40)
    print(f"  Manifold: {'OK' if ok else 'FAILED'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
