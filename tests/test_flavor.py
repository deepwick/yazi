import gzip
import os
import shutil
import struct
import tomllib
import zlib
from pathlib import Path

import pytest

from deepwick_spec.ansi import runs, style_of
from deepwick_spec.check_port import check_port
from deepwick_spec.palette import load_palette
from deepwick_spec.tui import capture, tmux_available

REPO = Path(__file__).resolve().parents[1]
PALETTE = load_palette()
C = PALETTE.colors


TREE_ROLES = {
    "src": "directory", "run.sh": "executable", "link": "symlink", "broken": "brokenSymlink",
    "pipe": "special", "bundle.tar.gz": "archive", "photo.png": "media", "notes.pdf": "document",
    "app.py": "source", "old.bak": "temp", "libfoo.a": "temp",
}


def tiny_png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))
    header = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"\x00\x00")) + chunk(b"IEND", b"")


def make_tree(root):
    root.mkdir(parents=True, exist_ok=True)
    (root / "src").mkdir()
    (root / "run.sh").write_text("#!/bin/sh\necho run\n")
    (root / "run.sh").chmod(0o755)
    (root / "app.py").write_text("print('hi')\n")
    (root / "link").symlink_to("app.py")
    (root / "broken").symlink_to("missing")
    os.mkfifo(root / "pipe")
    (root / "bundle.tar.gz").write_bytes(gzip.compress(b"deepwick"))
    (root / "photo.png").write_bytes(tiny_png())
    (root / "notes.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    (root / "old.bak").write_text("old\n")
    (root / "libfoo.a").write_bytes(b"!<arch>\n")
    return root


def assert_role(text, name, role):
    style = style_of(text, name)
    expected = PALETTE.files[role]
    assert style["fg"] == C[expected.color].lower(), (name, role, style)
    assert style["bold"] == ("bold" in expected.style), (name, role, style)


YAZI_ROLES = dict(TREE_ROLES)


def name_style(text, name):
    # The hovered entry is drawn as one run with its icon ("<icon> src"); the status bar repeats it later.
    for run, style in runs(text):
        if run.strip() == name or run.strip().endswith(" " + name):
            return style
    raise AssertionError(f"{name!r} not on screen")


def flavor():
    path = REPO / "flavor.toml"
    assert path.is_file(), "flavor.toml missing"
    return tomllib.loads(path.read_text(encoding="utf-8"))


def rule(**match):
    for r in flavor()["filetype"]["rules"]:
        if all(r.get(k) == v for k, v in match.items()):
            return r
    raise AssertionError(f"no filetype rule {match}")


def test_filetype_rules_follow_file_roles():
    assert rule(mime="image/*")["fg"] == C["magenta"]
    assert rule(mime="{audio,video}/*")["fg"] == C["magenta"]
    archive = [r for r in flavor()["filetype"]["rules"] if r.get("mime", "").startswith("application/{zip")]
    assert archive and archive[0]["fg"] == C["rose"]
    assert rule(mime="application/{pdf,doc,rtf}")["fg"] == C["orange"]
    directory = [r for r in flavor()["filetype"]["rules"] if r.get("url") == "*/" and "is" not in r]
    assert directory == [{"url": "*/", "fg": C["blue"], "bold": True}]


def test_special_kinds():
    rules = flavor()["filetype"]["rules"]
    by_kind = {r["is"]: r for r in rules if "is" in r and r.get("url") == "*"}
    assert by_kind["orphan"]["fg"] == C["red"]
    assert by_kind["link"]["fg"] == C["cyan"]
    assert by_kind["exec"]["fg"] == C["green"] and by_kind["exec"]["bold"] is True
    for kind in ("fifo", "sock", "block", "char"):
        assert by_kind[kind]["fg"] == C["gold"], kind


def test_ui_accents():
    f = flavor()
    assert f["mode"]["normal_main"] == {"fg": C["background"], "bg": C["blue"], "bold": True}
    assert f["indicator"]["current"] == {"bg": C["selection"]}
    assert f["status"]["perm_read"] == {"fg": C["yellow"]}
    assert f["status"]["perm_write"] == {"fg": C["red"]}
    assert f["status"]["perm_exec"] == {"fg": C["green"]}
    assert f["mgr"]["border_style"] == {"fg": C["overlay"]}


def test_tmtheme_is_the_bat_theme():
    bat = REPO.parent / "bat" / "Deepwick.tmTheme"
    if not bat.is_file():
        pytest.skip("bat clone not present")
    assert (REPO / "tmtheme.xml").read_bytes() == bat.read_bytes()


def test_preview_is_the_screenshot():
    assert (REPO / "preview.png").resolve() == (REPO / "screenshot.png").resolve()


@pytest.mark.skipif(not (tmux_available() and shutil.which("yazi")), reason="requires tmux and yazi")
def test_yazi_renders_file_roles(tmp_path):
    flavor_dir = tmp_path / "config" / "yazi" / "flavors" / "deepwick.yazi"
    flavor_dir.mkdir(parents=True)
    shutil.copy(REPO / "flavor.toml", flavor_dir)
    shutil.copy(REPO / "tmtheme.xml", flavor_dir)
    (tmp_path / "config" / "yazi" / "theme.toml").write_text('[flavor]\ndark = "deepwick"\n')
    env = {"HOME": str(tmp_path / "home")}
    for kind in ("CONFIG", "DATA", "STATE", "CACHE"):
        env[f"XDG_{kind}_HOME"] = str(tmp_path / kind.lower())
    tree = make_tree(tmp_path / "tree")
    text = capture(f"yazi {tree}", env, size=(140, 30), wait_for="notes.pdf", timeout=20)
    for name, role in YAZI_ROLES.items():
        style = name_style(text, name)
        expected = PALETTE.files[role]
        assert style["fg"] == C[expected.color].lower(), (name, role, style)
        assert style["bold"] == ("bold" in expected.style), (name, role, style)
    assert style_of(text, "NOR")["bg"] == C["blue"].lower()


def test_port_conventions():
    assert check_port(REPO, PALETTE.allowed_hex()) == []
