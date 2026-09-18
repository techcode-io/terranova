from __future__ import annotations

from collections.abc import Iterator

from terranova.parser import TfEvent, iter_events

_VERSION_LINE = (
    '{"@level":"info","@message":"Terraform 1.7.0","type":"version",'
    '"terraform_version":"1.7.0"}'
)


class TestIterEvents:
    def test_returns_a_generator(self) -> None:
        result = iter_events(_VERSION_LINE)
        assert isinstance(result, Iterator)

    def test_yields_one_event_per_valid_line(self) -> None:
        text = f"{_VERSION_LINE}\n{_VERSION_LINE}"
        events = list(iter_events(text))
        assert len(events) == 2
        assert all(isinstance(event, TfEvent) for event in events)
        assert all(event.type == "version" for event in events)

    def test_skips_malformed_lines_without_raising(self) -> None:
        text = f"not json\n\n{_VERSION_LINE}\n[1, 2, 3]"
        events = list(iter_events(text))
        assert len(events) == 1
        assert events[0].type == "version"

    def test_empty_text_yields_nothing(self) -> None:
        assert list(iter_events("")) == []
