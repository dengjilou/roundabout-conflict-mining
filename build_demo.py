"""Build the standalone portfolio demo using invented trajectories and map."""
import argparse
from collections import defaultdict
import json
from math import cos, pi, sin
from pathlib import Path
from tempfile import TemporaryDirectory

from mine import ROOT, find_visits, mine, write_outputs

ZONE = {
    "name": "示意环岛 · 北侧入口",
    "x": 0, "y": 0, "radius": 2.5,
    "approach": {"lookback_s": 15, "gates": {
        "north_entry": [[-16, 14], [-8, 14]],
        "circulating": [[-16, -9], [-16, 0]],
    }},
}


def north_profile(time):
    # Integrate the piecewise-linear speed backwards from arrival at distance zero.
    breakpoints = [(-9, 5.5), (-7, 5.5), (-5, 2.5), (-2.5, 5.0), (0, 5.0)]
    if time >= 0:
        return 5 * time, 5.0
    distance = 0.0
    for (start, initial), (end, final) in reversed(list(zip(breakpoints, breakpoints[1:]))):
        if time >= start:
            speed = initial + (final - initial) * (time - start) / (end - start)
            return distance - (end - time) * (speed + final) / 2, speed
        distance -= (end - start) * (initial + final) / 2
    return distance + (time + 9) * 5.5, 5.5


def position(distance, from_entry):
    if not from_entry or distance >= 0:
        angle = distance / 28
        return 28 * sin(angle), 28 * cos(angle) - 28, -angle
    if distance < -6 * pi:
        return -12, 12 - distance - 6 * pi, -pi / 2
    angle = (distance + 6 * pi) / 12
    return -12 * cos(angle), 12 - 12 * sin(angle), angle - pi / 2


def demo_frames():
    # Three separate examples: short following, short merge, and longer merge with speed drop.
    arrivals = [(10.0, False, False), (12.2, False, False),
                (27.0, False, False), (29.5, True, False),
                (45.0, False, False), (48.2, True, True)]
    frames = defaultdict(dict)
    classes = {}
    for obj, (arrival, from_entry, slowing) in enumerate(arrivals, start=101):
        classes[obj] = "Car"
        for tick in range(round((arrival - 9) * 30), round((arrival + 7) * 30) + 1):
            time = tick / 30
            delta = time - arrival
            distance, speed = north_profile(delta) if slowing else (5 * delta, 5)
            frames[time][obj] = (*position(distance, from_entry), 4.4, 1.8, speed)
    return dict(sorted(frames.items())), classes


MAP = """<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">
<rect width="1280" height="720" fill="#172b2b"/>
<defs><pattern id="grid" width="44" height="44" patternUnits="userSpaceOnUse">
<path d="M44 0H0V44" fill="none" stroke="#304644" stroke-width="1"/></pattern></defs>
<rect width="1280" height="720" fill="url(#grid)"/>
<circle cx="640" cy="542" r="302" fill="#213a35" stroke="#8d9f95" stroke-width="2"/>
<path d="M520 0V142A120 120 0 0 0 640 262" fill="none" stroke="#839691" stroke-width="86"/>
<circle cx="640" cy="542" r="280" fill="none" stroke="#839691" stroke-width="86"/>
<path d="M520 0V142A120 120 0 0 0 640 262" fill="none" stroke="#34454a" stroke-width="81"/>
<circle cx="640" cy="542" r="280" fill="none" stroke="#34454a" stroke-width="81"/>
<path d="M520 0V142A120 120 0 0 0 640 262" fill="none" stroke="#9dabaa" stroke-width="2" stroke-dasharray="12 11"/>
<circle cx="640" cy="542" r="280" fill="none" stroke="#a1afad" stroke-opacity=".62" stroke-width="2" stroke-dasharray="15 12"/>
<circle cx="640" cy="542" r="212" fill="none" stroke="#364f48" stroke-width="2"/>
<circle cx="640" cy="542" r="184" fill="none" stroke="#364f48" stroke-width="2"/>
<g fill="#b6cfca" font-family="sans-serif" font-size="20">
<text x="43" y="54">SCHEMATIC ROUNDABOUT</text>
<text x="583" y="87">NORTH ENTRY</text>
<text x="734" y="210">CIRCULATING LANE</text>
</g>
<path d="M43 654v10m0-5h100m0-5v10" fill="none" stroke="#b6cfca" stroke-width="2"/>
<text x="43" y="693" fill="#b6cfca" font-family="sans-serif" font-size="19">10 m</text>
</svg>"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    rules = json.loads((ROOT / "zones.json").read_text(encoding="utf-8"))["rules"]
    frames, classes = demo_frames()
    visits = find_visits(frames, classes, ZONE, rules["max_frame_gap_s"])
    events = mine(frames, visits, rules)
    # Temporary inputs stay alongside generated artifacts on the selected drive.
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=args.destination.parent) as folder:
        background = Path(folder) / "schematic.svg"
        background.write_text(MAP, encoding="utf-8")
        background.with_suffix(".wld").write_text("0.1\n0\n0\n-0.1\n-64\n26.2\n", encoding="ascii")
        write_outputs(args.destination, background, "synthetic", ZONE, rules, frames,
                      classes, visits, events, False)


if __name__ == "__main__":
    main()
