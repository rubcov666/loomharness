# Local checks

## Hook and SSH diagnostics

`harness.py status` shows the saved project choice. Run `harness.py doctor` in the working directory of the chat that should receive events. Its JSON includes host, path, connection type, the chat's own `session` and `projectEnabled`. `codexRegistry` records a fresh Codex `hooks/list` query for this directory, including `enabled` and `trustStatus` for each handler. An empty list means zero registrations. `unavailable` means the query failed. Select the app's binary with `--codex PATH`.

`userHookFile` shows declarations in the user's `hooks.json` even if Codex CLI is unavailable. `lastHook` is the last recorded event in UTC for this directory and session. `null` means none was observed. Startup, user prompts, stops and checked actions are recorded. Routine tools take a fast path. Records include only the event, client, time, working directory and session ID.

If Codex runs on macOS or Linux and the project runs on Windows, diagnostics can be run on each participating host. Restrict operations to the hosts authorized for the task. The local LoomHarness Windows SSH client needs OpenSSH with a verified host key and a remote `python`. Run it from the relevant chat to forward its session ID.

```sh
sshkit="$HOME/.agents/skills/loomharness/scripts/ssh_windows.py"
python3 -I -X utf8 "$sshkit" --project 'C:\Work\Project' win-host harness doctor
python3 -I -X utf8 "$sshkit" --project 'C:\Work\Project' win-host team intake
python3 -I -X utf8 "$sshkit" --project 'C:\Work\Project' --steps steps.json win-host
```

`steps.json` contains command arrays such as `[["team","intake"],["team","inbox","--watch"]]`. The first failure stops the queue. Stderr identifies the step and original code. Codes 1–255 propagate to the caller, larger Windows codes map to 1, and SSH connection errors normally return 255. `--ssh-config` selects an OpenSSH profile and `--scripts` selects an absolute Windows scripts directory. Arguments are passed literally.

## Reading evidence

Check a document, spreadsheet or service result in its native environment. For a local receipt, read a saved report or text export with its source, version and observed outcome. Add state files through `state_files` in the [project settings](verification.md).

On Windows run the helper in PowerShell from the project directory.

```powershell
$kit = Join-Path $env:USERPROFILE '.agents/skills/loomharness/scripts/harness.py'
python -I -X utf8 $kit enable
python -I -X utf8 $kit disable
python -I -X utf8 $kit status
python -I -X utf8 $kit read 'logs/verification.log' --find 'CHECK'
python -I -X utf8 $kit read 'logs/verification.log' --start 120 --lines 20
```

On macOS and Linux use Python 3.

```sh
kit="$HOME/.agents/skills/loomharness/scripts/harness.py"
python3 -I -X utf8 "$kit" enable
python3 -I -X utf8 "$kit" disable
python3 -I -X utf8 "$kit" status
python3 -I -X utf8 "$kit" read 'logs/verification.log' --find 'CHECK'
python3 -I -X utf8 "$kit" read 'logs/verification.log' --start 120 --lines 20
```

`enable` and `disable` save a per-project choice in the Codex profile. Git projects use the current worktree root. The legacy `.codex/harness.enabled` marker enables hooks until the user changes the choice. Answer the setup question in the chat's language or use `LoomHarness on` or `LoomHarness off` in any language. Existing explicit English and Russian command aliases remain supported.

## Hooks for other CLIs

The installer registers the same four handlers for each supported CLI. Use `python -X utf8 install.py --cli claude` for Claude Code, `--cli gemini` for Gemini CLI or `--cli all` for detected clients. The installer updates the respective user settings. For evidence reads, `--lines` defaults to 40, or 12 with `--find`. Literal search is case-sensitive and gives up to 12 numbered matches with a continuation. No match returns an error. Read nearby lines with `--start 120 --lines 20`. For PDFs, images and spreadsheets, use their viewer and attach the observed result. A summary or the state file itself cannot verify its own correctness.

The hook reads the requested read-review model from `read_review` in `$CODEX_HOME/loomharness/delegation.json`. Without an override it uses the default model.

## Self-update

`python harness.py update` compares the installation with the configured Git source and caches the result for one day. Configure it with `--set-origin URL [--branch main]`. Apply with `--apply` when boards have no active work. It checks compilation and makes recovery possible. `--rollback` restores the previous version. Git installations use `pull --ff-only`. Plain directories use a checked clone with backups under `$CODEX_HOME/loomharness/backups/`.

## Headless dispatch

By default, `dispatch` returns Codex app chat operations (`create_thread` or `send_message_to_thread`). `python team.py driver set claude|gemini|antigravity|opencode|dsh` selects a headless driver and returns a `run_command` for the coordinator's shell. `scripts/team_driver.py` assembles the prompt from the card, runs the CLI in the project and saves its full log to `.loomharness/runs/`. It prints a summary with token usage when the CLI supplies it in JSON. `driver set desktop` restores the app and `driver show` displays the choice. Discover models with `python team_driver.py models`. Save a routine model with `models --apply --routine NAME` to `.codex/harness-models.json`. Admission still uses blind trials.

## Persistent board process

For an active board, `python team.py serve` keeps one process. The port is in `$CODEX_HOME/loomharness/serve.port`. Stop it with `quit` on stdin or terminate the process. Call it with `python scripts/hk_call.py --project PATH COMMAND …` or `python team.py rpc`. Batch sessions benefit from reusing the process. One-off commands can use the direct CLI.

`harness.py update --force-check` bypasses the daily cache. Use `--apply` to install an available update and `--rollback` to restore the previous copy. Configure the source with `update --set-origin URL [--branch main]`.

`AGENT_STOP` in the project root stops activity and blocks new tool calls, including edits. Report status in chat and resume when the owner removes it. If the user declines hooks, proceed normally until they change their choice. Before editing `STATE.json`, `features.json`, `test-results.json` or a configured `state_files` path, read evidence in the same session using `read`. Session and stop hooks supply the remaining guidance.
