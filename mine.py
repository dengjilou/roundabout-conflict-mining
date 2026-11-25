"""Mine auditable roundabout passage pairs from an OpenDD recording."""
import argparse
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import dataclass
from html import escape
import csv
import json
from math import hypot, isfinite, sqrt
from pathlib import Path
import re
import sqlite3

ROOT = Path(__file__).parent
VEHICLES = {"car", "van", "truck", "bus", "motorcycle", "heavy vehicle", "medium vehicle"}


@dataclass(frozen=True)
class Visit:
    obj: int
    enter: float
    leave: float
    approach: str | None = None
    gate_time: float | None = None


def read_frames(database, recording):
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        tables = [name for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ) if re.fullmatch(r"rdb\d+_\d+", name)]
        if recording is None and len(tables) == 1:
            recording = tables[0]
        if recording not in tables:
            raise ValueError("Select one recording with --recording: " + ", ".join(tables))
        rows = connection.execute(
            'SELECT timestamp,objid,utm_x,utm_y,utm_angle,length,width,class,v '
            f'FROM "{recording}" ORDER BY timestamp,objid'
        )
        frames = defaultdict(dict)
        classes = {}
        for time, obj, x, y, heading, length, width, label, speed in rows:
            frames[time][obj] = (x, y, heading, length, width, speed)
            classes[obj] = label
    return recording, dict(frames), classes


def inside(point, zone):
    return hypot(point[0] - zone["x"], point[1] - zone["y"]) <= zone["radius"]


def crossing_time(before, after, zone):
    t0, x0, y0 = before
    t1, x1, y1 = after
    offset_x, offset_y = x0 - zone["x"], y0 - zone["y"]
    dx, dy = x1 - x0, y1 - y0
    a = dx * dx + dy * dy
    b = 2 * (offset_x * dx + offset_y * dy)
    c = offset_x * offset_x + offset_y * offset_y - zone["radius"] ** 2
    root = sqrt(max(0, b * b - 4 * a * c))
    fraction = (-b + root if c <= 0 else -b - root) / (2 * a)
    return t0 + (t1 - t0) * min(1, max(0, fraction))


def gate_crossing(before, after, segment):
    (ax, ay), (bx, by) = segment
    t0, x0, y0 = before
    t1, x1, y1 = after
    gx, gy = bx - ax, by - ay
    side0 = gx * (y0 - ay) - gy * (x0 - ax)
    side1 = gx * (y1 - ay) - gy * (x1 - ax)
    if not side0 > 0 >= side1:
        return None
    fraction = side0 / (side0 - side1)
    x, y = x0 + fraction * (x1 - x0), y0 + fraction * (y1 - y0)
    projection = (x - ax) * gx + (y - ay) * gy
    if not 0 <= projection <= gx * gx + gy * gy:
        return None
    return t0 + fraction * (t1 - t0)


def find_visits(frames, classes, zone, max_gap):
    tracks = defaultdict(list)
    for time, frame in frames.items():
        for obj, point in frame.items():
            if classes[obj].lower() in VEHICLES:
                tracks[obj].append((time, point[0], point[1]))
    visits = []
    config = zone.get("approach", {})
    gates = config.get("gates", {})
    for obj, track in tracks.items():
        entered = None
        last_gate = None
        for before, after in zip(track, track[1:]):
            if after[0] - before[0] > max_gap:
                entered = None
                last_gate = None
                continue
            for name, segment in gates.items():
                time = gate_crossing(before, after, segment)
                if time is not None and (last_gate is None or time > last_gate[0]):
                    last_gate = (time, name)
            was_inside, is_inside = inside(before[1:], zone), inside(after[1:], zone)
            if is_inside and not was_inside:
                entered = crossing_time(before, after, zone)
                entry_gate = last_gate if last_gate and 0 <= entered - last_gate[0] <= config["lookback_s"] else None
            elif was_inside and not is_inside and entered is not None:
                leave = crossing_time(before, after, zone)
                gate_time, approach = entry_gate or (None, None)
                visits.append(Visit(obj, entered, leave, approach, gate_time))
                last_gate = None
                entered = None
    return sorted(visits, key=lambda visit: (visit.enter, visit.obj))


