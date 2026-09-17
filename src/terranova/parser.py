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
"""
Low-level parsing of terraform's own `-json` machine-readable output.

This module knows only about terraform's wire format (`TfEvent`, one line of
its newline-delimited JSON UI stream) and stays deliberately dumb about what
that means for terranova. `terranova.binds` is the boundary that interprets
these events into terranova's own result types - keep it that way rather than
growing domain concepts (summaries, validation results, ...) in here, so
terraform's JSON schema never leaks past `binds.py` as terranova's own API.
"""

import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Final, cast

from terranova.utils import str_or_none

_WHITESPACE: Final[str] = " \t\r\n"


@dataclass(frozen=True)
class TfEvent:
    """Represents one line of terraform's `-json` machine-readable output."""

    type: str
    level: str | None
    message: str | None
    raw: dict[str, object]

    @staticmethod
    def parse(payload: dict[str, object]) -> "TfEvent | None":
        """Build a `TfEvent` from one already-decoded JSON object, if it's shaped like one."""
        event_type = payload.get("type")
        if not isinstance(event_type, str):
            return None
        return TfEvent(
            type=event_type,
            level=str_or_none(payload.get("@level")),
            message=str_or_none(payload.get("@message")),
            raw=payload,
        )


def iter_events(text: str) -> Iterator[TfEvent]:
    """
    Parse every JSON value in terraform's `-json` output as a stream of events.

    Scans `text` in place with `json.JSONDecoder.raw_decode` at successive
    offsets, instead of splitting it into per-line copies first - `str` is
    immutable, so `str.splitlines()`/slicing would allocate a brand new string
    object for every line. `raw_decode` reads straight out of the original
    buffer, so parsing never costs more memory than the output objects it
    actually produces. A malformed line is tolerated and skipped by resuming
    at the next newline, rather than raising, since terraform occasionally
    writes non-JSON diagnostics to the same stream this reads from (e.g. a
    crash before JSON mode is fully initialized).

    Args:
        text: the full captured `-json` output of a terraform invocation.

    Yields:
        each successfully parsed event, in stream order.
    """
    decoder = json.JSONDecoder()
    pos = 0
    length = len(text)
    while pos < length:
        while pos < length and text[pos] in _WHITESPACE:
            pos += 1
        if pos >= length:
            return
        try:
            decoded = cast("tuple[object, int]", decoder.raw_decode(text, pos))
        except json.JSONDecodeError:
            next_newline = text.find("\n", pos)
            pos = next_newline + 1 if next_newline != -1 else length
            continue
        payload, pos = decoded
        if not isinstance(payload, dict):
            continue
        event = TfEvent.parse(cast("dict[str, object]", payload))
        if event is not None:
            yield event
