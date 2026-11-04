from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import openai_package as pkg  # noqa: E402

SKILL = pkg.SKILL


@pytest.fixture(scope="module")
def archive(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("pkg") / "plugin.zip"
    path, _digest = pkg.build(out=out)
    return path


def names(path: Path) -> set[str]:
    with zipfile.ZipFile(path) as z:
        return set(z.namelist())


def test_the_archive_holds_the_manifest_the_skill_and_the_icon(archive: Path) -> None:  # AC2
    found = names(archive)
    assert {
        "plugin.json",
        f"{SKILL}/SKILL.md",
        "assets/icon.png",
        "LICENSE",
    } <= found
    with zipfile.ZipFile(archive) as z:
        manifest = json.loads(z.read("plugin.json"))
    assert manifest["version"] == (ROOT / "VERSION").read_text().strip()
    assert "skills" not in manifest  # the schema has no such key; skills/ is found by convention
    assert (ROOT / "skills").is_dir()


@pytest.mark.parametrize(
    "path",
    [
        "agents/contact-attendant.md",
        ".mcp.json",
        "mcp_config.json",
        ".claude-plugin/marketplace.json",
        ".claude-plugin/plugin.json",
        "latest.json",
        f"{SKILL}/references/harness-claude-code.md",
        f"{SKILL}/references/harness-claude-desktop.md",
        f"{SKILL}/references/harness-antigravity.md",
    ],
)
def test_what_belongs_to_another_runtime_stays_out(archive: Path, path: str) -> None:  # AC2
    assert path not in names(archive)


def test_nothing_from_the_development_tree_leaks(archive: Path) -> None:  # AC2
    for name in names(archive):
        assert not name.startswith(("tests/", "tools/", "evals/", ".venv/", "dist/")), name
        assert "__pycache__" not in name and not name.endswith(".pyc"), name


def test_no_markdown_names_another_vendor_and_every_link_resolves(archive: Path) -> None:  # AC3
    with zipfile.ZipFile(archive) as z:
        content = {n: z.read(n) for n in z.namelist()}
    assert pkg.problems(content, (ROOT / "VERSION").read_text().strip()) == []
    for name, data in content.items():
        if name.endswith(".md"):
            assert not pkg.FOREIGN.search(data.decode()), name


def test_the_manifest_has_no_hooks_apps_or_mcp_servers(archive: Path) -> None:  # AC4
    with zipfile.ZipFile(archive) as z:
        manifest = json.loads(z.read("plugin.json"))
    for key in pkg.FORBIDDEN_MANIFEST_KEYS:
        assert key not in manifest


def test_no_secret_shaped_text_is_shipped(archive: Path) -> None:  # AC2
    with zipfile.ZipFile(archive) as z:
        for name in z.namelist():
            if name.endswith((".md", ".json", ".py", ".txt")):
                assert not re.search(r"tn_live_[A-Za-z0-9]{16,}", z.read(name).decode()), name


def test_two_builds_are_byte_identical(tmp_path: Path) -> None:  # AC5
    first, digest_a = pkg.build(out=tmp_path / "a.zip")
    second, digest_b = pkg.build(out=tmp_path / "b.zip")
    assert digest_a == digest_b
    assert first.read_bytes() == second.read_bytes()


def test_the_update_table_keeps_only_the_codex_row(archive: Path) -> None:
    with zipfile.ZipFile(archive) as z:
        text = z.read(f"{SKILL}/references/update.md").decode()
    assert "| Codex |" in text
    for label in pkg.UPDATE_ROWS_TO_DROP:
        assert f"| {label} |" not in text


def test_a_changed_source_line_fails_the_build_and_names_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # AC6
    target = pkg.REPLACEMENTS[0]
    drifted = pkg.Replacement(target.path, "a sentence that is not in the file", "x")
    monkeypatch.setattr(pkg, "REPLACEMENTS", (drifted, *pkg.REPLACEMENTS[1:]))
    with pytest.raises(pkg.PackageError, match=re.escape(target.path)):
        pkg.build(out=tmp_path / "x.zip")


def test_a_manifest_with_hooks_is_refused() -> None:  # AC4
    content = pkg.members()
    manifest = json.loads(content["plugin.json"])
    manifest["hooks"] = {"PreToolUse": []}
    content["plugin.json"] = json.dumps(manifest).encode()
    found = pkg.problems(content, (ROOT / "VERSION").read_text().strip())
    assert any("hooks" in line for line in found)


VERSION = (ROOT / "VERSION").read_text().strip()


def listing_manifest(archive: Path) -> dict:
    with zipfile.ZipFile(archive) as z:
        return json.loads(z.read("plugin.json"))


def refused(mutate) -> list[str]:
    """Problems found after ``mutate`` changes the archive content (a dict of path to bytes)."""
    content = pkg.members()
    mutate(content)
    return pkg.problems(content, VERSION)


def change_manifest(change):
    def apply(content: dict[str, bytes]) -> None:
        manifest = json.loads(content["plugin.json"])
        change(manifest)
        content["plugin.json"] = json.dumps(manifest).encode()

    return apply


def test_the_archive_carries_the_one_mcp_server(archive: Path) -> None:  # AC9
    with zipfile.ZipFile(archive) as z:
        servers = json.loads(z.read("mcp.json"))["mcpServers"]
    assert list(servers) == ["turbo-notify"]
    assert servers["turbo-notify"]["url"] == "https://mcp.turbonotify.com"
    assert servers["turbo-notify"]["type"] == "streamable-http"
    assert "headers" not in servers["turbo-notify"]
    # The schema allows nothing else in a server entry: the portal found no server in a file
    # with an `extensions` block and no `$schema` (first draft upload, 2026-11-04).
    assert set(servers["turbo-notify"]) <= set(pkg.MCP_SERVER_KEYS)
    with zipfile.ZipFile(archive) as z:
        document = json.loads(z.read("mcp.json"))
    assert document["$schema"] == pkg.MCP_SCHEMA_URL
    assert set(document) == {"$schema", "mcpServers"}


def test_the_manifest_has_the_listing_the_review_and_the_publication(archive: Path) -> None:  # AC10
    block = listing_manifest(archive)["extensions"]["com.openai"]
    interface = block["interface"]
    assert interface["displayName"] == "Turbo Notify"
    assert interface["category"] == "Productivity"
    assert interface["composerIcon"] == interface["logo"] == "./assets/icon.png"
    cases = block["review"]["test_cases"]
    assert (len(cases["positive"]), len(cases["negative"])) == (5, 3)
    assert block["publication"]["countries"] == []  # worldwide: an empty list lifts the restriction
    assert pkg._png_size(zipfile.ZipFile(archive).read("assets/icon.png")) == (512, 512)


def test_the_author_is_the_developer_and_no_claude_style_key_is_left(archive: Path) -> None:  # AC11
    manifest = listing_manifest(archive)
    assert (
        manifest["author"]["name"]
        == manifest["extensions"]["com.openai"]["interface"]["developerName"]
    )
    assert manifest["$schema"] == pkg.SCHEMA_URL
    assert set(manifest) <= set(pkg.AGENT_PLUGIN_KEYS)
    for key in pkg.CLAUDE_STYLE_KEYS:
        assert key not in manifest
    text = json.dumps(manifest)
    for key in pkg.REVIEWER_KEYS:
        assert key not in text


def test_the_tools_the_cases_name_exist_in_the_skill_or_the_server_list(
    archive: Path,
) -> None:  # AC10
    block = listing_manifest(archive)["extensions"]["com.openai"]
    for case in block["review"]["test_cases"]["positive"]:
        for tool in case["tools_triggered"].split(","):
            assert re.fullmatch(r"[a-z]+(_[a-z]+)+", tool.strip()), tool


@pytest.mark.parametrize(
    ("label", "mutate", "needle"),
    [
        (
            "name over 30",
            lambda m: m["extensions"]["com.openai"]["interface"].update(displayName="x" * 31),
            "interface.displayName",
        ),
        (
            "http url",
            lambda m: m["extensions"]["com.openai"]["interface"].update(supportURL="http://x.test"),
            "interface.supportURL",
        ),
        (
            "four positive cases",
            lambda m: m["extensions"]["com.openai"]["review"]["test_cases"]["positive"].pop(),
            "5 positive",
        ),
        (
            "developer differs from author",
            lambda m: m["author"].update(name="Someone Else"),
            "developerName",
        ),
        (
            "reviewer credentials",
            lambda m: m["extensions"]["com.openai"]["review"].update(test_credentials={"a": "b"}),
            "test_credentials",
        ),
        (
            "dash in prose",
            lambda m: m["extensions"]["com.openai"]["interface"].update(
                shortDescription="WhatsApp — business"
            ),
            "dash",
        ),
        (
            "logo missing from the archive",
            lambda m: m["extensions"]["com.openai"]["interface"].update(logo="./assets/none.png"),
            "interface.logo",
        ),
    ],
)
def test_a_listing_that_breaks_a_limit_is_refused_naming_the_field(
    label, mutate, needle
) -> None:  # AC12
    found = refused(change_manifest(mutate))
    assert any(needle in line for line in found), (label, found)


def test_countries_are_worldwide_when_empty_and_must_be_clean_codes_otherwise() -> None:  # D9
    def with_countries(value):
        return change_manifest(
            lambda m: m["extensions"]["com.openai"]["publication"].update(countries=value)
        )

    assert not any("countries" in line for line in refused(with_countries([])))
    assert not any("countries" in line for line in refused(with_countries(["BR", "US"])))
    assert any("uppercase" in line for line in refused(with_countries(["br"])))
    assert any("must be a list" in line for line in refused(with_countries(None)))


def test_an_mcp_file_with_a_header_or_a_second_server_is_refused() -> None:  # AC12
    def with_header(content: dict[str, bytes]) -> None:
        content["mcp.json"] = json.dumps(
            {
                "mcpServers": {
                    "turbo-notify": {"url": "https://mcp.turbonotify.com", "headers": {"a": "b"}}
                }
            }
        ).encode()

    def with_two(content: dict[str, bytes]) -> None:
        server = {"url": "https://mcp.turbonotify.com"}
        content["mcp.json"] = json.dumps({"mcpServers": {"a": server, "b": server}}).encode()

    assert any("headers" in line for line in refused(with_header))
    assert any("exactly one server" in line for line in refused(with_two))


def test_the_demo_url_goes_in_only_when_given(tmp_path: Path) -> None:  # D10
    assert pkg.missing_notes(pkg.members())
    content = pkg.members(demo_url="https://example.test/demo")
    assert pkg.missing_notes(content) == []
    assert pkg.problems(content, VERSION) == []
    assert pkg.build(out=tmp_path / "d.zip", demo_url="https://example.test/demo")


def test_the_repository_manifest_keeps_its_keys_and_a_live_homepage() -> None:  # AC13, D8
    manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    for key in ("icon", "supportUrl", "privacyPolicyUrl"):
        assert key in manifest
    assert "extensions" not in manifest
    assert manifest["homepage"] == "https://docs.turbonotify.com/en/agents/skills/"


def test_the_helper_runs_from_an_empty_folder(archive: Path, tmp_path: Path) -> None:  # AC7
    empty = tmp_path / "clean"
    empty.mkdir()
    with zipfile.ZipFile(archive) as z:
        z.extractall(empty)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("TN_", "TURBO_"))}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-I", str(empty / SKILL / "scripts" / "tn_assist"), "--help"],
        cwd=empty,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert "ingest" in done.stdout
