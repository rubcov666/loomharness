"""Summarize one numeric time series over declared half-open observation windows."""

import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import sys


def number(value):
    if type(value) is int or (type(value) is float and math.isfinite(value)):
        return value
    raise ValueError("expected a finite numeric observation or threshold")


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("timestamps must be ISO 8601 strings with a UTC offset")
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("timestamp has no UTC offset")
    return result


def summarize(data, threshold, *, summary=False):
    threshold = number(threshold)
    if type(summary) is not bool:
        raise ValueError("summary must be a boolean")
    if not isinstance(data, dict) or set(data) != {"windows", "samples"}:
        raise ValueError("expected windows and samples")
    if not isinstance(data["windows"], list) or not data["windows"] or not isinstance(data["samples"], list):
        raise ValueError("declare at least one window and supply an array of samples")
    windows = []
    for row in data["windows"]:
        if not isinstance(row, dict) or set(row) not in ({"start","end"}, {"start","end","expected"}):
            raise ValueError("each window needs start, end and an optional expected count")
        start, end = timestamp(row["start"]), timestamp(row["end"])
        if start >= end or ("expected" in row and (type(row["expected"]) is not int or row["expected"] < 0)):
            raise ValueError("invalid window boundaries or expected count")
        windows.append((start, end, row))
    windows.sort(key=lambda w: w[0])
    if any(left[1] > right[0] for left, right in zip(windows, windows[1:])):
        raise ValueError("overlapping observation windows")
    observations = {}
    for row in data["samples"]:
        if not isinstance(row, dict) or set(row) != {"at","value"}:
            raise ValueError("each sample needs a timestamp and value")
        at = timestamp(row["at"])
        if at in observations:
            raise ValueError("duplicate sample timestamp")
        observations[at] = {"at":row["at"], "value":number(row["value"])}
    counts = [0] * len(windows)
    alerts, outside_alerts = [], []
    alert_count = outside_alert_count = 0
    index = outside = 0
    peak = None
    for at, row in sorted(observations.items()):
        while index < len(windows) and at >= windows[index][1]:
            index += 1
        if index == len(windows) or at < windows[index][0]:
            outside += 1
            if row["value"] >= threshold:
                outside_alert_count += 1
                if not summary:
                    outside_alerts.append(row)
            continue
        counts[index] += 1
        if row["value"] >= threshold:
            alert_count += 1
            if not summary:
                alerts.append(row)
        if peak is None or row["value"] > peak["value"]:
            peak = row
    coverage = []
    for (_, _, row), count in zip(windows, counts):
        if row.get("expected", count) != count:
            raise ValueError(f"sample count differs from declared coverage at {row['start']}")
        coverage.append({"start":row["start"], "end":row["end"], "count":count})
    result = {"coverage": coverage, "gaps": [w["start"] for w in coverage if not w["count"]],
              "peak": peak, "at_least": threshold, "outside_count": outside}
    if summary:
        result.update(alert_count=alert_count, outside_alert_count=outside_alert_count)
    else:
        result.update(alerts=alerts, outside_alerts=outside_alerts)
    return result


def unique_keys(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate JSON key")
    return result


def read_series(path):
    with path.open(encoding="utf-8-sig") as stream:
        return json.load(stream, object_pairs_hook=unique_keys)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path, help="normalized JSON series, or - for stdin")
    parser.add_argument("--at-least", type=json.loads, required=True)
    parser.add_argument("--summary", action="store_true", help="return alert counts, omitting individual samples")
    args = parser.parse_args()
    try:
        data = json.load(sys.stdin, object_pairs_hook=unique_keys) if str(args.file) == "-" else read_series(args.file)
        print(json.dumps(summarize(data, args.at_least, summary=args.summary),
                         ensure_ascii=False, separators=(",", ":")))
    except (OSError, ValueError) as error:
        sys.exit(str(error))
