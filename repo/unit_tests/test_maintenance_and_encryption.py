import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from app.core.encryption import decrypt_string, encrypt_string
from app.services.maintenance_service import MaintenanceService


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _MappingResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeDb:
    def __init__(self, process_rows=None, task_rows=None, table_rows=None):
        self.process_rows = process_rows or []
        self.task_rows = task_rows or []
        self.table_rows = table_rows or {}
        self.calls = 0

    def scalars(self, query):
        self.calls += 1
        if self.calls == 1:
            return _ScalarResult(self.process_rows)
        if self.calls == 2:
            return _ScalarResult(self.task_rows)
        return _ScalarResult([])

    def execute(self, query):
        table_name = query.get_final_froms()[0].name
        return _MappingResult(self.table_rows.get(table_name, []))


def test_sensitive_encryption_round_trip():
    encoded = encrypt_string("jane.doe@example.com")
    assert encoded != "jane.doe@example.com"
    assert decrypt_string(encoded) == "jane.doe@example.com"


def test_maintenance_backup_writes_file(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.maintenance_service.BACKUP_ROOT", Path(tmp_path))
    monkeypatch.setattr("app.services.maintenance_service.MaintenanceService._fetch_raw_rows", lambda **_: [
        {
            "id": "user-1",
            "username": "alice",
            "email": "jane.doe@example.com",
            "phone": "5551234567",
            "medical_record_number": "MRN-123",
        }
    ])
    fake_db = _FakeDb(
        process_rows=[SimpleNamespace(id="proc-1", organization_id="org-1", business_number="BN-1", status="completed", payload={"ok": True})],
        task_rows=[SimpleNamespace(id="task-1", organization_id="org-1", process_instance_id="proc-1", status="completed", step_name="review")],
        table_rows={},
    )
    job = SimpleNamespace(details={}, next_run_at=None)

    MaintenanceService._run_full_backup(db=fake_db, job=job)

    assert job.details["backup_path"].startswith(str(tmp_path))
    backup_path = Path(job.details["backup_path"])
    assert backup_path.exists()
    payload = json.loads(backup_path.read_text(encoding="utf-8"))
    assert payload["algorithm"] == "AES-256-GCM"
    assert payload["compression"] == "gzip"
    file_bytes = backup_path.read_bytes()
    assert b"jane.doe@example.com" not in file_bytes
    assert b"5551234567" not in file_bytes
    assert b"MRN-123" not in file_bytes


def test_maintenance_backup_cleans_up_stale_files(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.maintenance_service.BACKUP_ROOT", Path(tmp_path))
    stale_backup = tmp_path / "backup_20200101000000.json.enc"
    stale_backup.write_text("{}", encoding="utf-8")
    fresh_backup = tmp_path / "backup_20990101000000.json.enc"
    fresh_backup.write_text("{}", encoding="utf-8")

    MaintenanceService._cleanup_old_backups()

    assert not stale_backup.exists()
    assert fresh_backup.exists()


def test_maintenance_archive_updates_payload():
    old_completion = datetime.now(timezone.utc) - timedelta(days=60)
    instance = SimpleNamespace(
        completed_at=old_completion,
        status="completed",
        payload={"flow": "done"},
    )
    fake_db = _FakeDb(process_rows=[instance], task_rows=[])
    job = SimpleNamespace(details={}, next_run_at=None)

    MaintenanceService._archive_completed_workflows(db=fake_db, job=job)

    assert job.details["archived_count"] == 1
    assert instance.payload["archived_at"] is not None


def test_maintenance_archive_records_audit_and_version_snapshot(monkeypatch):
    instance = SimpleNamespace(
        id="proc-1",
        organization_id="org-1",
        completed_at=datetime.now(timezone.utc) - timedelta(days=60),
        status="completed",
        payload={"flow": "done"},
    )
    fake_db = _FakeDb(process_rows=[instance], task_rows=[])
    job = SimpleNamespace(details={}, next_run_at=None)
    snapshot_calls = []
    audit_calls = []

    monkeypatch.setattr(
        "app.services.maintenance_service.DataGovernanceService.create_version_snapshot",
        lambda **kwargs: snapshot_calls.append(kwargs),
    )
    monkeypatch.setattr(
        "app.services.maintenance_service.AuditService.log_event",
        lambda **kwargs: audit_calls.append(kwargs),
    )

    MaintenanceService._archive_completed_workflows(db=fake_db, job=job)

    assert job.details["archived_count"] == 1
    assert len(snapshot_calls) == 1
    assert len(audit_calls) == 1
    assert audit_calls[0]["action"] == "workflow.archived"
