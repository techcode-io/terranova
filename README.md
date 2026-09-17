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

## 📖 Usage

### How to install on Linux

Releases ship `.deb` and `.rpm` packages that install `terranova` under `/opt/terranova`
and symlink it into `/usr/bin/terranova`.

```bash
# Debian/Ubuntu (amd64 or arm64)
gh release download --repo techcode-io/terranova -p '*_amd64.deb' -O terranova.deb
sudo dpkg -i terranova.deb

# Fedora/RHEL (amd64 or arm64)
gh release download --repo techcode-io/terranova -p '*.x86_64.rpm' -O terranova.rpm
sudo rpm -i terranova.rpm
```

### How to install on macOS

```bash
# For MacOSX Apple Silicon
gh release download --repo techcode-io/terranova -p '*-darwin-arm64.tar.gz' -O terranova.tar.gz

# For MacOSX Intel
gh release download --repo techcode-io/terranova -p '*-darwin-amd64.tar.gz' -O terranova.tar.gz

# Extract and install
mkdir -p /usr/local/opt/terranova
tar -C /usr/local/opt/terranova -xzf terranova.tar.gz
chmod +x /usr/local/opt/terranova/terranova
ln -sf /usr/local/opt/terranova/terranova /usr/local/bin/terranova
```

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
