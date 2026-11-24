"""Early compaction of the active model context. No provider SDK or tokenizer."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


DEFAULT_TRIGGER = 256_000


def integer(name, value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def summary_allowance(target):
    return min(4096, max(1, target // 4))


def evaluate(result, after):
    """Compare total tokens over the remaining ordinary calls, including one summary and recovery."""
    saved = max(0, result["used_tokens"] - after)
    expense = result["compaction_tokens"] + result["reread_tokens"]
    needed = expense // saved + 1 if saved else None
    result.update(estimated_next_input_tokens=after, saved_per_call=saved, break_even_calls=needed)
    pressure = result["used_tokens"] >= result["safety_tokens"]
    pinned_above_review = result["protected_tokens"] >= result["trigger_tokens"]
    ceiling = result["safety_tokens"] if pressure or pinned_above_review else result["trigger_tokens"]
    if not saved or after >= ceiling:
        result.update(action="preserve", reason="checkpoint_leaves_no_headroom")
    elif needed <= result["remaining_calls"]:
        result.update(action="compact", reason="saves_tokens")
    elif pressure:
        result.update(action="compact", reason="context_window_pressure")
    elif pinned_above_review:
        result.update(action="preserve", reason="protected_context_reaches_trigger")
    else:
        result.update(action="keep", reason="compaction_would_not_pay_back")


def budget(window_tokens=None, used_tokens=None, protected_tokens=0, trigger_cap=DEFAULT_TRIGGER,
           target_tokens=None, client=None, *, remaining_calls=2, compaction_tokens=None, reread_tokens=0):
    """Use the active model's window and current input, including cached tokens once."""
    if client is not None and not isinstance(client, str):
        raise ValueError("client must be a CLI name")
    integer("trigger_cap", trigger_cap, 2)
    integer("protected_tokens", protected_tokens)
    integer("remaining_calls", remaining_calls)
    integer("reread_tokens", reread_tokens)
    if compaction_tokens is not None:
        integer("compaction_tokens", compaction_tokens)
    if target_tokens is not None:
        integer("target_tokens", target_tokens, 1)
        if target_tokens >= trigger_cap:
            raise ValueError("target_tokens must be below trigger_cap")
    if used_tokens is not None:
        integer("used_tokens", used_tokens)
        if protected_tokens > used_tokens:
            raise ValueError("protected_tokens exceeds the active input")
    if window_tokens is None:
        return {"action": "measure", "missing": "window_tokens"}
    integer("window_tokens", window_tokens, 4)
    trigger = min(trigger_cap, window_tokens * 3 // 4)
    preferred = target_tokens
    if preferred is None or preferred >= trigger:
        preferred = max(1, trigger * 5 // 16)
    safety = max(trigger, window_tokens * 7 // 8)
    target = max(preferred, protected_tokens)
    allowance = summary_allowance(target)
    after = max(target, protected_tokens + allowance)
    result = {"trigger_tokens": trigger, "target_tokens": target, "safety_tokens": safety}
    if client and protected_tokens < trigger:
        # A fixed-setting CLI cannot reconsider on every call. Solve the same strict inequality
        # H * (used - after) > summary + rereads for the first profitable input size.
        native_trigger = safety
        if remaining_calls and compaction_tokens is not None:
            native_trigger = after + (compaction_tokens + reread_tokens) // remaining_calls + 1
        elif remaining_calls > 1:
            native_trigger = (remaining_calls * after + allowance + reread_tokens) // (remaining_calls - 1) + 1
        result["adapter"] = native_settings(client, window_tokens, min(safety, max(trigger, native_trigger)))
    if used_tokens is None:
        result.update(action="measure", missing="used_tokens")
    elif remaining_calls == 0:
        result.update(action="keep", reason="work_finished")
    elif used_tokens < trigger:
        result.update(action="keep", reason="below_review_threshold")
    else:
        cost = used_tokens + allowance if compaction_tokens is None else compaction_tokens
        result.update(used_tokens=used_tokens, protected_tokens=protected_tokens, remaining_calls=remaining_calls,
                      compaction_tokens=cost, reread_tokens=reread_tokens)
        evaluate(result, after)
    return result


def native_settings(client, window, trigger):
    """Settings for the host to apply. A native compactor controls its own output size."""
    if client == "codex":
        return {"kind": "native", "args": ["-c", f"model_auto_compact_token_limit={trigger}",
                                          "-c", 'model_auto_compact_token_limit_scope="total"']}
    if client == "claude" and 100_000 <= trigger <= 1_000_000:
        return {"kind": "native", "env": {"CLAUDE_CODE_AUTO_COMPACT_WINDOW": str(trigger)}}
    if client == "gemini":
        return {"kind": "native", "settings": {"model": {"compressionThreshold": trigger / window}}}
    return {"kind": "portable"}


def block_tokens(block):
    if not isinstance(block, dict) or not isinstance(block.get("messages"), list):
        raise ValueError("each block needs a messages array and a token count")
    if any(not isinstance(item, dict) for item in block["messages"]):
        raise ValueError("messages must contain native message objects")
    if "keep" in block and type(block["keep"]) is not bool:
        raise ValueError("keep must be a boolean")
    anchors = block.get("anchors", [])
    if not isinstance(anchors, list) or any(not isinstance(s, str) or not s.strip() for s in anchors):
        raise ValueError("anchors must be an array of nonempty strings")
    return integer("tokens", block.get("tokens"), 1 if block["messages"] else 0)


def select_turns(snapshot, *, existing_checkpoint=False):
    """Select only a contiguous prefix of completed, unprotected turn groups."""
    prefix, turns = snapshot["prefix"], snapshot["turns"]
    if not isinstance(turns, list):
        raise ValueError("turns must be an array of complete turn groups")
    fixed = block_tokens(prefix)
    counts = [block_tokens(turn) for turn in turns]
    used = fixed + sum(counts)
    boundary = next((i for i, turn in enumerate(turns) if turn.get("keep")), max(0, len(turns) - 1))
    protected = fixed + sum(counts[boundary:])
    result = budget(snapshot.get("window_tokens"), used, protected,
                    snapshot.get("trigger_cap", DEFAULT_TRIGGER), snapshot.get("target_tokens"),
                    remaining_calls=snapshot.get("remaining_calls", 2),
                    compaction_tokens=0 if existing_checkpoint else snapshot.get("compaction_tokens"),
                    reread_tokens=snapshot.get("reread_tokens", 0))
    result["used_tokens"] = used
    if "break_even_calls" not in result:
        return result

    # Leave space for the checkpoint while retaining as much original history as possible.
    target = result["target_tokens"]
    allowance = summary_allowance(target)
    remaining = used
    cut = 0
    while cut < boundary and remaining + allowance > target:
        remaining -= counts[cut]
        cut += 1
    if not cut or remaining >= used:
        return dict(result, action="preserve", reason="no_removable_turns")
    evaluate(result, remaining + allowance)
    if not existing_checkpoint and result["action"] != "compact":
        return result
    # Bind the checkpoint to the exact source and fixed instructions, including turn order.
    source = json.dumps([prefix, turns[:cut]], ensure_ascii=False, sort_keys=True,
                        separators=(",", ":"), allow_nan=False).encode("utf8")
    result.update(summarize_turns=cut, retained_tokens=remaining, source_sha256=hashlib.sha256(source).hexdigest())
    anchors = list(dict.fromkeys(s for turn in turns[:cut] for s in turn.get("anchors", [])))
    if anchors:
        for turn in turns[:cut]:
            for anchor in turn.get("anchors", []):
                if not any(anchor in text for text in string_values(turn["messages"])):
                    raise ValueError("an anchor is absent from its source turn")
        result["required_anchors"] = anchors
    return result


def plan(snapshot):
    return select_turns(snapshot)


def string_values(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, (list, dict)):
        for item in value.values() if isinstance(value, dict) else value:
            yield from string_values(item)


def apply_checkpoint(snapshot, checkpoint):
    """Produce a replacement input only after a matching, shorter checkpoint is available."""
    # The host has already paid for this checkpoint. Its actual size is now known.
    result = select_turns(snapshot, existing_checkpoint=True)
    if "source_sha256" not in result:
        return result
    if checkpoint.get("source_sha256") != result["source_sha256"]:
        raise ValueError("checkpoint and selected source differ; plan the current history again")
    count = block_tokens(checkpoint)
    if not checkpoint["messages"]:
        raise ValueError("a checkpoint needs a nonempty summary message")
    size = result["retained_tokens"] + count
    evaluate(result, size)
    if result["action"] != "compact":
        return result
    messages = list(snapshot["prefix"]["messages"])
    messages.extend(checkpoint["messages"])
    if anchors := result.get("required_anchors"):
        missing = [anchor for anchor in anchors if not any(anchor in text for text in string_values(messages))]
        if missing:
            return dict(result, action="preserve", reason="missing_anchors", missing_anchors=missing)
    for turn in snapshot["turns"][result["summarize_turns"]:]:
        messages.extend(turn["messages"])
    return dict(result, action="replace", replacement_tokens=size, messages=messages)


def read_object(path):
    obj = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(obj, dict):
        raise ValueError("expected a JSON object")
    return obj


def launch(client, window, trigger_cap, argv, **costs):
    settings = budget(window, trigger_cap=trigger_cap, client=client, **costs)["adapter"]
    if settings["kind"] != "native" or "settings" in settings:
        raise ValueError("this client needs project settings or a host adapter; use budget --client")
    executable = shutil.which(client)
    if not executable:
        raise ValueError(f"{client} is not on PATH")
    env = dict(os.environ, **settings.get("env", {}))
    command = [executable, *settings.get("args", []), *argv]
    if os.name == "posix":
        os.execve(executable, command, env)
    return subprocess.run(command, env=env).returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("budget", help="Decide from the current model's input usage.")
    check.add_argument("--window", type=int, dest="window_tokens")
    check.add_argument("--used", type=int, dest="used_tokens")
    check.add_argument("--protected", type=int, default=0, dest="protected_tokens")
    check.add_argument("--trigger", type=int, default=DEFAULT_TRIGGER, dest="trigger_cap")
    check.add_argument("--target", type=int, dest="target_tokens")
    check.add_argument("--client", help="Host CLI name. Unrecognized clients use the portable path.")
    start = commands.add_parser("launch", help="Start a native CLI with a per-session compaction threshold.")
    start.add_argument("--window", type=int, required=True)
    start.add_argument("--trigger", type=int, default=DEFAULT_TRIGGER, dest="trigger_cap")
    start.add_argument("client", choices=("codex", "claude"))
    start.add_argument("argv", nargs=argparse.REMAINDER)
    for command in (check, start):
        command.add_argument("--remaining-calls", type=int, default=2, help="Expected ordinary calls including the next.")
        command.add_argument("--compaction-tokens", type=int, help="Extra input and output to produce one checkpoint.")
        command.add_argument("--reread-tokens", type=int, default=0, help="Total extra tokens to recover omitted details.")
    commands.add_parser("plan", help="Select completed turns for a checkpoint.").add_argument("snapshot")
    compact = commands.add_parser("apply", help="Build the replacement model input on stdout.")
    compact.add_argument("snapshot")
    compact.add_argument("checkpoint")
    args = vars(parser.parse_args())
    command = args.pop("command")
    if command == "launch":
        return launch(**args)
    if command == "budget":
        result = budget(**args)
    else:
        snapshot = read_object(args["snapshot"])
        result = plan(snapshot) if command == "plan" else apply_checkpoint(snapshot, read_object(args["checkpoint"]))
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError) as exc:
        sys.exit(str(exc))
