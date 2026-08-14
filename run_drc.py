"""
Runs kicad-cli's real DRC engine against a .kicad_pcb and prints a readable
report from the JSON it produces - build_pcb.py already does this inline as
its last step, but only prints per-type counts; this reads the same JSON
output and prints the actual offending items too, so you don't have to go
digging through the temp file by hand every time.

Usage:
    python run_drc.py                  # DRC's Manifold.kicad_pcb
    python run_drc.py path/to/other.kicad_pcb
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PCB = os.path.join(HERE, "Manifold.kicad_pcb")


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


def main():
    pcb_path = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PCB
    if not os.path.isfile(pcb_path):
        raise SystemExit(f"no such file: {pcb_path}")

    kicad_cli = find_kicad_cli()
    if not kicad_cli:
        raise SystemExit("kicad-cli not found")

    report_path = os.path.join(os.environ.get("TEMP", HERE), "manifold_pcb_drc.json")
    subprocess.run([kicad_cli, "pcb", "drc", "--format", "json",
                    "--output", report_path, "--exit-code-violations", pcb_path],
                   capture_output=True, text=True)

    drc = json.load(open(report_path, encoding="utf-8"))

    print(f"DRC report for {pcb_path}")
    print(f"  kicad-cli {drc.get('kicad_version', '?')}, run {drc.get('date', '?')}\n")

    # "unconnected_items" is its own top-level array in the JSON, separate
    # from "violations" below - on this project's boards that's *expected*
    # (they're unrouted netlist-correct placements, not routed designs), so
    # it's reported as a count only, not spelled out item by item.
    unconnected = drc.get("unconnected_items", [])
    print(f"Unconnected items: {len(unconnected)} "
          f"(expected on an unrouted board - every net still needs routing)\n")

    violations = drc.get("violations", [])
    if not violations:
        print("No other DRC violations.")
        return

    by_type = {}
    for v in violations:
        by_type.setdefault(v.get("type", "unknown"), []).append(v)

    print(f"Violations: {len(violations)} across {len(by_type)} type(s)\n")
    for vtype, items in sorted(by_type.items(), key=lambda kv: -len(kv[1])):
        severity = items[0].get("severity", "?")
        print(f"[{severity}] {vtype} x{len(items)}")
        for v in items:
            print(f"    {v.get('description', '')}")
            for item in v.get("items", []):
                pos = item.get("pos", {})
                print(f"      - {item.get('description', '')} "
                      f"@ ({pos.get('x', '?')}, {pos.get('y', '?')})")
        print()


if __name__ == "__main__":
    main()
