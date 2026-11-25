"""Repeat the speed-evidence comparison on the frozen OpenDD rdb5 cohorts."""
import argparse
from collections import Counter
import json
from pathlib import Path

from mine import ROOT, find_visits, mine, read_frames


def evaluate(database, zone, rules, start, stop):
    duration = 0
    counts = Counter()
    examples = {}
    for index in range(start, stop + 1):
        recording, frames, classes = read_frames(database, f"rdb5_{index}")
        duration += max(frames) - min(frames)
        visits = find_visits(frames, classes, zone, rules["max_frame_gap_s"])
        for event in mine(frames, visits, rules):
            counts["pairs"] += 1
            counts[event["path_label"]] += 1
            counts["time_hits"] += event["pet_hit"]
            counts["merge_time_hits"] += event["merging_time_hit"]
            counts["merge_review_hits"] += event["merge_review_hit"]
            counts["speed_hits"] += event["speed_review_hit"]
            counts["same_approach_speed_hits"] += (
                event["path_label"] == "same_approach" and event["speed_review_hit"]
            )
            if event["merge_review_hit"] and not event["merging_time_hit"]:
                examples.setdefault(recording, []).append(event["id"])
    fields = ("pairs", "same_approach", "entry_and_circulating", "unknown", "time_hits",
              "merge_time_hits", "merge_review_hits", "speed_hits", "same_approach_speed_hits")
    return {
        "recordings": stop - start + 1,
        "duration_min": round(duration / 60, 2),
        **{field: counts[field] for field in fields},
        "speed_only_merge_events": examples,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    config = json.loads((ROOT / "zones.json").read_text(encoding="utf-8"))
    rules = config["rules"]
    zone = config["sites"]["rdb5"]
    result = {
        "earlier_rdb5_343_362": evaluate(args.database, zone, rules, 343, 362),
        "heldout_rdb5_363_392": evaluate(args.database, zone, rules, 363, 392),
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
