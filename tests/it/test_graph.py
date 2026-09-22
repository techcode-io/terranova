from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest

from terranova.exceptions import CyclicImportError
from terranova.graph import DependencyGraph, build_dependency_graph, compute_waves
from terranova.resources import ResourcesManifest

_MANIFEST_NO_IMPORTS: Final[str] = """
version: "1.0"
metadata:
  name: test
  description: test
"""


def _manifest_with_imports(*sources: str) -> str:
    imports = "\n".join(
        f"  - from: {source}\n    import: some_output" for source in sources
    )
    return f"""version: "1.2"
metadata:
  name: test
  description: test
imports:
{imports}
"""


def _manifest_with_import_as(source: str, target: str) -> str:
    return f"""version: "1.2"
metadata:
  name: test
  description: test
imports:
  - from: {source}
    import: some_output
    as: {target}
"""


def _write_manifest_dir(base: Path, rel_path: str, content: str) -> tuple[Path, str]:
    resource_dir = base.joinpath(*rel_path.split("/"))
    resource_dir.mkdir(parents=True, exist_ok=True)
    (resource_dir / "manifest.yml").write_text(content)
    return resource_dir, rel_path


class TestComputeWaves:
    def test_independent_nodes_single_wave(self) -> None:
        graph = DependencyGraph(
            nodes={"a", "b", "c"},
            depends_on={"a": set(), "b": set(), "c": set()},
        )
        assert compute_waves(graph) == [["a", "b", "c"]]

    def test_linear_chain(self) -> None:
        graph = DependencyGraph(
            nodes={"a", "b", "c"},
            depends_on={"a": set(), "b": {"a"}, "c": {"b"}},
        )
        assert compute_waves(graph) == [["a"], ["b"], ["c"]]

    def test_diamond(self) -> None:
        graph = DependencyGraph(
            nodes={"a", "b", "c", "d"},
            depends_on={"a": set(), "b": {"a"}, "c": {"a"}, "d": {"b", "c"}},
        )
        assert compute_waves(graph) == [["a"], ["b", "c"], ["d"]]

    def test_self_cycle_raises(self) -> None:
        graph = DependencyGraph(nodes={"a"}, depends_on={"a": {"a"}})
        with pytest.raises(CyclicImportError):
            compute_waves(graph)

    def test_multi_node_cycle_raises(self) -> None:
        graph = DependencyGraph(nodes={"a", "b"}, depends_on={"a": {"b"}, "b": {"a"}})
        with pytest.raises(CyclicImportError):
            compute_waves(graph)

    def test_cycle_message_preserves_chain_order(self) -> None:
        """
        The cause should describe the actual cycle chain, not an alphabetically
        sorted node list - assert on the set of consecutive edges rather than a
        fixed string, since which node the cycle is reported as "starting" from
        isn't guaranteed deterministic (`TopologicalSorter` iterates a `set`).
        """
        graph = DependencyGraph(
            nodes={"a", "b", "c"},
            depends_on={"a": {"b"}, "b": {"c"}, "c": {"a"}},
        )
        with pytest.raises(CyclicImportError) as exc_info:
            compute_waves(graph)

        chain = exc_info.value.cause.rsplit(": ", 1)[-1].split(" -> ")
        assert chain[0] == chain[-1]
        edges = set(zip(chain, chain[1:]))
        assert edges <= {("a", "b"), ("b", "c"), ("c", "a")}
        assert len(edges) == 3

    def test_empty_graph_no_waves(self) -> None:
        graph = DependencyGraph(nodes=set(), depends_on={})
        assert compute_waves(graph) == []


