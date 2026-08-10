from vague_code.tui.state import TuiEntryKind, TuiTranscript


def test_add_entry_assigns_ids_and_default_labels() -> None:
    t = TuiTranscript()
    first = t.add(TuiEntryKind.USER, "hello")
    second = t.add(TuiEntryKind.ASSISTANT, "hi")
    assert first.id == 1
    assert second.id == 2
    assert first.label == "you"
    assert second.label == "vague-code"
    assert len(t.entries) == 2


def test_add_entry_custom_label_and_status() -> None:
    t = TuiTranscript()
    entry = t.add(TuiEntryKind.TOOL, "cmd", label="tool bash", status="running")
    assert entry.label == "tool bash"
    assert entry.status == "running"


def test_record_tool_activity_running_sets_active() -> None:
    t = TuiTranscript()
    running = t.record_tool_activity("bash", "running")
    assert t.active_tool is running
    assert t.recent_tools == []


def test_record_tool_activity_finished_moves_to_recent() -> None:
    t = TuiTranscript()
    t.record_tool_activity("bash", "running")
    done = t.record_tool_activity("bash", "success", summary="ok")
    assert t.active_tool is None
    assert t.recent_tools == [done]
