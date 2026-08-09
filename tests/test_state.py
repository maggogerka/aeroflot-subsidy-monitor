from app.state import MonitorState, StateStore


def test_same_availability_only_notifies_once():
    state = MonitorState()
    assert state.record_availability("2026-08-29", True, "AVAILABLE") is True
    assert state.record_availability("2026-08-29", True, "AVAILABLE") is False
    assert state.date_state("2026-08-29").occurrence == 1


def test_reappearance_creates_new_occurrence():
    state = MonitorState()
    assert state.record_availability("2026-08-29", True, "AVAILABLE") is True
    assert state.record_availability("2026-08-29", False, "UNAVAILABLE") is False
    assert state.record_availability("2026-08-29", True, "AVAILABLE") is True
    assert state.date_state("2026-08-29").occurrence == 2


def test_state_survives_restart(tmp_path):
    path = tmp_path / "state.json"
    store = StateStore(path)
    state = MonitorState(cycle=7, monitor_started_at="2026-07-28T12:00:00+00:00")
    state.record_availability("2026-08-30", True, "AVAILABLE")
    state.record_success()
    store.save(state)

    loaded = StateStore(path).load()
    assert loaded.cycle == 7
    assert loaded.monitor_started_at == "2026-07-28T12:00:00+00:00"
    assert loaded.date_state("2026-08-30").available is True
    assert loaded.last_successful_check is not None


def test_corrupt_state_is_recovered(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{broken", encoding="utf-8")
    state = StateStore(path).load()
    assert state.cycle == 0
    assert list(tmp_path.glob("state.json.corrupt-*"))
