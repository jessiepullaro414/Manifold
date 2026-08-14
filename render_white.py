"""
One-off helper: kicad-cli's --background only offers default/transparent/
opaque (opaque defaults to a purple-blue gradient, not a plain studio
white) - render transparent (which keeps the floor shadow as real partial-
alpha pixels, confirmed by inspection) then composite onto solid white
ourselves for a clean product-shot look matching the ecu-pcb reference
images. Not part of the regular build pipeline - a presentation-image
utility only.
"""
import subprocess
import sys
from PIL import Image

KICAD_CLI = r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe"
PCB = "Manifold.kicad_pcb"


def render(output, extra_args):
    args = [KICAD_CLI, "pcb", "render", "--quality", "high", "--floor",
            "--width", "1920", "--height", "1440", "--background", "transparent",
            "-o", output, *extra_args, PCB]
    subprocess.run(args, check=True, capture_output=True)
    im = Image.open(output).convert("RGBA")
    bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
    bg.alpha_composite(im)
    bg.convert("RGB").save(output)
    print("wrote", output)


shots = {
    "manifold_hero.png": ["--perspective", "--rotate", "-35,0,-135", "--zoom", "0.85"],
    "manifold_angle2.png": ["--perspective", "--rotate", "-50,0,-150", "--zoom", "0.72"],
    "manifold_top.png": ["--side", "top", "--zoom", "0.85"],
    "manifold_back.png": ["--side", "bottom", "--zoom", "0.85"],
}

for name, args in shots.items():
    render(name, args)