def speed_drop(samples, duration, max_gap):
    """Return the largest speed decrease over an exact interval of native data."""
    times = [time for time, _ in samples]
    best = None
    segment_start = 0
    for index, (time, speed) in enumerate(samples):
        if index and time - times[index - 1] > max_gap:
            segment_start = index
        # A gap is handled by comparing against ends in the same continuous segment.
        start = time - duration
        if start < times[segment_start]:
            continue
        left = bisect_right(times, start, lo=segment_start, hi=index) - 1
        t0, v0 = samples[left]
        t1, v1 = samples[left + 1]
        initial_speed = v0 + (v1 - v0) * (start - t0) / (t1 - t0)
        drop = max(0.0, initial_speed - speed)
        if best is None or drop > best["drop_mps"]:
            best = {"start_s": start, "end_s": time, "drop_mps": drop}
    return best


def add_speed_evidence(events, frames, rules):
    settings = rules["speed"]
    target_ids = {event[key] for event in events for key in ("first", "second")}
    tracks = defaultdict(list)
    for time, frame in frames.items():
        for obj, point in frame.items():
            speed = point[5]
            if obj in target_ids and speed is not None and isfinite(speed):
                tracks[obj].append((time, speed))
    track_times = {obj: [time for time, _ in points] for obj, points in tracks.items()}
    for event in events:
        chart = {}
        for key in ("first", "second"):
            obj = event[key]
            times = track_times.get(obj, [])
            gate_time = event[f"{key}_gate_s"]
            enter_time = event[f"{key}_enter"]
            origin = gate_time if gate_time is not None else enter_time
            start = bisect_left(times, origin - settings["pre_gate_s"])
            stop = bisect_right(times, enter_time)
            samples = tracks[obj][start:stop]
            best = speed_drop(samples, settings["drop_window_s"], rules["max_frame_gap_s"])
            event[f"{key}_speed_drop"] = best
            event[f"{key}_drop_mps"] = best["drop_mps"] if best else None
            chart_start = bisect_left(times, event["speed_chart_start_s"])
            chart_stop = bisect_right(times, event["speed_chart_end_s"])
            chart[key] = tracks[obj][chart_start:chart_stop]
        event["speed_review_hit"] = any(
            event[f"{key}_drop_mps"] is not None
            and event[f"{key}_drop_mps"] >= settings["review_drop_mps"]
            for key in ("first", "second")
        )
        event["merge_review_hit"] = event["path_label"] == "entry_and_circulating" and (
            event["merging_time_hit"] or event["speed_review_hit"]
        )
        event["speed_series"] = chart


def mine(frames, visits, rules):
    times = list(frames)
    events = []
    for index, first in enumerate(visits):
        end = bisect_right(visits, first.leave + rules["max_gap_s"], key=lambda v: v.enter)
        for second in visits[index + 1:end]:
            if first.obj == second.obj:
                continue
            margin = rules["visit_margin_s"]
            start = max(first.enter - margin, times[0])
            stop = min(max(first.leave, second.leave) + margin, times[-1])
            window = times[bisect_left(times, start):bisect_right(times, stop)]
            distances = []
            for time in window:
                frame = frames[time]
                if first.obj in frame and second.obj in frame:
                    a, b = frame[first.obj], frame[second.obj]
                    distances.append((hypot(a[0] - b[0], a[1] - b[1]), time))
            distance, nearest = min(distances, default=(None, None))
            gap = second.enter - first.leave
            proximity_hit = distance is not None and distance <= rules["max_distance_m"]
            pet_hit = 0 <= gap <= rules["pet_s"]
            if first.approach is None or second.approach is None:
                path_label = "unknown"
            elif first.approach == second.approach:
                path_label = "same_approach"
            else:
                path_label = "entry_and_circulating"
            events.append({
                "id": len(events) + 1,
                "first": first.obj, "second": second.obj,
                "first_enter": first.enter, "first_leave": first.leave,
                "second_enter": second.enter, "second_leave": second.leave,
                "first_approach": first.approach, "first_gate_s": first.gate_time,
                "second_approach": second.approach, "second_gate_s": second.gate_time,
                "path_label": path_label,
                "merging_time_hit": path_label == "entry_and_circulating" and pet_hit,
                "pet_s": gap if gap >= 0 else None,
                "overlap_s": min(first.leave, second.leave) - second.enter if gap < 0 else 0,
                "distance_m": distance, "nearest_s": nearest, "shared_frames": len(distances),
                "distance_hit": proximity_hit, "pet_hit": pet_hit,
                "status": "both" if proximity_hit and pet_hit else
                          "distance" if proximity_hit else "pet" if pet_hit else "neither",
            })
    if "speed" in rules:
        for event in events:
            gate_times = [event[f"{key}_gate_s"] for key in ("first", "second")]
            origin = min((time for time in gate_times if time is not None),
                         default=event["first_enter"])
            event["speed_chart_start_s"] = max(times[0], origin - rules["speed"]["pre_gate_s"])
            event["speed_chart_end_s"] = min(times[-1],
                max(event["first_leave"], event["second_leave"]) + rules["visit_margin_s"])
        add_speed_evidence(events, frames, rules)
    return events


