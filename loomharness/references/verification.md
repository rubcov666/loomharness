# Project plans and repeat compilation

For short questions such as “What is next?” or “What is done?”, the hook reads up to five plans or handoffs from an enabled project. It tries HANDOFF.md, TASKS.md, PLAN.md, STATUS.md and SESSION.md, then text variants and supported localized filenames. Customize Markdown, text, CSV/TSV and state paths with an optional `.codex/harness-plan.json`.

```json
{
  "documents": ["HANDOFF.md", "docs/Plan.txt"],
  "state_files": ["reports/decisions.csv"],
  "counts": {
    "file": "reports/tasks.csv",
    "column": "status",
    "values": ["done", "in progress"]
  }
}
```

`documents` replaces the default list. The hook reads up to five local UTF-8 documents of up to 256 KiB each and one table up to 512 KiB. CSV defaults to comma and TSV to tab. For a regional CSV set `"delimiter": ";"` in `counts`. Require a header and consistent field counts. The summary gives total rows, exact category counts and an other count. Use the native application for live cloud data or formula results.

`state_files` lists additional paths that require an evidence read before an `apply_patch` edit. Specify exact project-relative paths with `/`. For cloud records and other tools, the agent follows the skill's verification rules.

## Compilation

Use the project's ordinary build and cache rules. For standalone Java sources, the skill helper takes a complete source list, ordered dependencies and the Java version. Set `JAVA_HOME` or `--jdk` to the JDK root. For example, in a Java 21 test client project, substitute the installed script and project paths in the following command.

```sh
python /path/to/loomharness/scripts/javac_cache.py --cache-dir .cache/javac --release 21 --jar lib/format.jar src/ReportExport.java
```

Use `python3` on macOS or Linux. Repeat `--jar` for each JAR. On success stdout gives the absolute class directory for the next classpath. Compilation uses UTF-8, disables annotation processors and excludes implicit sources. For modules, code generation, special flags or implicit dependencies, use the original build command.

The helper hashes sources, JARs and compiler components and verifies the cached class list and contents. Javac diagnostics go to stderr. Warnings cause recompilation on the next call. Successful compilation confirms the classes exist. Run the test against that directory and check the claimed user result. Unused cache directories can be removed.

For an existing build script, pass `--path-file` pointing to a fresh file for this run. After exit 0, read the UTF-8 path and use it in the classpath. Exit 65 indicates a compiler error. Fall back to the original compiler command for unavailable caches or unsupported inputs. This mode also checks JARs for embedded sources and annotation processors that need the project's usual javac settings.
