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
"""Marshmallow schemas used to validate resources manifests, one per supported version."""

from types import MappingProxyType

from marshmallow import EXCLUDE, Schema, fields, validate


class _BaseSchema(Schema):
    """Base schema allowing unknown fields, matching the manifest's historically permissive shape."""

    class Meta:
        unknown: str = EXCLUDE


class MetadataSchema(_BaseSchema):
    """Validates a manifest's `metadata` block."""

    name: fields.String = fields.String()
    description: fields.String = fields.String()
    url: fields.String = fields.String()
    contact: fields.String = fields.String()


class DependencySchema(_BaseSchema):
    """Validates a single entry of a manifest's `dependencies` list."""

    source: fields.String = fields.String(required=True)
    target: fields.String = fields.String(required=True)


class RunbookEnvSchema(_BaseSchema):
    """Validates a single entry of a runbook's `env` list."""

    name: fields.String = fields.String(required=True)
    value: fields.String = fields.String()


class RunbookEnvSchemaV1_3(RunbookEnvSchema):
    """Runbook env entry, adding the `if` conditional introduced in v1.3."""

    with_if: fields.String = fields.String(data_key="if")


class RunbookSchema(_BaseSchema):
    """Validates a single entry of a manifest's `runbooks` list."""

    name: fields.String = fields.String(required=True)
    entrypoint: fields.String = fields.String(required=True)
    workdir: fields.String = fields.String()
    args: fields.List[str] = fields.List(
        fields.String(), validate=validate.Length(min=1)
    )
    env: fields.List[dict[str, object]] = fields.List(
        fields.Nested(RunbookEnvSchema), validate=validate.Length(min=1)
    )


class RunbookSchemaV1_3(RunbookSchema):
    """Runbook entry using the v1.3 env schema (adds the `if` conditional)."""

    env: fields.List[dict[str, object]] = fields.List(
        fields.Nested(RunbookEnvSchemaV1_3), validate=validate.Length(min=1)
    )


class ImportSchema(_BaseSchema):
    """Validates a single entry of a manifest's `imports` list."""

    source: fields.String = fields.String(required=True, data_key="from")
    resource: fields.String = fields.String(required=True, data_key="import")
    target: fields.String = fields.String(data_key="as")


class ManifestSchemaV1_0(_BaseSchema):
    """Manifest schema version 1.0: metadata and dependencies."""

    version: fields.String = fields.String(required=True)
    metadata: fields.Nested = fields.Nested(MetadataSchema, required=True)
    dependencies: fields.List[dict[str, object]] = fields.List(
        fields.Nested(DependencySchema), validate=validate.Length(min=1)
    )


class ManifestSchemaV1_1(ManifestSchemaV1_0):
    """Manifest schema version 1.1: adds runbooks."""

    runbooks: fields.List[dict[str, object]] = fields.List(
        fields.Nested(RunbookSchema), validate=validate.Length(min=1)
    )


class ManifestSchemaV1_2(ManifestSchemaV1_1):
    """Manifest schema version 1.2: adds imports."""

    imports: fields.List[dict[str, object]] = fields.List(
        fields.Nested(ImportSchema), validate=validate.Length(min=1)
    )


class ManifestSchemaV1_3(ManifestSchemaV1_2):
    """Manifest schema version 1.3: adds the runbook env `if` conditional."""

    runbooks: fields.List[dict[str, object]] = fields.List(
        fields.Nested(RunbookSchemaV1_3), validate=validate.Length(min=1)
    )


class EngineSchema(_BaseSchema):
    """Validates a manifest's `engine` block."""

    name: fields.String = fields.String(
        required=True, validate=validate.OneOf(["terraform"])
    )
    version: fields.String = fields.String(
        required=True,
        validate=validate.Regexp(
            r"^(system|latest|\d+\.\d+\.\d+)$",
            error="Must be `system`, `latest` or an exact version like `1.9.5`.",
        ),
    )


class ManifestSchemaV1_4(ManifestSchemaV1_3):
    """Manifest schema version 1.4: adds the `engine` block."""

    engine: fields.Nested = fields.Nested(EngineSchema)


MANIFEST_SCHEMAS: MappingProxyType[str, type[Schema]] = MappingProxyType(
    {
        "1.0": ManifestSchemaV1_0,
        "1.1": ManifestSchemaV1_1,
        "1.2": ManifestSchemaV1_2,
        "1.3": ManifestSchemaV1_3,
        "1.4": ManifestSchemaV1_4,
    }
)
