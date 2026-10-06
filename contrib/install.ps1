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
# Install terranova on Windows: extracts the release zip and adds it to the user PATH.
#
# Usage: install.ps1 [-Version <x.y.z>] [-Prefix <dir>]
#
# Note: release assets are not checksummed; provenance can be checked with
# `gh attestation verify <file> --repo techcode-io/terranova`.
#
# Terranova creates symbolic links for shared dependencies, which Windows only allows with
# Developer Mode enabled (Settings > System > For developers) or from an elevated shell.

[CmdletBinding()]
param(
    # Version to install (default: latest, or $env:TERRANOVA_VERSION)
    [string]$Version = $env:TERRANOVA_VERSION,
    # Install directory (default: %LOCALAPPDATA%\terranova)
    [string]$Prefix = (Join-Path $env:LOCALAPPDATA "terranova")
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Repo = "techcode-io/terranova"

function Say($Message) { Write-Host "==> $Message" }

switch ($env:PROCESSOR_ARCHITECTURE) {
    "AMD64" { $Arch = "amd64" }
    default { throw "unsupported architecture: $env:PROCESSOR_ARCHITECTURE" }
}

if (-not $Version) {
    Say "Resolving latest version"
    $Version = (Invoke-RestMethod "https://api.github.com/repos/$Repo/releases/latest").tag_name
}
$Version = $Version.TrimStart("v")
Say "Installing terranova $Version (windows/$Arch)"

$Asset = "terranova-$Version-windows-$Arch.zip"
$Tmp = Join-Path ([System.IO.Path]::GetTempPath()) ([System.Guid]::NewGuid().ToString())
New-Item -ItemType Directory -Path $Tmp | Out-Null
try {
    Say "Downloading $Asset"
    Invoke-WebRequest "https://github.com/$Repo/releases/download/$Version/$Asset" -OutFile (Join-Path $Tmp $Asset)

    Say "Extracting to $Prefix"
    if (Test-Path $Prefix) { Remove-Item -Recurse -Force $Prefix }
    Expand-Archive -Path (Join-Path $Tmp $Asset) -DestinationPath $Prefix
}
finally {
    Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue
}

$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (($UserPath -split ";") -notcontains $Prefix) {
    Say "Adding $Prefix to the user PATH (restart your shell to pick it up)"
    [Environment]::SetEnvironmentVariable("Path", "$UserPath;$Prefix", "User")
}

Say "Installed: $(& (Join-Path $Prefix 'terranova.exe') --version)"
