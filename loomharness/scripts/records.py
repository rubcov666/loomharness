"""Filter and summarize local records with bounded evidence and source locations."""

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction
import json
from pathlib import Path
import re


FORMATS = {"json", "jsonl", "csv", "tsv", "log"}
DETAIL_BYTES = 8192
NUMERIC_TYPES = (int, float, Decimal)
SCALARS = (*NUMERIC_TYPES, str, bool, type(None))


def encode_decimal(value):
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"cannot encode {type(value).__name__}")


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False, default=encode_decimal)


def unique_keys(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate JSON keys")
    return result


def read_json(text):
    def invalid_constant(value):
        raise ValueError(f"invalid JSON number: {value}")

    return json.loads(text, object_pairs_hook=unique_keys, parse_constant=invalid_constant, parse_float=Decimal)


def read_rows(path, source_format, rows_key, pattern, skipped):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        if source_format == "json":
            data = read_json(stream.read())
            if rows_key is not None:
                if not isinstance(data, dict) or rows_key not in data:
                    raise ValueError("rows_key must name an array in the top-level object")
                data = data[rows_key]
            if not isinstance(data, list):
                raise ValueError("expected a JSON array or an object and rows_key")
            for index, record in enumerate(data):
                yield {"index": index}, record
        elif source_format in {"csv", "tsv"}:
            reader = csv.reader(stream, delimiter="," if source_format == "csv" else "\t", strict=True)
            header = next(reader, [])
            if not header or "" in header or len(header) != len(set(header)):
                raise ValueError("expected unique, nonempty column names")
            while True:
                start = reader.line_num + 1
                try:
                    cells = next(reader)
                except StopIteration:
                    break
                if not cells:
                    continue
                if len(cells) != len(header):
                    raise ValueError(f"line {start}: expected {len(header)} columns")
                yield {"line": start}, dict(zip(header, cells))
        else:
            for index, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    if source_format == "jsonl":
                        record = read_json(line)
                    else:
                        match = pattern.search(line)
                        if not match:
                            skipped["unmatched_lines"] += 1
                            continue
                        record = match.groupdict()
                except ValueError as error:
                    raise ValueError(f"line {index}: {error}") from error
                yield {"line": index}, record


def numeric(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ValueError("expected a finite number")
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError("expected a finite number") from error
    if not result.is_finite():
        raise ValueError("expected a finite number")
    return result


def instant(value):
    if not isinstance(value, str):
        raise ValueError("expected an ISO time with a zone")
    result = datetime.fromisoformat(value)
    if result.utcoffset() is None:
        raise ValueError("expected an ISO time with a zone")
    return result


def result_number(value):
    if value.denominator == 1:
        return value.numerator
    with localcontext() as context:
        context.prec = len(str(abs(value.numerator))) + len(str(value.denominator)) + 2
        return format(Decimal(value.numerator) / Decimal(value.denominator), "f")


def names(values, label):
    if not isinstance(values, (list, tuple)) or any(not isinstance(v, str) or not v for v in values):
        raise ValueError(f"{label} requires a list of field names")
    if len(values) != len(set(values)):
        raise ValueError(f"{label} contains duplicate fields")
    return values


def mapping(value, label):
    if not isinstance(value, dict) or any(not isinstance(k, str) or not k for k in value):
        raise ValueError(f"{label} requires an object keyed by field name")
    return value


def bound_checks(lower, upper):
    checks = []
    for edges, inclusive in ((mapping(lower, "gte"), True), (mapping(upper, "lt"), False)):
        for name, value in edges.items():
            convert = instant if isinstance(value, str) else numeric
            checks.append((name, convert(value), convert, inclusive))
    return checks


def numbers(record, fields, missing):
    values = {}
    for field in fields:
        try:
            values[field] = Fraction(numeric(record.get(field)))
        except ValueError:
            missing[field] += 1
    return values


def wire_size(value):
    return len(compact(value).encode("utf-8")) + 1


def scalar_key(value):
    if type(value) in NUMERIC_TYPES:
        return ("number", numeric(value))
    return (type(value).__name__, value)


def summarize(
    rows, *, where=None, gte=None, lt=None, fields=(), group=(), sums=(), limit=10, offset=0, group_limit=10
):
    """Summarize an iterable of (source location, record) pairs in source order."""
    if (
        type(limit) is not int
        or type(group_limit) is not int
        or not 0 <= limit <= 100
        or not 0 <= group_limit <= 100
    ):
        raise ValueError("limit and group_limit must be integers from 0 to 100")
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a nonnegative integer")
    where = mapping({} if where is None else where, "where")
    if any(not isinstance(v, SCALARS) for v in where.values()):
        raise ValueError("where values must be JSON scalars")
    expected = {key: scalar_key(value) for key, value in where.items()}
    checks = bound_checks({} if gte is None else gte, {} if lt is None else lt)
    fields, group, sums = names(fields, "fields"), names(group, "group"), names(sums, "sums")
    input_count = matched = invalid_range = missing_filter = 0
    total = defaultdict(Fraction)
    missing = Counter()
    buckets = {}
    evidence = []
    evidence_bytes = DETAIL_BYTES
    for location, record in rows:
        input_count += 1
        if not isinstance(record, dict):
            raise ValueError(f"expected a record object at {location}")
        if any(key not in record for key in where):
            missing_filter += 1
            continue
        if any(scalar_key(record[key]) != value for key, value in expected.items()):
            continue
        try:
            values = [
                (convert(record.get(key)), threshold, inclusive)
                for key, threshold, convert, inclusive in checks
            ]
        except (ValueError, OverflowError):
            invalid_range += 1
            continue
        if any(value < edge if inclusive else value >= edge for value, edge, inclusive in values):
            continue
        matched += 1
        additions = numbers(record, sums, missing)
        for key, value in additions.items():
            total[key] += value
        if group:
            keys = [record.get(name) for name in group]
            if any(not isinstance(key, SCALARS) for key in keys):
                raise ValueError(f"group fields need scalar values at {location}")
            label = tuple(map(scalar_key, keys))
            count, subtotal, keys = buckets.setdefault(label, (0, defaultdict(Fraction), keys))
            for key, value in additions.items():
                subtotal[key] += value
            buckets[label] = (count + 1, subtotal, keys)
        if offset < matched <= offset + limit:
            entry = {"at": location, "values": {name: record[name] for name in fields if name in record}}
            size = wire_size(entry)
            if size <= evidence_bytes:
                evidence.append(entry)
                evidence_bytes -= size

    groups = []
    group_bytes = DETAIL_BYTES
    for _, (count, subtotal, keys) in sorted(buckets.items(), key=lambda item: (-item[1][0], item[0]))[
        :group_limit
    ]:
        entry = {
            "values": keys,
            "count": count,
            "sums": {key: result_number(value) for key, value in subtotal.items()},
        }
        size = wire_size(entry)
        if size <= group_bytes:
            groups.append(entry)
            group_bytes -= size
    return {
        "records": input_count,
        "matched": matched,
        "missing_filter": missing_filter,
        "invalid_range": invalid_range,
        "sums": {key: result_number(value) for key, value in total.items()},
        "missing_sums": dict(missing),
        "groups": groups,
        "groups_omitted": len(buckets) - len(groups),
        "evidence": evidence,
        "evidence_omitted": matched - len(evidence),
        "next_offset": offset + limit if limit and offset + limit < matched else None,
    }


def inspect(path, *, source_format="auto", rows_key=None, pattern=None, query=None):
    if source_format == "auto":
        suffix = path.suffix.lower().lstrip(".")
        source_format = "jsonl" if suffix == "ndjson" else suffix if suffix in FORMATS else "log"
    if source_format not in FORMATS or (rows_key is not None and source_format != "json"):
        raise ValueError("unsupported format or rows_key outside JSON")
    if (source_format == "log") != (pattern is not None):
        raise ValueError("supply a named-capture pattern for log format")
    if pattern is not None:
        try:
            pattern = re.compile(pattern)
        except re.error as error:
            raise ValueError(f"invalid log pattern: {error}") from error
        if not pattern.groupindex:
            raise ValueError("expected at least one named capture (?P<field>...)")
    skipped = Counter()
    try:
        result = summarize(
            read_rows(path, source_format, rows_key, pattern, skipped),
            **({} if query is None else mapping(query, "query")),
        )
    except csv.Error as error:
        raise ValueError(f"invalid delimited file: {error}") from error
    return {
        "source": str(path),
        "format": source_format,
        **result,
        "unmatched_lines": skipped["unmatched_lines"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--format", choices=sorted(FORMATS | {"auto"}), default="auto")
    parser.add_argument("--rows-key", help="top-level JSON key that contains the record array")
    parser.add_argument("--pattern", help="regular expression with named captures for text logs")
    query = parser.add_mutually_exclusive_group()
    query.add_argument("--query", help="inline JSON query")
    query.add_argument("--spec", type=Path, help="JSON query file")
    args = parser.parse_args()
    try:
        spec = (
            read_json(args.spec.read_text(encoding="utf-8-sig")) if args.spec else read_json(args.query or "{}")
        )
        print(
            compact(
                inspect(
                    args.path,
                    source_format=args.format,
                    rows_key=args.rows_key,
                    pattern=args.pattern,
                    query=spec,
                )
            )
        )
    except (OSError, ValueError, TypeError, OverflowError) as error:
        parser.exit(1, f"Records: {error}\n")


if __name__ == "__main__":
    main()
