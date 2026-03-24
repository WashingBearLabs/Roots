"""Tests for StdoutSink and FileSink (US-003)."""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import PropertyMock, patch

import pytest

from roots.events.sinks import FileSink, StdoutSink
from roots.events.types import EventEnvelope


@pytest.fixture
def sample_event() -> EventEnvelope:
    return EventEnvelope(
        event="roots.run.started",
        timestamp=datetime(2026, 3, 23, 12, 0, 0, tzinfo=timezone.utc),
        run_id="run-1",
        process_id="proc-1",
        metadata={"key": "value"},
    )


# --- StdoutSink ---


class TestStdoutSink:
    async def test_prints_event_to_stdout(
        self, sample_event: EventEnvelope, capsys: pytest.CaptureFixture[str]
    ) -> None:
        sink = StdoutSink()
        await sink.emit(sample_event)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["event"] == "roots.run.started"
        assert data["run_id"] == "run-1"

    async def test_default_pretty_prints(
        self, sample_event: EventEnvelope, capsys: pytest.CaptureFixture[str]
    ) -> None:
        sink = StdoutSink()
        await sink.emit(sample_event)
        captured = capsys.readouterr()
        # Pretty-printed JSON has newlines and indentation
        assert "\n" in captured.out.strip()
        assert "  " in captured.out

    async def test_compact_mode_single_line(
        self, sample_event: EventEnvelope, capsys: pytest.CaptureFixture[str]
    ) -> None:
        sink = StdoutSink(compact=True)
        await sink.emit(sample_event)
        captured = capsys.readouterr()
        # Compact output is a single line (plus trailing newline from print)
        lines = captured.out.strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["event"] == "roots.run.started"

    async def test_serialization_error_handled(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        sink = StdoutSink()
        event = EventEnvelope(
            event="roots.run.started",
            timestamp=datetime.now(timezone.utc),
            run_id="run-1",
            process_id="proc-1",
        )
        with patch.object(
            type(event),
            "model_dump_json",
            new_callable=PropertyMock(return_value=lambda **kw: (_ for _ in ()).throw(ValueError("boom"))),
        ):
            await sink.emit(event)
        # Should not crash — stdout should be empty or have no valid JSON
        captured = capsys.readouterr()
        assert "roots.run.started" not in captured.out


# --- FileSink ---


class TestFileSink:
    async def test_appends_event_as_json_line(
        self, sample_event: EventEnvelope, tmp_path: Path
    ) -> None:
        file = tmp_path / "events.jsonl"
        sink = FileSink(file)
        await sink.emit(sample_event)
        content = file.read_text()
        lines = content.strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["event"] == "roots.run.started"
        assert data["run_id"] == "run-1"

    async def test_creates_file_if_not_exists(
        self, sample_event: EventEnvelope, tmp_path: Path
    ) -> None:
        file = tmp_path / "subdir" / "events.jsonl"
        assert not file.exists()
        sink = FileSink(file)
        await sink.emit(sample_event)
        assert file.exists()
        data = json.loads(file.read_text().strip())
        assert data["event"] == "roots.run.started"

    async def test_appends_multiple_events(
        self, sample_event: EventEnvelope, tmp_path: Path
    ) -> None:
        file = tmp_path / "events.jsonl"
        sink = FileSink(file)
        await sink.emit(sample_event)
        event2 = EventEnvelope(
            event="roots.run.completed",
            timestamp=datetime.now(timezone.utc),
            run_id="run-1",
            process_id="proc-1",
        )
        await sink.emit(event2)
        lines = file.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["event"] == "roots.run.started"
        assert json.loads(lines[1])["event"] == "roots.run.completed"

    async def test_serialization_error_handled(
        self, tmp_path: Path
    ) -> None:
        file = tmp_path / "events.jsonl"
        sink = FileSink(file)
        event = EventEnvelope(
            event="roots.run.started",
            timestamp=datetime.now(timezone.utc),
            run_id="run-1",
            process_id="proc-1",
        )
        with patch.object(
            type(event),
            "model_dump_json",
            new_callable=PropertyMock(return_value=lambda **kw: (_ for _ in ()).throw(ValueError("boom"))),
        ):
            await sink.emit(event)
        # Should not crash — file should not exist or be empty
        if file.exists():
            assert file.read_text() == ""

    async def test_accepts_string_path(
        self, sample_event: EventEnvelope, tmp_path: Path
    ) -> None:
        file = tmp_path / "events.jsonl"
        sink = FileSink(str(file))
        await sink.emit(sample_event)
        assert file.exists()
        data = json.loads(file.read_text().strip())
        assert data["event"] == "roots.run.started"
