# run, audit and map

`context.py` captures command output, audits project instructions and locates source code. See [context.md](context.md) for examples and [helper.md](helper.md) for recording evidence.

**run.** Pass the executable and its arguments after `--`. Invoke a shell explicitly for shell scripts. Choose a fresh filename in an existing log directory. The command saves the original stdout and stderr and propagates the exit code. The summary counts recognized unittest, pytest and Rust successes and Ninja build steps. For long logs it includes line numbers, diagnostics and the tail. `10-24: waiting` denotes identical consecutive lines. Whole lines are compared before truncation. Logs up to 4 KiB are displayed in full if compression would hide details. REVIEW marks omitted details. Open the relevant part of the saved log before accepting a REVIEW, when collecting all errors, or when the cause is unclear. Confirm expected tests and outcomes, including skips and zero-test runs with exit code 0. [Read part of a log](helper.md)

**audit.** Run from the chat's working directory. The command reads the effective local Codex CLI configuration, compares discovered instruction files with the CLI preview and lists configured MCP servers and duplicate lines. Without preview support it labels the report as an estimate. It accounts for global instructions, the project root and nested folders, AGENTS.override.md precedence, fallback names and the project byte limit. Matches shorter than 24 characters are omitted. Review duplicates in context, since independent projects can need the same rule. App plugins and instructions injected by the chat are outside the audit.

**map.** Search UTF-8 text with any extension, including extensionless files. Results give file paths, line numbers and recognized declaration names. In Git the command uses tracked and untracked files and repository ignore rules. Outside Git it uses ripgrep's file list when available. The fallback walker skips hidden directories, symlinks, node_modules and __pycache__, without applying Git or ripgrep ignore files. Binary files and files over 1 MiB are excluded. Use the native application for structured documents.
## Build evidence and method definitions

For saved test results, run `context.py build-check evidence/validation-index.json --artifact dist/app.jar`. Alternatively, pass a known `--revision SHA256` or `--build-report build.json` containing `candidateSha256` and optionally `runtimeSha256`. When both hashes are present they must match. The report includes the SHA, passed, failed, skipped and unconfirmed runs, passed assertions, the first problem and the full report path.

The index takes a `revision` and a `runs` array. Each run needs its own `revision`, `label`, `exit` and positive evidence (`passes`, a `PASS` assertion or `status: passed`). Java archives support `releaseJar` and `jar`. Skips, missing exit codes and `errors` stay visible. Repeat `--require LABEL` for required tests. Exit code 0 means the checks for this revision passed, 1 indicates command or check failure, and 2 means incomplete or inconsistent evidence.

For repeated builds, run `context.py build-check --recipe check.json --require smoke`. Paths and commands are relative to the recipe directory. This example uses build and test scripts supplied by the project.

```json
{
  "artifact": "dist/app.jar",
  "build": ["python", "build.py", "{artifact}"],
  "test": ["python", "test.py", "--jar", "{artifact}", "--sha", "{revision}", "--report", "{index}"]
}
```

The test writes a fresh index to the supplied location. The command saves full build and test logs under `.loomharness/logs/` and compares the artifact SHA before and after verification.

For a text search start with `rg -n`. To read a complete Java method and its overloads, use `context.py symbol src/Catalog.java Catalog.lookup`. This parses only the named file and needs `tree-sitter==0.25.2` and `tree-sitter-java==0.23.5` in the chosen Python environment. For exact cross-file references use the project's language server or `find_referencing_symbols` in an existing Serena installation. [Serena](https://github.com/oraios/serena)
