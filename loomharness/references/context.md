# Reading, comparing and executing

## Finding a tool

Call a known tool directly. Otherwise search the current environment's catalog. In Codex, search `ALL_TOOLS` locally and return just the names. Substitute task-specific words. Narrow the action or app name if more than eight names match.

```js
const words = ["list", "thread"];
const matches = value => words.every(word => value.toLowerCase().includes(word));
let hits = ALL_TOOLS.filter(tool => matches(tool.name));
if (!hits.length) hits = ALL_TOOLS.filter(tool => matches(tool.name + " " + tool.description));
text({total: hits.length, names: hits.slice(0, 8).map(tool => tool.name)});
```

Read the selected tool's full description, parameters and limits. The example uses one specific tool name. If there is no match, check the catalog, application name or CLI help.

```js
const name = "mcp__codex_app__list_threads";
const selected = ALL_TOOLS.find(tool => tool.name === name);
text(selected ?? {missing: name});
```

## Excerpts and versions

See [telemetry.md](telemetry.md) for time-window metrics and [commands.md](commands.md) for `run`, `audit` and `map`.

The enabled hook prints the `view.py` command for the current system. Append a file path and the options you need.

| Question | Arguments after the command | Result |
|---|---|---|
| Read a location identified in a code map or log | `service.py --start 120 --lines 8` | Numbered source lines and the next page. The MCP tool `harness_view` accepts `start` and `lines`. |
| Find a condition or exception in a document, export or log | `minutes.txt --find "Deadline change"` | Matching line numbers with two lines of context on each side. Continue with `--start`. Search is case-sensitive and reads UTF-8 and UTF-16/32 with a BOM. |
| Count log lines containing a marker | `build.log --find "state=retry" --count` | Exact count from `--start` to the end of the file. Multiple hits on one line count once. In MCP set `count_only: true` on `harness_find`. |
| Record the version of a verified file of any format | `slides.pptx --fingerprint` | SHA-256 of the full file and its size in bytes. |
| Request the same fields of a large report again | `report.json --fields id status --receipt new`, then replace `new` with the returned receipt | The first read includes the view and receipt. Matching path, bytes, options and representation return one `UNCHANGED` line. |
| Check whether a previously verified result can be reused | `slides.pptx --expect-sha256 HASH` | UNCHANGED with exit 0 on a match, CHANGED with exit 1 on a change. Read errors and invalid hashes return 2. |

A page contains up to 100 lines (default 20), bounded by 48 KiB of text. Lines over 440 characters show a prefix followed by `[…]`. Use `--find` to search inside a long line. Numbers refer to the original file, including JSON before tabular formatting.

A search with no matches exits 1. When layout, a formula or a current value matters, open the original document, sheet or record in its native tool. Use the object ID and version for cloud data. A local hash covers only the local bytes. Include other inputs, settings and source dates that affect the result.

Use the native tool for external actions. If the response is lost, check history or status using the same ID. For a local command, `context.py run --log` saves all output to a new file, propagates the exit code and rejects an existing filename before running. Read the saved output with `view.py --find`. Run interactive programs in their usual interface.

**Reuse a verified result.** If known local files and a processing method fully determine a result, save a receipt after checking the actual output. For example, for a document export using a custom template, substitute the path to the installed `context.py` in the following command.

```sh
python context.py reuse --record export.receipt --input brief.txt --input layout.xml --result report.pdf --method "Export v3, A4, English" --save
```

For the same request, repeat the command without `--save`. Exit 0 with UNCHANGED means all specified inputs, outputs, paths and the method match. Reuse the verified result. A nonzero exit requires checking again. Pass each input and output separately. Update the receipt after accepting a changed result. Account for versions of relevant network data, current dates, applications or fonts, or recheck the result. Confirm delivery, charges and other service actions with the service itself.

**Select a compact representation.** Use `view.py` from the same scripts directory as `context.py`. For example, run `python -I -X utf8 "$view" builds.json --fields id attempt status conclusion` or `python3 -I -X utf8 "$view" manifest.txt --base "$verified_commit"`, where the path variable points to `~/.agents/skills/loomharness/scripts/view.py`.

The script compares the source, compact JSON, a flat TOON table, selected fields and a diff using UTF-8 bytes and an estimate from words and punctuation. It favors the source for similar sizes. Field selection preserves row order and count, requested and unknown fields, free text and diagnostics. Only uniform metadata URLs, hashes and dates in recognized formats can be omitted. The header names omitted fields. Missing required fields select a complete representation. For arbitrary errors, exact URLs or dates, request the relevant fields or full content. Read the source directly for indentation, encoding or byte-level checks.

A diff needs the full hash of a previously verified commit and the same repository path. If the base is unavailable, the file is new or the patch is too large, the command uses the current file. Diffs include deleted lines. Malformed `.json`, duplicate keys and binary input produce an error.

Run commands in the project's working directory. On Windows set the path in PowerShell.

~~~powershell
$ctx = Join-Path $env:USERPROFILE '.agents/skills/loomharness/scripts/context.py'
python -I -X utf8 $ctx run --log check-1.log -- python -m unittest -v
python -I -X utf8 $ctx audit
python -I -X utf8 $ctx map discount --limit 6
~~~

On macOS and Linux use Python 3.

~~~sh
ctx="$HOME/.agents/skills/loomharness/scripts/context.py"
python3 -I -X utf8 "$ctx" run --log check-1.log -- python3 -m unittest -v
python3 -I -X utf8 "$ctx" audit
python3 -I -X utf8 "$ctx" map discount --limit 6
~~~

See [commands.md](commands.md) for `run`, `audit` and `map`.

## PowerShell summaries

`context.py run --log FILE -- PROGRAM ARGS` retains the original bytes. The summary collapses serialized PowerShell progress records and keeps adjacent text, errors, warnings and original line numbers. Unknown object types and malformed XML retain the original text.