def world_file(background):
    candidates = [background.with_suffix(suffix) for suffix in (".pgw", ".tfw", ".wld")]
    return next(path for path in candidates if path.is_file())


def write_outputs(destination, image, recording, zone, rules, frames, classes, visits, events, report_only):
    destination.mkdir(parents=True, exist_ok=True)
    counts = Counter(event["status"] for event in events)
    summary = {
        "recording": recording, "duration_s": max(frames) - min(frames),
        "objects": len(classes), "frames": len(frames), "passages": len(visits),
        "pairs": len(events), "overlap": sum(e["overlap_s"] > 0 for e in events),
        "without_shared_frames": sum(not e["shared_frames"] for e in events),
        "screen_counts": {key: counts[key] for key in ("both", "distance", "pet", "neither")},
        "path_counts": dict(Counter(event["path_label"] for event in events)),
        "merging_time_hits": sum(event["merging_time_hit"] for event in events),
        "merge_review_hits": sum(e.get("merge_review_hit", False) for e in events),
        "speed_review_hits": sum(e.get("speed_review_hit", False) for e in events),
        "zone": zone, "rules": rules,
    }
    payload = {"summary": summary, "events": events}
    (destination / "report.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with (destination / "review.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fields = ["id", "first", "second", "first_leave", "second_enter", "pet_s", "distance_m",
                  "overlap_s", "shared_frames", "status", "first_approach", "first_gate_s",
                  "second_approach", "second_gate_s", "path_label", "merging_time_hit",
                  "first_drop_mps", "second_drop_mps", "speed_review_hit", "merge_review_hit",
                  "review_label", "notes"]
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(events)
    print(json.dumps(summary, ensure_ascii=True))
    if report_only:
        return
    # Downsample only the display; the measurements above use each native observation.
    render_frames = []
    for time, frame in frames.items():
        if not render_frames or time - render_frames[-1][0] >= 0.098:
            render_frames.append([time, [[obj, *[round(number, 3) for number in point[:5]]]
                                        for obj, point in frame.items()]])
    payload.update(frames=render_frames, affine=list(map(float, world_file(image).read_text().split())))
    template = (ROOT / "replay.html").read_text(encoding="utf-8")
    import base64
    import mimetypes
    mime = mimetypes.guess_type(image.name)[0]
    backdrop = f"data:{mime};base64," + base64.b64encode(image.read_bytes()).decode("ascii")
    html = template.replace("__BACKDROP__", escape(backdrop, quote=True)).replace(
        "__REPLAY_DATA__", json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c"))
    (destination / "replay.html").write_text(html, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("background", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--recording")
    parser.add_argument("--report-only", action="store_true", help="Skip generating the HTML replay")
    args = parser.parse_args()
    recording, frames, classes = read_frames(args.database, args.recording)
    config = json.loads((ROOT / "zones.json").read_text(encoding="utf-8"))
    zone, rules = config["sites"][recording.split("_")[0]], config["rules"]
    visits = find_visits(frames, classes, zone, rules["max_frame_gap_s"])
    write_outputs(args.output, args.background, recording, zone, rules, frames, classes,
                  visits, mine(frames, visits, rules), args.report_only)


if __name__ == "__main__":
    main()
