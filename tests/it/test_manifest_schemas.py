from __future__ import annotations

import pytest
from marshmallow import ValidationError

from terranova.schemas.manifest import (
    ManifestSchemaV1_0,
    ManifestSchemaV1_1,
    ManifestSchemaV1_2,
    ManifestSchemaV1_3,
)


class TestManifestSchemas:
    def test_v1_0_minimal_manifest_is_valid(self) -> None:
        ManifestSchemaV1_0().load(
            {"version": "1.0", "metadata": {"name": "test", "description": "test"}}
        )

    def test_v1_1_minimal_manifest_is_valid(self) -> None:
        ManifestSchemaV1_1().load(
            {"version": "1.1", "metadata": {"name": "test", "description": "test"}}
        )

    def test_v1_1_runbook_missing_entrypoint_raises(self) -> None:
        with pytest.raises(ValidationError):
            ManifestSchemaV1_1().load(
                {
                    "version": "1.1",
                    "metadata": {"name": "test", "description": "test"},
                    "runbooks": [{"name": "my_runbook"}],
                }
            )

    def test_v1_2_minimal_manifest_is_valid(self) -> None:
        ManifestSchemaV1_2().load(
            {"version": "1.2", "metadata": {"name": "test", "description": "test"}}
        )

    def test_v1_2_import_accepts_wire_keys(self) -> None:
        ManifestSchemaV1_2().load(
            {
                "version": "1.2",
                "metadata": {"name": "test", "description": "test"},
                "imports": [
                    {
                        "from": "../other_group",
                        "import": "output_var",
                        "as": "input_var",
                    }
                ],
            }
        )

    def test_v1_2_import_missing_required_keys_raises(self) -> None:
        with pytest.raises(ValidationError):
            ManifestSchemaV1_2().load(
                {
                    "version": "1.2",
                    "metadata": {"name": "test", "description": "test"},
                    "imports": [{"as": "input_var"}],
                }
            )

    def test_v1_3_minimal_manifest_is_valid(self) -> None:
        ManifestSchemaV1_3().load(
            {"version": "1.3", "metadata": {"name": "test", "description": "test"}}
        )

    def test_v1_3_runbook_env_accepts_if(self) -> None:
        ManifestSchemaV1_3().load(
            {
                "version": "1.3",
                "metadata": {"name": "test", "description": "test"},
                "runbooks": [
                    {
                        "name": "my_runbook",
                        "entrypoint": "sh",
                        "env": [{"name": "OPTIONAL_VAR", "if": "is_defined"}],
                    }
                ],
            }
        )
