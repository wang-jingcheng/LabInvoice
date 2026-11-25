from contextlib import closing
import io
import sqlite3
import zipfile

from conftest import invoices, project


def archive_state(client, route="/api/admin/workbench"):
    return {p["id"]: p["is_archived"] for p in client.get(route).json["projects"]}


def allocate(client, invoice, project_id):
    return client.post("/api/admin/allocate", json={"invoice_ids": [invoice["id"]], "project_id": project_id})


def manual_item(client, task_id, project_id):
    response = client.post("/api/admin/manual-expenses", json={
        "task_id": task_id, "user_id": 1, "project_id": project_id,
        "category": "劳务费", "amount": "23.45", "remark": "核对完成",
    })
    assert response.status_code == 200
    return response.json["id"]


def test_completing_task_archives_its_projects_and_preserves_exports(site, legacy):
    _, client, _ = site
    invoice_project = project(client, "票据项目")
    manual_project = project(client, "手工报销项目")
    unused_project = project(client, "下次使用")
    row = invoices(client)[0]
    assert allocate(client, row, invoice_project).status_code == 200
    manual_item(client, 1, manual_project)
    before = {path.name: path.read_bytes() for path in legacy[0].iterdir()}
    assert client.post("/api/admin/window/1/close").status_code == 200
    assert archive_state(client) == {invoice_project: False, manual_project: False, unused_project: False}

    assert client.post("/api/admin/window/1/complete").status_code == 200
    archived = {invoice_project: True, manual_project: True, unused_project: False}
    assert archive_state(client) == archived
    assert archive_state(client, "/api/admin/overview") == archived
    assert client.post("/api/admin/window/1/complete").status_code == 200
    assert archive_state(client) == archived
    assert {path.name: path.read_bytes() for path in legacy[0].iterdir()} == before
    assert invoices(client)[0]["project_id"] == invoice_project
    assert client.get(f"/api/admin/projects/{invoice_project}/print-manifest").json["invoice_count"] == 1
    response = client.get(f"/admin/projects/{invoice_project}/download")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
        name = next(name for name in archive.namelist() if name.endswith(row["filename"]))
        assert archive.read(name) == before[row["filename"]]
    response = client.get(f"/admin/projects/{manual_project}/download")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
        assert "核对完成" in archive.read("无电子发票报销项.csv").decode("utf-8-sig")


def test_shared_project_archives_after_last_task_and_undo_restores_it(site, legacy):
    _, client, _ = site
    pid = project(client)
    assert allocate(client, invoices(client)[0], pid).status_code == 200
    manual_item(client, 2, pid)
    assert client.post("/api/admin/window/1/complete").status_code == 200
    assert archive_state(client) == {pid: False}
    assert client.post("/api/admin/window/2/complete").status_code == 200
    assert archive_state(client) == {pid: True}
    assert client.post("/api/admin/window/1/uncomplete").status_code == 200
    assert archive_state(client) == {pid: False}
    assert client.get("/api/admin/workbench").json["projects"][0]["total_cents"] == 7346


def test_archive_uses_existing_task_states_and_ignores_deleted_entries(site):
    module, client, _ = site
    pid = project(client)
    manual_item(client, 1, pid)
    removed = manual_item(client, 2, pid)
    assert client.delete(f"/api/admin/manual-expenses/{removed}").status_code == 200
    # An existing deployment may already have completed tasks before this release.
    with module.app.app_context():
        db = module.get_db()
        db.execute("UPDATE windows SET is_completed=1 WHERE id=1")
        db.commit()
    assert archive_state(client) == {pid: True}


def test_archived_projects_are_read_only_for_stale_clients_until_undo(site, legacy):
    _, client, _ = site
    pid = project(client)
    next_pid = project(client, "新一批")
    row, available = invoices(client)
    assert allocate(client, row, pid).status_code == 200
    category = invoices(client)[0]["category_id"]
    assert client.post("/api/admin/window/1/complete").status_code == 200

    url = f"/api/admin/projects/{pid}"
    assert client.patch(url, json={"name": "变更"}).status_code == 409
    assert client.delete(url + "?unassign=1").status_code == 409
    assert client.post(url + "/categories", json={"name": "变更"}).status_code == 409
    assert client.patch(f"/api/admin/categories/{category}", json={"name": "变更"}).status_code == 409
    assert client.delete(f"/api/admin/categories/{category}?unassign=1").status_code == 409
    assert allocate(client, available, pid).status_code == 409
    assert allocate(client, row, next_pid).status_code == 409
    assert allocate(client, row, None).status_code == 409
    assert client.post("/api/admin/invoices/edit", json={"invoice_id": row["id"], "amount": "1"}).status_code == 409
    assert client.post("/api/admin/item/1/测试成员/old/amount", json={"amount": "1"}).status_code == 409
    manual = {"task_id": 2, "user_id": 1, "project_id": pid, "category": "劳务费", "amount": "5"}
    assert client.post("/api/admin/manual-expenses", json=manual).status_code == 409
    mid = manual_item(client, 2, next_pid)
    assert client.patch(f"/api/admin/manual-expenses/{mid}", json=manual).status_code == 409

    assert client.post("/api/admin/window/1/uncomplete").status_code == 200
    assert client.patch(url, json={"name": "恢复整理"}).status_code == 200
    assert allocate(client, row, None).status_code == 200

def test_archive_keeps_task_name_when_invoice_metadata_is_unreadable(site, legacy):
    _, client, _ = site
    pid = project(client)
    assert allocate(client, invoices(client)[0], pid).status_code == 200
    assert client.post("/api/admin/window/1/complete").status_code == 200
    (legacy[0] / "meta.json").write_text("unreadable test fixture", encoding="utf-8")
    summary = client.get("/api/admin/workbench").json["projects"][0]
    assert summary["is_archived"] is True
    assert summary["task_titles"] == ["十月收集"]



def test_workbench_archive_uses_same_snapshot_as_invoice_state(site, legacy, monkeypatch):
    module, client, root = site
    pid = project(client)
    assert allocate(client, invoices(client)[0], pid).status_code == 200
    with closing(sqlite3.connect(root / "labinvoice.db")) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        original_scan = module.collect_invoices

        def scan_then_complete():
            result = original_scan()
            writer.execute("UPDATE windows SET is_completed=1,is_collection_closed=1 WHERE id=1")
            writer.commit()
            return result

        monkeypatch.setattr(module, "collect_invoices", scan_then_complete)
        data = client.get("/api/admin/workbench").json
        assert all(not row["task_completed"] for row in data["invoices"])
        assert data["projects"][0]["is_archived"] is False
        monkeypatch.setattr(module, "collect_invoices", original_scan)
    assert archive_state(client) == {pid: True}

