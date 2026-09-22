<h1 align="center">Terranova</h1>

<p align="center">
  <i align="center">Terranova is a thin wrapper for Terraform that provides extra tools and logic to handle Terraform configurations at scale.</i>
</p>

> **Note**: This is a fork of [elastic/terranova](https://github.com/elastic/terranova) maintained and developed by [techcode.io](https://techcode.io).<br>
> See the [original repository](https://github.com/elastic/terranova) for the upstream source.

<h4 align="center">
  <a href="https://github.com/techcode-io/terranova/actions/workflows/ci.yml">
    <img src="https://img.shields.io/github/actions/workflow/status/techcode-io/terranova/ci.yml?branch=main&label=ci&style=flat-square" alt="continuous integration" style="height: 20px;">
  </a>
  <a href="https://github.com/techcode-io/terranova/graphs/contributors">
    <img src="https://img.shields.io/github/contributors-anon/techcode-io/terranova?color=yellow&style=flat-square" alt="contributors" style="height: 20px;">
  </a>
  <a href="https://opensource.org/licenses/Apache-2.0">
    <img src="https://img.shields.io/badge/apache%202.0-blue.svg?style=flat-square&label=license" alt="license" style="height: 20px;">
  </a>
  <br>
</h4>

- [Source](https://github.com/techcode-io/terranova)
- [Documentation](https://github.com/techcode-io/terranova)
- [Issues](https://github.com/techcode-io/terranova/issues)
- [Contact](mailto:adrien.mannocci@gmail.com)

## :package: Prerequisites

- [uv](https://docs.astral.sh/uv/) for build system.
- [Podman](https://podman.io/docs) for container packaging.

## :sparkles: Features

- Ability to share terraform configuration without modules.
- Ability to define arbitrary resource layout.
- Ability to auto-generate documentation using metadata attached to resource definition.
- Ability to execute runbooks to interact with resources.
- Ability to import variables between resource group.
- Ability to run commands across resource groups in parallel, honoring dependency order.

## :dart: Motivation

- We needed a way to manage resources as code at scale.
- The solution should leverage terraform to avoid re-implementing the wheel.
- The solution shouldn't leverage the terraform configuration DSL to add features since it can change.

## :hammer: Workflow

### Setup

The following steps will ensure your project is cloned properly.

1. Clone repository:
   ```shell
   git clone https://github.com/techcode-io/terranova
   cd terranova
   ```
2. Install dependencies and setup environment:
   ```shell
   uv sync
   uv run poe env:configure
   ```

### Lint

- To lint you have to use the workflow.

```bash
uv run poe lint
```

- It will lint the project code using `pylint`.

### Format

- To format you have to use the workflow.

```bash
uv run poe fmt
```

- It will format the project code using `black` and `isort`.

### Claude Code sandbox

- To run [Claude Code](https://claude.com/claude-code) against this repository without exposing your machine, use the workflow.

```bash
uv run poe claude:sandbox
```

- It builds (or reuses) the `.devcontainer` image with `podman` or `docker` and starts Claude Code inside it with permission prompts skipped: the container is the boundary.
- The container engine is the first of `podman` and `docker` found on the `PATH`; set `CONTAINER_ENGINE` to force one.
- Outbound network access is restricted by an egress firewall to an allowlist of hosts (package registries, VCS, Anthropic services). IPv6 is disabled.
- The workspace is writable, but `.devcontainer`, `.claude`, `.git/hooks` and `.git/config` are mounted read-only, since they are executed on the host.
- Claude Code auth, the `uv` cache and the shell history persist across rebuilds in named volumes.
- If an IDE with the Claude Code plugin has the project open, its selection and diagnostics context is bridged into the sandbox through a host-side relay on a free loopback port picked at each launch. The IDE auth token never enters the container.
- The container is recreated automatically when `.devcontainer` changes.
- The same `.devcontainer` can also be opened directly from VS Code.

## 📖 Usage

### How to install (Linux and macOS)

```bash
curl -fsSL https://raw.githubusercontent.com/techcode-io/terranova/main/contrib/install.sh | sh

# Pin a version
curl -fsSL https://raw.githubusercontent.com/techcode-io/terranova/main/contrib/install.sh | sh -s -- --version 0.7.2
```

The script installs the `.deb`/`.rpm` package on Linux (under `/opt/terranova`, symlinked
into `/usr/bin/terranova`) and the tarball on macOS. See `contrib/install.sh --help` for
`--prefix` and `--bin-dir`.

### Shell completion

`terranova completion <bash|zsh|fish>` prints a completion script that completes commands,
options, resource group paths and runbook names (using `--conf-dir`/`TERRANOVA_CONF_DIR`).

The Linux `.deb`/`.rpm` packages install the scripts system-wide. Otherwise, enable it manually:

```bash
# bash (~/.bashrc)
eval "$(terranova completion bash)"

# zsh (~/.zshrc)
eval "$(terranova completion zsh)"

# fish
terranova completion fish > ~/.config/fish/completions/terranova.fish
```

The macOS tarball also ships pre-generated scripts in its `completions/` directory.

### Define an arbitrary resource layout

- `terranova` rely on the concept of resource groups.
- You can define as many resource groups as you want.
- The base layout should contain the directory `resources` and `shared`.
- The `resources` directory contains resource groups.
- The `shared` directory contains any sharable resource that will be symlink if defined as dependency.
- A resource group is defined when a `manifest.yml` is present.
- By default, `terranova` will look for a `conf` directory in the working directory that contain both above directories.

```
conf
├── resources
│   ├── resource_group_1
│   │   ├── runbooks
│   │   │   └── pyinfra.py
│   │   ├── main.tf
│   │   └── manifest.yml
│   └── resource_group_2
│       ├── main.tf
│       └── manifest.yml
└── shared
    ├── providers
    │   └── github.tf
    └── config.tf
```

- `terranova` will rely on the layout to apply change using terraform.
- In the above case, running `terranova apply resource_group_1` will run `terraform` on resources present in that directory.
- `terranova` supports any depth within the layout.
- This allows you to reflect any structure.

### How to get start

- Create a new directory in `conf/resources`.
- Create a new `manifest.yml` file with the following content.

```yaml
version: "1.2"

metadata:
  name: Terranova Hello World
  description: Hello World
  url: https://github.com/techcode-io/terranova
  contact: mailto:adrien.mannocci@gmail.com
```

- Define any resource using standard `terraform` configuration.
- Add metadata on each resource to allow auto-generate documentation.

```terraform
/*
@attr-name attr-value
*/
data "null_data_source" "values" {
  inputs = {}
}

/*
@attr-name attr-value
*/
resource "null_resource" "foobar" {}
```

- You can now run `terranova init <resource_group_name>` and `terranova apply <resource_group_name>`.

### How to define shared dependencies.

- In some case, we need to share common terraform configuration or scripts across many resource group.
- It's possible to define dependencies in the manifest and symlink them in any resource group.
- Those common resources should be defined in the `shared` directory.
- All symlink are maintained by `terranova` and are updated when the `terranova init` command is run.

```yaml
# Supported since 1.0 manifest version.
---
dependencies:
  - source: providers/github.tf # Which file or directory to symlink.
    target: 00-github-provider.tf # Where to symlink the file or directory.
```

### How to define runbook.

- In some case, we need to interact with terraform resources using specific tooling.
- It's possible to define a runbook in the manifest and invoke arbitrary tools.
- It's also possible to interact with `terranova` to extract information using [`outputs`](https://developer.hashicorp.com/terraform/language/values/outputs).

```yaml
# Supported since 1.1 manifest version.
---
runbooks:
  - name: "<runbook_name>" # Used as argument in the command
    entrypoint: "<tool_entrypoint>" # Tool to invoke
    workdir: "<working_directory>" # Optional: Used to navigate in sub-directories.
    args:
      - <arguments> # List of arguments to pass
    env:
      - name: PATH # Inherit environment value
      - name: FOO # Override or define environment value
        value: bar
```

### How to import variables across resource groups.

- In some case, we need to interact across many resource groups and need to import variables from a resource group to another one.
- It's possible to define imports in the manifest.

```yaml
# Supported since 1.2 manifest version.
---
imports:
  - from: "<resource_group_path>" # Relative resource group path
    import: "<output_variable>" # Name of the output variable to import
    as: "<working_directory>" # Optional: Name of the input variable to map to.
```

### How to pin the terraform/OpenTofu version.

- By default, `terraform` is looked up on the `PATH`.
- It's possible to pin the engine and its version per resource group in the manifest, either `terraform` or `opentofu`.
- An exact version is downloaded from the engine's official releases (HashiCorp's `releases.hashicorp.com` for `terraform`, OpenTofu's GitHub releases for `opentofu`), verified against the published SHA-256 checksums and cached in `~/.terranova/engines/<engine>/<version>/`.
- `latest` is looked up at most once every 24 hours (the answer is kept in `~/.terranova/engines/<engine>/.latest.json`), so runs stay consistent and keep working offline with a previously installed version. `terraform` uses HashiCorp's checkpoint API; `opentofu` uses GitHub's releases API, which is rate-limited for unauthenticated requests — the 24-hour cache keeps that lookup rare.
- With `plan` and `apply`, distinct versions are downloaded in parallel before any resource group runs.

```yaml
# Supported since 1.4 manifest version.
---
version: "1.4"
engine:
  name: terraform # `terraform` or `opentofu`.
  version: "1.9.5" # Exact version, `latest` for the newest stable one, or `system` to use the `PATH` lookup.
```

- Downloads are available for Linux, macOS and Windows (amd64 and arm64).
- Runbooks of that resource group also get the pinned binary first on their `PATH`. Without an `engine` block, or with `system`, they keep the system `PATH` (looking up `terraform` or `tofu` depending on `engine.name`).

### How to run commands across resource groups in parallel.

- By default, `terranova` runs with `--strategy sequential`: one resource group after another.
- `plan`, `apply`, `fmt` and `validate` also accept `--strategy parallel` to run independent resource
  groups concurrently instead.
- Use `--group-concurrency <n>` to cap how many resource groups run at once under `--strategy parallel`
  (defaults to a sane pool size if unset). This is distinct from `--parallelism`, which limits terraform's
  own resource-level concurrency inside a single invocation.
- Use `--fail-at-end` to let unaffected resource groups keep running after a failure instead of stopping
  the whole run immediately.

```bash
terranova plan --strategy parallel --group-concurrency 4
terranova apply --strategy parallel --auto-approve --fail-at-end
```

- For `plan` and `apply`, execution order still honors dependencies declared through manifest
  [`imports`](#how-to-import-variables-across-resource-groups): resource groups are grouped into
  topological "waves", where every group in a wave has all its dependencies satisfied by earlier waves.
  `--strategy parallel` runs a wave's groups concurrently and waits for the whole wave to finish before
  starting the next one; `--strategy sequential` runs the same waves flattened, one group at a time.
  A cyclic `imports` chain is rejected with an error before anything runs.
- `fmt` and `validate` don't resolve `imports`, so there's no dependency ordering to respect: every
  discovered resource group is treated as a single wave and is safe to run concurrently with every other.
- `apply --strategy parallel` requires `--auto-approve` (or applying a saved `.tnplan` file): running
  several `terraform apply` processes at once means none of them can fall back to an interactive
  approval prompt.

### How to scope a run to what changed.

- `plan`, `apply`, `destroy` and `docs` accept `--auto-scope` (`-A`) to only target the resource groups
  affected by your current git changes, instead of passing an explicit `path`.
- The changes considered are the working tree and staged changes compared to `HEAD`, plus untracked
  files (respecting `.gitignore`).
- Each changed file is mapped to its nearest ancestor directory containing a `manifest.yml`; a change
  outside any resource group is ignored, and several changes in one group select it only once.
- It can't be combined with an explicit `path` (or, for `apply`, a `.tnplan` file), and it must be run
  from inside a git repository.
- With `docs`, the docs directory isn't wiped first, so documentation of groups outside the scope is kept.

```bash
terranova plan --auto-scope
terranova apply -A --auto-approve
```

- To apply exactly what was planned, combine it with `plan --out`: the saved `.tnplan` file only contains
  the scoped resource groups, and `apply` takes its targets from that file. Don't pass `--auto-scope` to
  `apply` in that case, it can't be combined with a plan file.

```bash
terranova plan --auto-scope --out changes.tnplan
terranova apply changes.tnplan
```

### How to regenerate the documentation.

- Run the following command `terranova docs`.

### How to apply configuration changes.

- Run the following command `terranova apply <path>`.

### How to import a resource.

- Run the following command `terranova import <path> <resource_address> <identifier>`.
- The [`import`](https://developer.hashicorp.com/terraform/cli/import) terraform command is used under the hood.

## :heart: Contributing

If you find this project useful here's how you can help, please click the :eye: **Watch** button to avoid missing
notifications about new versions, and give it a :star2: **GitHub Star**!

You can also contribute by:

- Sending a [Pull Request](https://github.com/techcode-io/terranova/pulls) with your awesome new features and bug fixed.
- Be part of the community and help resolve [Issues](https://github.com/techcode-io/terranova/issues).

## 🧾 License

The `terranova` project is free and open-source software licensed under the Apache-2.0 license.
