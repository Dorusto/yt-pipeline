import json

from common.feedback import append_feedback


def rows_of(workspace):
    path = workspace / "feedback.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_append_feedback_appends_one_line(tmp_path):
    append_feedback(tmp_path, "feedback", text="hello", tool="t")
    rows = rows_of(tmp_path)
    assert len(rows) == 1
    assert rows[0]["action"] == "feedback"
    assert rows[0]["text"] == "hello"
    assert rows[0]["tool"] == "t"
    assert rows[0]["ts"]


def test_append_feedback_appends_never_overwrites(tmp_path):
    append_feedback(tmp_path, "feedback", text="one")
    append_feedback(tmp_path, "reply", text="two", reply="ok")
    rows = rows_of(tmp_path)
    assert [row["action"] for row in rows] == ["feedback", "reply"]
    assert rows[1]["reply"] == "ok"
