# LoomHarness versioning

Use `1.X.Y` for the current release line. Keep the first component at `1`.

- For fixes, documentation and small improvements, increment `Y`. For example, `1.1.0` becomes `1.1.1`.
- For a substantial feature or workflow change, increment `X` and reset `Y` to zero. For example, `1.1.4` becomes `1.2.0`.

Use `loomharness/VERSION` as the source of truth. Update the version shown in `README.md` and `SIMPLE.md` in the same change. Run `python -X utf8 tests/record_release_hashes.py` to register the new installer hash.

## Verification

Run checks on the local computer with `python -X utf8 tests/verify_local.py`. It uses Python and Git, checks native Codex registration when the CLI is installed, and reports skipped Node.js or JDK checks. Keep hosted runners and model requests out of automatic verification. Use the saved benchmark counters for token and cost reports.

## Pull requests

As part of completing a requested LoomHarness change, merge your pull request into `main` after the relevant local checks pass. The owner has authorized routine merges in this repository. Reuse the temporary branch, merge the verified head, and confirm the result on GitHub.
