import json
import os

import pytest

from meeting_digest.state import DeliveryState, StateError


def test_missing_file_starts_empty(tmp_path):
    state = DeliveryState.load(str(tmp_path / "nothing.json"))

    assert len(state) == 0
    assert state.pending_sinks("conv_1", ["markdown"]) == {"markdown"}


def test_marks_and_persists_per_sink(tmp_path):
    path = str(tmp_path / "state.json")
    state = DeliveryState.load(path)
    state.mark_delivered("conv_1", "markdown")
    state.save()

    reloaded = DeliveryState.load(path)

    assert reloaded.is_delivered("conv_1", "markdown")
    assert not reloaded.is_delivered("conv_1", "slack")
    assert reloaded.pending_sinks("conv_1", ["markdown", "slack"]) == {"slack"}


def test_save_creates_missing_directories(tmp_path):
    path = str(tmp_path / "nested" / "deeper" / "state.json")
    state = DeliveryState.load(path)
    state.mark_delivered("conv_1", "markdown")
    state.save()

    assert os.path.exists(path)


def test_save_leaves_no_temp_files_behind(tmp_path):
    path = str(tmp_path / "state.json")
    state = DeliveryState.load(path)
    state.mark_delivered("conv_1", "markdown")
    state.save()
    state.save()

    assert sorted(os.listdir(str(tmp_path))) == ["state.json"]


def test_corrupt_state_is_an_error_not_a_silent_reset(tmp_path):
    # A silent reset would re-deliver every conversation in the lookback window.
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(StateError):
        DeliveryState.load(str(path))


def test_unknown_state_version_is_rejected(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"version": 99, "delivered": {}}), encoding="utf-8")

    with pytest.raises(StateError):
        DeliveryState.load(str(path))
