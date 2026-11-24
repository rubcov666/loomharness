"""Record current file hashes in install.py PREVIOUS_RELEASES.

After changing any file listed in install.py FILES, run:
    python -X utf8 tests/record_release_hashes.py
verify_install.py refuses a release whose FILES hashes are not in the table.
"""

import hashlib
import re
from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = root / "install.py"
text = path.read_text(encoding="utf8")
files_match = re.search(r"FILES = \((.*?)\)", text, re.S)
names = re.findall(r'"([^"]+)"', files_match.group(1))
updated = []
for name in names:
    data = (root / "loomharness" / name).read_bytes().replace(b"\r\n", b"\n")
    digest = hashlib.sha256(data).hexdigest()
    key = f'"{name}": {{'
    if key in text:
        idx = text.index(key)
        entry = text[idx:text.index("},", idx) + 2]
        if digest not in entry:
            text = text.replace(key, key + f'"{digest}", ', 1)
            updated.append(name)
    else:
        dict_close = text.index("\n}", text.index("PREVIOUS_RELEASES = {"))
        text = (text[:dict_close] + f'\n    "{name}": {{"{digest}"}},' + text[dict_close:])
        updated.append(name + " (new entry)")
path.write_text(text, encoding="utf8", newline="\n")
print("recorded:", ", ".join(updated) if updated else "nothing new")