class TestBuildDependencyGraph:
    def test_no_imports_all_independent(self, resources_dir: Path) -> None:
        dir_a, rel_a = _write_manifest_dir(
            resources_dir, "group_a", _MANIFEST_NO_IMPORTS
        )
        dir_b, rel_b = _write_manifest_dir(
            resources_dir, "group_b", _MANIFEST_NO_IMPORTS
        )
        paths = [(dir_a, rel_a), (dir_b, rel_b)]
        manifests = {
            rel_path: ResourcesManifest.from_file(full_path / "manifest.yml")
            for full_path, rel_path in paths
        }
        graph = build_dependency_graph(paths, manifests)
        assert graph.nodes == {"group_a", "group_b"}
        assert graph.depends_on == {"group_a": set(), "group_b": set()}
        assert compute_waves(graph) == [["group_a", "group_b"]]

    def test_chain_of_imports_produces_edges(self, resources_dir: Path) -> None:
        dir_producer, rel_producer = _write_manifest_dir(
            resources_dir, "producer", _MANIFEST_NO_IMPORTS
        )
        dir_consumer, rel_consumer = _write_manifest_dir(
            resources_dir,
            "consumer",
            _manifest_with_imports("producer"),
        )
        paths = [(dir_producer, rel_producer), (dir_consumer, rel_consumer)]
        manifests = {
            rel_path: ResourcesManifest.from_file(full_path / "manifest.yml")
            for full_path, rel_path in paths
        }
        graph = build_dependency_graph(paths, manifests)
        assert graph.depends_on["consumer"] == {"producer"}
        assert graph.depends_on["producer"] == set()
        assert compute_waves(graph) == [["producer"], ["consumer"]]

    def test_import_from_out_of_scope_source_creates_no_edge(
        self, resources_dir: Path
    ) -> None:
        """
        An import source that isn't part of the current `paths` (e.g. the
        invocation was scoped to a subpath, or points at a group applied by an
        earlier separate run) is treated as an already-satisfied external
        dependency, not an error - there's nothing to order it against here.
        """
        dir_consumer, rel_consumer = _write_manifest_dir(
            resources_dir,
            "consumer",
            _manifest_with_imports("does_not_exist"),
        )
        paths = [(dir_consumer, rel_consumer)]
        manifests = {
            rel_path: ResourcesManifest.from_file(full_path / "manifest.yml")
            for full_path, rel_path in paths
        }
        graph = build_dependency_graph(paths, manifests)
        assert graph.depends_on["consumer"] == set()
        assert compute_waves(graph) == [["consumer"]]


class TestCyclicImportDetails:
    def test_two_group_cycle_names_the_imports(self, resources_dir: Path) -> None:
        dir_a, rel_a = _write_manifest_dir(
            resources_dir, "group_a", _manifest_with_imports("group_b")
        )
        dir_b, rel_b = _write_manifest_dir(
            resources_dir, "group_b", _manifest_with_imports("group_a")
        )
        paths = [(dir_a, rel_a), (dir_b, rel_b)]
        manifests = {
            rel_path: ResourcesManifest.from_file(full_path / "manifest.yml")
            for full_path, rel_path in paths
        }
        graph = build_dependency_graph(paths, manifests)

        with pytest.raises(CyclicImportError) as exc_info:
            compute_waves(graph)

        resolution = exc_info.value.resolution
        assert resolution is not None
        assert "Remove or redirect" in resolution
        assert "`group_a` imports `some_output` from `group_b`" in resolution
        assert "`group_b` imports `some_output` from `group_a`" in resolution

    def test_three_group_cycle_names_the_imports(self, resources_dir: Path) -> None:
        dir_a, rel_a = _write_manifest_dir(
            resources_dir, "group_a", _manifest_with_imports("group_b")
        )
        dir_b, rel_b = _write_manifest_dir(
            resources_dir, "group_b", _manifest_with_imports("group_c")
        )
        dir_c, rel_c = _write_manifest_dir(
            resources_dir, "group_c", _manifest_with_import_as("group_a", "renamed")
        )
        paths = [(dir_a, rel_a), (dir_b, rel_b), (dir_c, rel_c)]
        manifests = {
            rel_path: ResourcesManifest.from_file(full_path / "manifest.yml")
            for full_path, rel_path in paths
        }
        graph = build_dependency_graph(paths, manifests)

        with pytest.raises(CyclicImportError) as exc_info:
            compute_waves(graph)

        resolution = exc_info.value.resolution
        assert resolution is not None
        assert "`group_a` imports `some_output` from `group_b`" in resolution
        assert "`group_b` imports `some_output` from `group_c`" in resolution
        assert (
            "`group_c` imports `some_output` from `group_a` as `renamed`" in resolution
        )
