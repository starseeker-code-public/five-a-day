"""Throwaway: add a version's Recent Versions row + Version History block.

`make version x.y.z` already does pyproject, the README badge and uv.lock; this
does the two README sections it deliberately leaves to /update-readme. Deleted
once the renumber is done.

    python tools/_bump_readme.py 1.30.1 2026-09-19 "Short description" "Heading|bullet|bullet"
"""

import re
import sys
from pathlib import Path

version, date, short, *sections = sys.argv[1:]
anchor = "v" + version.replace(".", "")

p = Path("README.md")
s = p.read_text(encoding="utf-8")

# --- Recent Versions: insert at the top, keep exactly three rows ---
m = re.search(r"(\| Version \| Date \| Description \|\n\|[-| ]+\|\n)((?:\|.*\|\n){3})", s)
assert m, "Recent Versions table not found"
rows = m.group(2).rstrip("\n").split("\n")
rows = [r.replace("| **v", "| v", 1).replace("** |", " |", 1) for r in rows]
new_rows = [f"| **v{version}** | {date} | {short} |"] + rows[:2]
s = s[: m.start(2)] + "\n".join(new_rows) + "\n" + s[m.end(2) :]

# --- Version History: close the currently-open block, open the new one ---
s = re.sub(r'(<details id="v\d+") open>', r"\1>", s, count=1)
s = re.sub(r"(<summary><strong>v[\d.]+ [^<]*?) \(current\)(</strong></summary>)", r"\1\2", s, count=1)

body = []
for section in sections:
    parts = section.split("|")
    body.append(f"**{parts[0]}**\n")
    body += [f"- {b}" for b in parts[1:]]
    body.append("")

block = (
    f'<details id="{anchor}" open>\n'
    f"<summary><strong>v{version} — {short} (current)</strong></summary>\n\n"
    + "\n".join(body)
    + "\n</details>\n\n"
)
idx = s.index("<details id=", s.index("## Version History"))
s = s[:idx] + block + s[idx:]

p.write_text(s, encoding="utf-8")
print(f"README: v{version} row + history block added")
