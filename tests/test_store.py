from pathlib import Path

from rhexora.store import TaskStore


def test_store_deduplicates_completed_tasks_and_detects_request_conflicts(tmp_path: Path):
    store = TaskStore(tmp_path / "task-ledger.sqlite3")
    assert store.claim("job-1", "task-hash") == {"state": "claimed"}
    assert store.claim("job-1", "task-hash") == {"state": "running"}

    response = {"kind": "result", "request_id": "job-1", "ok": True, "result": {"sum": 9}}
    store.save_response("job-1", task_hash="task-hash", response=response, status="completed")

    assert store.claim("job-1", "task-hash") == {"state": "completed", "response": response}
    assert store.claim("job-1", "different-task-hash") == {"state": "conflict"}
    assert store.record_count() == 1


def test_store_recovers_running_task_after_process_restart(tmp_path: Path):
    db_path = tmp_path / "task-ledger.sqlite3"
    first_process = TaskStore(db_path)
    assert first_process.claim("job-2", "hash-2") == {"state": "claimed"}

    restarted_process = TaskStore(db_path)
    assert restarted_process.claim("job-2", "hash-2") == {"state": "claimed"}
