import textwrap
from pathlib import Path

from click.testing import CliRunner

from terranova.cli import main
from tests import PROJECT_TESTS_FIXTURES_DIR
from tests.e2e.conftest import copy_as_git_repo

_MINIMAL_MANIFEST = """
version: "1.0"
metadata:
  name: {name}
  description: {name} group
"""


def test_docs_generates_markdown_for_each_resource_group(
    runner: CliRunner, tmp_path: Path
) -> None:
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "docs_resource_group"
    docs_dir = tmp_path / "docs"
    result = runner.invoke(
        main,
        args=[
            "--conf-dir",
            str(fixture_dir),
            "docs",
            "--docs-dir",
            str(docs_dir),
        ],
    )
    assert result.exit_code == 0
    doc_file = docs_dir / "team_a.md"
    assert doc_file.exists()


def test_docs_h3_uses_attrs_name_when_present(
    runner: CliRunner, tmp_path: Path
) -> None:
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "docs_resource_group"
    docs_dir = tmp_path / "docs"
    runner.invoke(
        main,
        args=["--conf-dir", str(fixture_dir), "docs", "--docs-dir", str(docs_dir)],
    )
    content = (docs_dir / "team_a.md").read_text()
    assert "### Named Bucket" in content


def test_docs_h3_falls_back_to_block_type_name_type_when_no_name_attr(
    runner: CliRunner, tmp_path: Path
) -> None:
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "docs_resource_group"
    docs_dir = tmp_path / "docs"
    runner.invoke(
        main,
        args=["--conf-dir", str(fixture_dir), "docs", "--docs-dir", str(docs_dir)],
    )
    content = (docs_dir / "team_a.md").read_text()
    assert "### data - unnamed - aws_ami" in content


def test_docs_multi_value_attr_rendered_as_bullet_list(
    runner: CliRunner, tmp_path: Path
) -> None:
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "docs_resource_group"
    docs_dir = tmp_path / "docs"
    runner.invoke(
        main,
        args=["--conf-dir", str(fixture_dir), "docs", "--docs-dir", str(docs_dir)],
    )
    content = (docs_dir / "team_a.md").read_text()
    assert "tag: alpha" in content
    assert "tag: beta" in content


def test_docs_rmtree_cleans_previous_docs_dir(
    runner: CliRunner, tmp_path: Path
) -> None:
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "docs_resource_group"
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    stale_file = docs_dir / "stale.md"
    stale_file.write_text("stale content")
    runner.invoke(
        main,
        args=["--conf-dir", str(fixture_dir), "docs", "--docs-dir", str(docs_dir)],
    )
    assert not stale_file.exists()


def test_docs_creates_nested_target_dirs(runner: CliRunner, tmp_path: Path) -> None:
    conf_dir = tmp_path / "conf"
    resource_dir = conf_dir / "resources" / "team" / "nested_group"
    resource_dir.mkdir(parents=True)
    (resource_dir / "manifest.yml").write_text(
        textwrap.dedent(
            """
            version: "1.0"
            metadata:
              name: Nested Group
              description: A nested resource group
            """
        )
    )
    docs_dir = tmp_path / "docs"
    result = runner.invoke(
        main,
        args=["--conf-dir", str(conf_dir), "docs", "--docs-dir", str(docs_dir)],
    )
    assert result.exit_code == 0
    assert (docs_dir / "team" / "nested_group.md").exists()


def test_docs_omits_url_and_contact_when_absent(
    runner: CliRunner, tmp_path: Path
) -> None:
    conf_dir = tmp_path / "conf"
    resource_dir = conf_dir / "resources" / "no_url_contact"
    resource_dir.mkdir(parents=True)
    (resource_dir / "manifest.yml").write_text(
        textwrap.dedent(
            """
            version: "1.0"
            metadata:
              name: No Url Contact
              description: A resource group without url/contact
            """
        )
    )
    docs_dir = tmp_path / "docs"
    result = runner.invoke(
        main,
        args=["--conf-dir", str(conf_dir), "docs", "--docs-dir", str(docs_dir)],
    )
    assert result.exit_code == 0
    content = (docs_dir / "no_url_contact.md").read_text()
    assert "[Source]" not in content
    assert "[Contact]" not in content


def test_docs_auto_scope_only_regenerates_changed_group_and_keeps_others(
    runner: CliRunner, tmp_path: Path
) -> None:
    conf_dir = tmp_path / "conf"
    resources_dir = conf_dir / "resources"
    for name in ("group_a", "group_b"):
        group_dir = resources_dir / name
        group_dir.mkdir(parents=True)
        (group_dir / "manifest.yml").write_text(
            textwrap.dedent(_MINIMAL_MANIFEST.format(name=name))
        )
    copy_as_git_repo(conf_dir, tmp_path / "conf_repo")
    conf_dir = tmp_path / "conf_repo"

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    stale_doc = docs_dir / "group_b.md"
    stale_doc.write_text("previously generated content")

    (conf_dir / "resources" / "group_a" / "manifest.yml").write_text(
        (conf_dir / "resources" / "group_a" / "manifest.yml").read_text()
        + "\n# changed\n"
    )

    result = runner.invoke(
        main,
        args=[
            "--conf-dir",
            str(conf_dir),
            "docs",
            "--docs-dir",
            str(docs_dir),
            "--auto-scope",
        ],
    )

    assert result.exit_code == 0
    assert (docs_dir / "group_a.md").exists()
    # A scoped run must not wipe docs for groups outside its scope.
    assert stale_doc.exists()


def test_docs_includes_url_and_contact_when_present(
    runner: CliRunner, tmp_path: Path
) -> None:
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "docs_resource_group"
    docs_dir = tmp_path / "docs"
    runner.invoke(
        main,
        args=["--conf-dir", str(fixture_dir), "docs", "--docs-dir", str(docs_dir)],
    )
    content = (docs_dir / "team_a.md").read_text()
    assert "[Source]" in content
    assert "[Contact]" in content
