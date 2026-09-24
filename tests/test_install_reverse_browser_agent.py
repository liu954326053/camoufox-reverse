"""Browser-free tests for the project-provided reverse browser skill installer."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "install_reverse_browser_agent.py"


class _MissingInstaller:
    def __getattr__(self, name):
        pytest.fail(f"installer is missing: {name}")


@pytest.fixture
def installer():
    if not SCRIPT.is_file():
        return _MissingInstaller()
    spec = importlib.util.spec_from_file_location("reverse_browser_agent_installer", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def skill_source(tmp_path: Path) -> Path:
    source = tmp_path / "source-skill"
    source.mkdir()
    (source / "SKILL.md").write_text("---\nname: test-skill\n---\n", encoding="utf-8")
    (source / "references").mkdir()
    (source / "references" / "usage.md").write_text("usage\n", encoding="utf-8")
    return source


def test_install_defaults_to_dry_run_and_does_not_create_target(
    installer, skill_source: Path, tmp_path: Path
):
    target = tmp_path / "codex-home" / "skills" / "camoufox-reverse-browser"

    result = installer.install_skill(skill_source, target)

    assert result["dry_run"] is True
    assert result["target"] == str(target)
    assert not target.exists()


def test_apply_copies_skill_without_touching_user_configuration(
    installer, skill_source: Path, tmp_path: Path
):
    target = tmp_path / "codex-home" / "skills" / "camoufox-reverse-browser"
    config = tmp_path / "codex-home" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text("existing = true\n", encoding="utf-8")

    result = installer.install_skill(skill_source, target, dry_run=False)

    assert result["dry_run"] is False
    assert (target / "SKILL.md").read_text(encoding="utf-8").startswith("---")
    assert (target / "references" / "usage.md").read_text(encoding="utf-8") == "usage\n"
    assert config.read_text(encoding="utf-8") == "existing = true\n"


def test_force_is_required_to_replace_an_existing_skill(
    installer, skill_source: Path, tmp_path: Path
):
    target = tmp_path / "target"
    installer.install_skill(skill_source, target, dry_run=False)
    (skill_source / "SKILL.md").write_text("updated\n", encoding="utf-8")

    with pytest.raises(installer.InstallError, match="already exists"):
        installer.install_skill(skill_source, target, dry_run=False)

    installer.install_skill(skill_source, target, dry_run=False, force=True)
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "updated\n"


def test_source_root_and_nested_symlinks_are_rejected(
    installer, skill_source: Path, tmp_path: Path
):
    source_link = tmp_path / "source-link"
    source_link.symlink_to(skill_source, target_is_directory=True)

    with pytest.raises(installer.InstallError, match="source.*symlink"):
        installer.install_skill(source_link, tmp_path / "target-a")

    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")
    (skill_source / "linked.txt").symlink_to(outside)

    with pytest.raises(installer.InstallError, match="source.*symlink"):
        installer.install_skill(skill_source, tmp_path / "target-b")


def test_target_symlink_and_symlinked_parent_are_rejected(
    installer, skill_source: Path, tmp_path: Path
):
    outside = tmp_path / "outside"
    outside.mkdir()
    target_link = tmp_path / "target-link"
    target_link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(installer.InstallError, match="target.*symlink"):
        installer.install_skill(skill_source, target_link, dry_run=False)

    parent_link = tmp_path / "skills-link"
    parent_link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(installer.InstallError, match="target.*symlink"):
        installer.install_skill(skill_source, parent_link / "target", dry_run=False)


def test_mcp_example_requires_project_dir_and_contains_no_proxy_credentials(installer):
    config = installer.build_mcp_config(
        "/absolute/project",
        "http://alice:super-secret@127.0.0.1:7890",
    )
    serialized = json.dumps(config, ensure_ascii=False)

    assert config["mcpServers"]["camoufox-reverse"]["args"][:2] == [
        "--project-dir",
        "/absolute/project",
    ]
    assert "--proxy" in config["mcpServers"]["camoufox-reverse"]["args"]
    assert "http://127.0.0.1:7890" in serialized
    assert "alice" not in serialized
    assert "super-secret" not in serialized

    with pytest.raises(installer.InstallError, match="project_dir"):
        installer.build_mcp_config("relative/project")


def test_mcp_example_dry_run_does_not_write_and_apply_writes_only_explicit_path(
    installer, tmp_path: Path
):
    example_path = tmp_path / "mcp-example.json"
    config = installer.build_mcp_config("/absolute/project")

    installer.write_mcp_example(example_path, config)
    assert not example_path.exists()

    installer.write_mcp_example(example_path, config, dry_run=False)
    assert json.loads(example_path.read_text(encoding="utf-8")) == config


def test_mcp_example_destination_symlink_is_rejected(installer, tmp_path: Path):
    outside = tmp_path / "outside.json"
    outside.write_text("{}\n", encoding="utf-8")
    example_link = tmp_path / "mcp-example.json"
    example_link.symlink_to(outside)

    with pytest.raises(installer.InstallError, match="example.*symlink"):
        installer.write_mcp_example(
            example_link,
            installer.build_mcp_config("/absolute/project"),
            dry_run=False,
        )
