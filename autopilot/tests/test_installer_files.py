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
AUTOUPDATE = open(os.path.join(ROOT, "bin", "vac-autoupdate")).read()
CI = open(os.path.join(ROOT, ".github", "workflows", "ci.yml")).read()


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


def test_autoupdate_installs_the_ci_react_build():
    assert "releases/download/nova-build-$sha/nova-react-build.tar.gz" in AUTOUPDATE
    assert 'react_build "$tmp" "$sha"' in AUTOUPDATE
    assert "name: Publish installable React build" in CI
    assert "nova-react-build.tar.gz" in CI


def test_install_fetches_the_react_build_when_an_older_updater_left_it_out(tmp_path):
    """Incident 2026-11-17: the release with the React app was installed by the PREVIOUS vac-autoupdate, which had no
    react_build step, so /autopilot/react/ answered 'not built'. install.sh (always from the new release) must fetch
    the CI build for its own commit, and a release without frontend/ must not try."""
    import subprocess, tarfile
    sha = "a" * 40
    src = tmp_path / sha
    (src / "frontend").mkdir(parents=True)
    (src / "frontend" / "package.json").write_text("{}")
    srv = tmp_path / "gh" / "o" / "r" / "releases" / "download" / f"nova-build-{sha}"
    srv.mkdir(parents=True)
    build = tmp_path / "build"; (build / "assets").mkdir(parents=True)
    (build / "index.html").write_text('<script src="./assets/index-abc123.js"></script>')
    (build / "assets" / "index-abc123.js").write_text("console.log(1)")
    with tarfile.open(srv / "nova-react-build.tar.gz", "w:gz") as t:
        t.add(build, arcname=".")
    text = INSTALL if "INSTALL" in globals() else open(os.path.join(ROOT, "install.sh")).read()
    start = text.index("react_sha=$(basename"); end = text.index('cp -r "$SRC/autopilot/app/static/."')
    snippet = "set -e\n" + text[start:end]
    env = dict(os.environ, SRC=str(src), DEST=str(tmp_path / "dest"), AUTOUPDATE_GITHUB=f"file://{tmp_path}/gh",
               AUTOUPDATE_REPO="o/r")
    r = subprocess.run(["bash", "-c", snippet], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    react = src / "autopilot" / "app" / "static" / "react"
    assert (react / "index.html").is_file() and (react / "assets" / "index-abc123.js").is_file(), r.stdout
    # missing artifact: a warning, never a failed install (the classic dashboard must keep working)
    other = tmp_path / ("b" * 40); (other / "frontend").mkdir(parents=True); (other / "frontend" / "package.json").write_text("{}")
    r = subprocess.run(["bash", "-c", snippet], env=dict(env, SRC=str(other)), capture_output=True, text=True)
    assert r.returncode == 0 and "WARNING: no CI React build" in r.stdout
