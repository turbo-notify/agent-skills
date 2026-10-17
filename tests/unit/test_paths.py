from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from tn_assist.paths import (
    ContactKey,
    InvalidContactKey,
    InvalidWorkspaceId,
    PathEscapeError,
    Workspace,
)

ORG = "org_" + "a" * 32
NUM = "num_" + "b" * 32


def make(tmp_path: Path, org: str = ORG, num: str = NUM) -> Workspace:
    return Workspace.open(tmp_path, org, num, create=True)


def test_org_and_num_id_format(tmp_path: Path) -> None:
    for bad_org in ("", "org_", "org_" + "A" * 32, "org_" + "a" * 31, "x" * 36, "../" + ORG):
        with pytest.raises(InvalidWorkspaceId):
            Workspace.open(tmp_path, bad_org, NUM, create=True)
    for bad_num in ("", "num_" + "z" * 32, "num_" + "b" * 33, ORG):
        with pytest.raises(InvalidWorkspaceId):
            Workspace.open(tmp_path, ORG, bad_num, create=True)


def test_workspace_lives_under_org_and_num(tmp_path: Path) -> None:
    ws = make(tmp_path)
    assert ws.root == tmp_path.resolve() / ORG / NUM
    assert ws.root.is_dir()


def test_modes_are_private(tmp_path: Path) -> None:
    ws = make(tmp_path)
    assert stat.S_IMODE(ws.root.stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / ORG).stat().st_mode) == 0o700


def test_open_without_create_requires_existing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        Workspace.open(tmp_path, ORG, NUM, create=False)


def test_path_inside_is_allowed(tmp_path: Path) -> None:
    ws = make(tmp_path)
    assert ws.path("contacts", "x", "card.md") == ws.root / "contacts" / "x" / "card.md"
    assert ws.path() == ws.root


@pytest.mark.parametrize(
    "parts",
    [
        ("..", "x"),
        ("contacts", "..", "..", "x"),
        ("/etc/passwd",),
        ("a\x00b",),
        ("..",),
    ],
)
def test_path_rejects_traversal(tmp_path: Path, parts: tuple[str, ...]) -> None:
    ws = make(tmp_path)
    with pytest.raises(PathEscapeError):
        ws.path(*parts)


def test_path_rejects_symlink_pointing_outside(tmp_path: Path) -> None:
    ws = make(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, ws.root / "link")
    with pytest.raises(PathEscapeError):
        ws.path("link", "secret.txt")


def test_path_rejects_sibling_with_common_string_prefix(tmp_path: Path) -> None:
    ws = make(tmp_path)
    sibling = ws.root.parent / (ws.root.name + "-evil")
    sibling.mkdir()
    os.symlink(sibling, ws.root / "link")
    with pytest.raises(PathEscapeError):
        ws.path("link", "x")


def test_contact_key_normalizes_spellings() -> None:
    forms = ["+55 (11) 97777-6666", "5511977776666", "55-11-97777-6666", " +5511977776666 "]
    assert {ContactKey.parse(f) for f in forms} == {ContactKey("5511977776666")}


def test_brazilian_twelve_digit_mobile_gets_the_ninth_digit() -> None:
    assert ContactKey.parse("551177776666") == ContactKey("5511977776666")


def test_brazilian_twelve_digit_landline_is_kept() -> None:
    assert ContactKey.parse("551132221111") == ContactKey("551132221111")


@pytest.mark.parametrize(
    "raw", ["", "123", "55119777766a", "1" * 16, "+", "../../etc", "5511 97777 66 66 x"]
)
def test_contact_key_rejects_garbage(raw: str) -> None:
    with pytest.raises(InvalidContactKey):
        ContactKey.parse(raw)


def test_contact_dir_is_inside_workspace(tmp_path: Path) -> None:
    ws = make(tmp_path)
    key = ContactKey.parse("5511977776666")
    assert ws.contact_dir(key) == ws.root / "contacts" / "5511977776666"


def test_foreign_ids_fail_closed(tmp_path: Path) -> None:
    a = make(tmp_path)
    b = make(tmp_path, org="org_" + "c" * 32, num="num_" + "d" * 32)
    (a.root / "secret.txt").write_text("a")
    rel_to_a = os.path.relpath(a.root / "secret.txt", b.root)
    with pytest.raises(PathEscapeError):
        b.path(rel_to_a)


def test_contact_key_maps_every_unicode_digit_to_ascii() -> None:  # security review
    arabic = "".join(chr(0x0660 + int(d)) for d in "5511966665555")
    full_width = "".join(chr(0xFF10 + int(d)) for d in "5511966665555")
    assert ContactKey.parse(arabic) == ContactKey("5511966665555")
    assert ContactKey.parse(full_width) == ContactKey("5511966665555")
