# LoomHarness versioning

Use `1.X.Y` for the current release line. Keep the first component at `1`.

- For fixes, documentation and small improvements, increment `Y`. For example, `1.1.0` becomes `1.1.1`.
- For a substantial feature or workflow change, increment `X` and reset `Y` to zero. For example, `1.1.4` becomes `1.2.0`.

Use `loomharness/VERSION` as the source of truth. Update the version shown in `README.md` and `SIMPLE.md` in the same change. Run `python -X utf8 tests/record_release_hashes.py` to register the new installer hash, then run `python -X utf8 tests/verify_install.py --codex`.
