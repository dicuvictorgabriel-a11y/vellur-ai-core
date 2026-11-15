"""The server installer must not stop on the repository layout.

Release d9b13d4 added docs/obsidian/ (screenshots). install.sh copied docs with `install -m 644 "$SRC/docs/"*`, which
fails on a directory; with `set -euo pipefail` the update aborted after 2 seconds and the server restored the previous
release. Every `install ... "$SRC/<dir>/"*` glob must therefore point at a folder without subfolders.
"""
import os
import re
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
INSTALL = open(os.path.join(ROOT, "install.sh")).read()


def test_install_globs_never_hit_a_directory():
    for line in INSTALL.splitlines():
        if not re.match(r"\s*install\b", line.split(";")[0]) and "&& install" not in line:
            continue
        for d, rest in re.findall(r'"\$SRC/([\w./-]+)/"(\*[\w.]*)?', line):
            if rest != "*":        # "*.py" and the like only match files
                continue
            folder = os.path.join(ROOT, d)
            subdirs = [e for e in os.listdir(folder) if os.path.isdir(os.path.join(folder, e))]
            assert not subdirs, f"install.sh copies {d}/* with `install`, but {d}/ has subfolders {subdirs}: {line.strip()}"


def test_docs_copy_skips_subfolders(tmp_path):
    line = next(x for x in INSTALL.splitlines() if '"$SRC/docs"' in x)
    dest = tmp_path / "dest"
    (dest / "docs").mkdir(parents=True)
    r = subprocess.run(["bash", "-euo", "pipefail", "-c", line], capture_output=True, text=True,
                       env={**os.environ, "SRC": ROOT, "DEST": str(dest)})
    assert r.returncode == 0, r.stderr
    copied = sorted(os.listdir(dest / "docs"))
    assert "OBSIDIAN.md" in copied and "obsidian" not in copied
