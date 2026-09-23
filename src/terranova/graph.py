#
# Copyright 2023-2025 Elasticsearch B.V.
# Copyright 2026-present Adrien Mannocci
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
import os
from dataclasses import dataclass, field
from graphlib import CycleError, TopologicalSorter
from itertools import pairwise
from pathlib import Path
from typing import cast

from terranova.exceptions import CyclicImportError
from terranova.resources import ResourcesImport, ResourcesManifest


def normalize_rel_path(path: str) -> str:
    """Normalize a resource group relative path so it can be used as a graph key."""
    return os.path.normpath(path).replace(os.sep, "/")


# One topological "wave": rel_paths whose dependencies are all satisfied by
# earlier waves, so every member of a wave can safely run concurrently with
# the rest of it. `compute_waves()` returns a `list[Wave]`, in execution order.
type Wave = list[str]


@dataclass(frozen=True)
class DependencyGraph:
    """Represents dependencies between resource groups, derived from manifest `imports`."""

    nodes: set[str] = field(default_factory=set)
    # Maps a resource group's rel_path to the set of rel_paths it imports from.
    depends_on: dict[str, set[str]] = field(default_factory=dict)
    # Maps a (importer_node, source_node) edge to the `ResourcesImport` that created it.
    edge_imports: dict[tuple[str, str], ResourcesImport] = field(default_factory=dict)


def build_dependency_graph(
    paths: list[tuple[Path, str]], manifests: dict[str, ResourcesManifest]
) -> DependencyGraph:
    """
    Build a dependency graph from the `imports` section of every discovered manifest.

    An import whose source isn't part of `paths` is treated as an already-satisfied
    external dependency rather than an error: `paths` may be scoped to a subpath
    (e.g. `terranova plan some/subdir`), and a project in that scope can legitimately
    import from a resource group outside of it that was applied by an earlier,
    separate invocation - there's nothing to order it against in *this* run.

    An import whose source is the importing resource group itself (a self-import)
    is likewise not turned into an edge: a group is always applied before its own
    runbooks run, so there's nothing to order it against either. See
    `extract_import_vars()` for how such an import's value actually gets resolved.

    Args:
        paths: discovered resource dirs, as returned by `resource_dirs()`.
        manifests: manifests already parsed for each rel_path in `paths`.

    Returns:
        the dependency graph, containing only edges between resource groups
        that are both part of `paths`.
    """
    nodes = {normalize_rel_path(rel_path) for _, rel_path in paths}
    depends_on: dict[str, set[str]] = {node: set() for node in nodes}
    edge_imports: dict[tuple[str, str], ResourcesImport] = {}

    for _, rel_path in paths:
        node = normalize_rel_path(rel_path)
        manifest = manifests[rel_path]
        if not manifest.imports:
            continue
        for importer in manifest.imports:
            source = normalize_rel_path(importer.source)
            if source == node:
                # A self-import is satisfied by this group's own apply, not by
                # another group - skip it instead of adding a self-loop edge,
                # which `TopologicalSorter` would otherwise reject as a cycle.
                continue
            if source in nodes:
                depends_on[node].add(source)
                edge_imports[(node, source)] = importer

    return DependencyGraph(
        nodes=nodes, depends_on=depends_on, edge_imports=edge_imports
    )


def compute_waves(graph: DependencyGraph) -> list[Wave]:
    """
    Compute topological execution waves from a dependency graph.

    Every resource group in a wave has all of its dependencies satisfied by
    resource groups in previous waves, and can therefore run concurrently
    with the rest of its wave.

    Args:
        graph: the dependency graph.

    Returns:
        list of waves, each a list of rel_paths, in execution order.

    Raises:
        CyclicImportError: if the graph contains a cycle.
    """
    sorter = TopologicalSorter[str]()
    for node in graph.nodes:
        sorter.add(node, *graph.depends_on[node])

    try:
        sorter.prepare()
    except CycleError as err:
        cycle = list(reversed(cast("list[str]", err.args[1])))
        raise CyclicImportError(
            cycle, _describe_cycle_edges(cycle, graph.edge_imports)
        ) from err

    waves: list[Wave] = []
    while sorter.is_active():
        ready = sorted(sorter.get_ready())
        waves.append(ready)
        sorter.done(*ready)

    return waves


def _describe_cycle_edges(
    cycle: list[str], edge_imports: dict[tuple[str, str], ResourcesImport]
) -> list[str]:
    """Render each edge of a cycle as the `imports` entry that created it, where known."""
    edges: list[str] = []
    for node, source in pairwise(cycle):
        importer = edge_imports.get((node, source))
        if importer is None:
            continue
        if importer.target:
            edges.append(
                f"`{node}` imports `{importer.resource}` from `{source}` as `{importer.target}`"
            )
        else:
            edges.append(f"`{node}` imports `{importer.resource}` from `{source}`")
    return edges
