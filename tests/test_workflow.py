import hashlib
import io
import json
import sqlite3
import zipfile

import pytest
from pypdf import PdfReader

from conftest import invoices, pdf_bytes, project


def test_legacy_batch_splits_into_individual_invoices_without_changing_total(site, legacy):
    _, client, _ = site
    directory, meta = legacy
    original = (directory / "meta.json").read_bytes()
    rows = invoices(client)
    assert len(rows) == 2
    assert sum(row["amount_cents"] for row in rows) == 10001
    assert all(row["legacy_allocated"] for row in rows)
    assert {row["original_category"] for row in rows} == {"专用材料费"}
    assert (directory / "meta.json").read_bytes() == original


def test_classify_each_invoice_into_different_projects_preserving_source(site, legacy):
    _, client, _ = site
    original = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in legacy[0].iterdir()}
    a, b = project(client), project(client, "项目B", "B-001")
    rows = invoices(client)
    for row, pid in zip(rows, [a, b]):
        r = client.post("/api/admin/allocate", json={"invoice_ids": [row["id"]], "project_id": pid})
        assert r.status_code == 200, r.data
    data = client.get("/api/admin/workbench").json
    assert sorted(p["total_cents"] for p in data["projects"]) == [5000, 5001]
    assert all(p["categories"][0]["name"] == "专用材料费" for p in data["projects"])
    assert sum(p["users"][0]["amount_cents"] for p in data["projects"]) == 10001
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in legacy[0].iterdir()} == original


def test_category_override_and_unassignment(site, legacy):
    _, client, _ = site
    pid = project(client)
    category = client.post(f"/api/admin/projects/{pid}/categories", json={"name": "设备配件"}).json["id"]
    row = invoices(client)[0]
    assert client.post("/api/admin/allocate", json={"invoice_ids": [row["id"]], "project_id": pid, "category_id": category}).status_code == 200
    updated = invoices(client)[0]
    assert updated["category"] == "设备配件"
    assert updated["original_category"] == "专用材料费"
    assert client.post("/api/admin/allocate", json={"invoice_ids": [row["id"]], "project_id": None}).status_code == 200
    assert invoices(client)[0]["project_id"] is None
    assert client.delete(f"/api/admin/projects/{pid}").status_code == 200


def test_batch_allocation_is_atomic_for_missing_or_draft_invoice(site, legacy):
    module, client, _ = site
    pid = project(client)
    row = invoices(client)[0]
    response = client.post("/api/admin/allocate", json={"invoice_ids": [row["id"], "missing"], "project_id": pid})
    assert response.status_code == 400
    assert all(r["project_id"] is None for r in invoices(client))
    with module.app.app_context():
        module.get_db().execute("UPDATE assignments SET status=1")
        module.get_db().commit()
    assert client.post("/api/admin/allocate", json={"invoice_ids": [row["id"]], "project_id": pid}).status_code == 409


def test_reject_category_of_another_project_and_stale_update(site, legacy):
    _, client, _ = site
    a, b = project(client), project(client, "B", "")
    category = client.post(f"/api/admin/projects/{b}/categories", json={"name": "错误项目项"}).json["id"]
    row = invoices(client)[0]
    assert client.post("/api/admin/allocate", json={"invoice_ids": [row["id"]], "project_id": a, "category_id": category}).status_code == 400
    assert client.post("/api/admin/allocate", json={"invoice_ids": [row["id"]], "project_id": a}).status_code == 200
    response = client.post("/api/admin/allocate", json={"invoice_ids": [row["id"]], "project_id": b, "versions": {row["id"]: row["version"]}})
    assert response.status_code == 409


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-1", "not-money", "10000000000000"])
def test_invalid_amount_rejected_without_mutation(site, legacy, amount):
    _, client, _ = site
    row = invoices(client)[0]
    response = client.post("/api/admin/invoices/amount", json={"invoice_id": row["id"], "amount": amount})
    assert response.status_code == 400
    assert invoices(client)[0]["amount_cents"] == row["amount_cents"]


def test_per_invoice_amount_propagates_to_project_and_member_totals(site, legacy):
    _, client, _ = site
    pid = project(client)
    rows = invoices(client)
    client.post("/api/admin/allocate", json={"invoice_ids": [r["id"] for r in rows], "project_id": pid})
    assert client.post("/api/admin/invoices/amount", json={"invoice_id": rows[0]["id"], "amount": "12.34"}).status_code == 200
    data = client.get("/api/admin/workbench").json
    assert data["projects"][0]["total_cents"] == 6234
    assert data["projects"][0]["categories"][0]["amount_cents"] == 6234
    task = client.get("/api/user/tasks").json[1]
    assert task["final_amount"] == 62.34


def test_project_export_has_only_assigned_invoice_and_attachments(site, legacy):
    _, client, _ = site
    pid = project(client)
    row = invoices(client)[0]
    client.post("/api/admin/allocate", json={"invoice_ids": [row["id"]], "project_id": pid})
    response = client.get(f"/admin/projects/{pid}/download")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.data)) as bundle:
        names = bundle.namelist()
        assert sum("主发票_" in name for name in names) == 1
        assert sum("附件_" in name for name in names) == 2
        assert "报销汇总.csv" in names
        assert "归档清单.json" in names
        csv_text = bundle.read("报销汇总.csv").decode("utf-8-sig")
        assert "5001" not in csv_text  # summary uses yuan, not raw cents
        assert "50.01" in csv_text


def test_print_defaults_exclude_attachments_can_include_pdf_and_deduplicate(site, legacy):
    _, client, _ = site
    pid = project(client)
    client.post("/api/admin/allocate", json={"invoice_ids": [r["id"] for r in invoices(client)], "project_id": pid})
    manifest = client.get(f"/api/admin/projects/{pid}/print-manifest").json
    files = manifest["files"]
    assert len([f for f in files if f["kind"] == "attachment"]) == 2
    assert all(not f["selected"] for f in files if f["kind"] == "attachment")
    default_ids = [f["id"] for f in files if f["selected"]]
    default = client.post(f"/admin/projects/{pid}/print", json={"file_ids": default_ids})
    assert default.status_code == 200
    assert len(PdfReader(io.BytesIO(default.data)).pages) == 2
    pdf_attachment = next(f for f in files if f["kind"] == "attachment" and f["printable"])
    combined = client.post(f"/admin/projects/{pid}/print", json={"file_ids": default_ids + [pdf_attachment["id"], pdf_attachment["id"]]})
    assert len(PdfReader(io.BytesIO(combined.data)).pages) == 3
    doc = next(f for f in files if not f["printable"])
    assert client.post(f"/admin/projects/{pid}/print", json={"file_ids": [doc["id"]]}).status_code == 400
    assert client.post(f"/admin/projects/{pid}/print", json={"file_ids": ["outside-project"]}).status_code == 400


def test_corrupt_pdf_fails_print_instead_of_silently_omitting_invoice(site, legacy):
    _, client, _ = site
    pid = project(client)
    row = invoices(client)[0]
    client.post("/api/admin/allocate", json={"invoice_ids": [row["id"]], "project_id": pid})
    (legacy[0] / row["filename"]).write_bytes(b"corrupt")
    manifest = client.get(f"/api/admin/projects/{pid}/print-manifest").json
    response = client.post(f"/admin/projects/{pid}/print", json={"file_ids": [f["id"] for f in manifest["files"] if f["selected"]]})
    assert response.status_code == 400
    assert row["filename"] in response.json["message"]


def test_guide_public_admin_editable_and_html_escaped(site):
    module, client, _ = site
    assert "使用说明" in module.app.test_client().get("/login").get_data(as_text=True)
    text = '实验室说明\n<script>alert(1)</script>'
    assert client.post("/api/admin/guide", json={"content": text}).status_code == 200
    with client.session_transaction() as session:
        session.clear()
    html = client.get("/help").get_data(as_text=True)
    assert "实验室说明" in html
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert client.post("/api/admin/guide", json={"content": "hijack"}).status_code in (401, 403)


def test_new_upload_same_names_are_not_overwritten_and_can_remove_single_file(site):
    module, client, root = site
    with module.app.app_context():
        module.get_db().execute("UPDATE assignments SET status=1 WHERE window_id=1")
        module.get_db().commit()
    response = client.post("/api/user/upload_item/1", data={"type": "专用材料费", "invoice_files": [(io.BytesIO(pdf_bytes()), "same.pdf"), (io.BytesIO(pdf_bytes()), "same.pdf")]})
    assert response.status_code == 200
    task = next(t for t in client.get("/api/user/tasks").json if t["id"] == 1)
    item = task["items"][0]
    assert len(set(item["invoice_files"])) == 2
    assert client.delete(f"/api/user/item/1/{item['id']}/files", json={"kind": "invoice", "filename": item["invoice_files"][0]}).status_code == 200
    assert client.post(f"/api/user/item/1/{item['id']}/files", data={"attachments": (io.BytesIO(pdf_bytes()), "新附件.pdf")}).status_code == 200
    updated = next(t for t in client.get("/api/user/tasks").json if t["id"] == 1)["items"][0]
    assert len(updated["invoice_files"]) == 1
    assert len(updated["attachments"]) == 1


def test_assigned_records_and_nonempty_project_cannot_be_deleted(site, legacy):
    module, client, _ = site
    pid = project(client)
    client.post("/api/admin/allocate", json={"invoice_ids": [invoices(client)[0]["id"]], "project_id": pid})
    assert client.delete(f"/api/admin/projects/{pid}").status_code == 409
    with module.app.app_context():
        module.get_db().execute("UPDATE assignments SET status=1")
        module.get_db().commit()
    assert client.delete("/api/user/item/1/old").status_code == 409
    assert client.post("/api/user/item/1/old/amount", json={"amount": 10}).status_code == 409
    assert client.post("/api/admin/window/delete/1").status_code == 409


def test_migration_is_idempotent_and_leaves_old_records_and_files(site, legacy):
    module, client, root = site
    module.init_db()
    module.init_db()
    with module.app.app_context():
        assert module.get_db().execute("SELECT count(*) FROM users").fetchone()[0] == 2
        assert module.get_db().execute("SELECT count(*) FROM windows").fetchone()[0] == 2
        tables = {r[0] for r in module.get_db().execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"projects", "project_categories", "invoice_allocations", "settings"} <= tables
    assert len(invoices(client)) == 2


def test_legacy_single_file_and_orphan_owner_are_visible(site):
    _, client, root = site
    folder = root / "uploads" / "task_1" / "旧成员" / "item_single"
    folder.mkdir(parents=True)
    (folder / "原始.pdf").write_bytes(pdf_bytes())
    (folder / "meta.json").write_text(json.dumps({"invoice_file": "原始.pdf", "auto_amount": 19.9, "type": "其他"}), encoding="utf-8")
    rows = invoices(client)
    assert len(rows) == 1
    assert rows[0]["amount_cents"] == 1990
    assert rows[0]["owner"] == "旧成员"


def test_anonymous_cannot_read_workbench_and_csrf_required(site):
    _, client, _ = site
    assert client.post("/api/admin/projects", json={"name": "forged"}, headers={"X-CSRF-Token": "bad"}).status_code == 403
    with client.session_transaction() as session:
        session.clear()
    assert client.get("/api/admin/workbench").status_code == 401


def test_identical_item_ids_and_filenames_in_two_tasks_remain_independent(site, legacy):
    import shutil
    _, client, root = site
    destination = root / "uploads" / "task_2" / "测试成员" / "item_old"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(legacy[0], destination)
    rows = invoices(client)
    assert len({r["id"] for r in rows}) == 4
    pid = project(client)
    ids = [r["id"] for r in rows if r["task_id"] == 1]
    assert client.post("/api/admin/allocate", json={"invoice_ids": ids, "project_id": pid}).status_code == 200
    assert all(r["project_id"] is None for r in invoices(client) if r["task_id"] == 2)
    assert client.get("/api/admin/workbench").json["projects"][0]["total_cents"] == 10001


def test_editing_returned_draft_preserves_previous_per_invoice_calibration(site, legacy):
    module, client, _ = site
    rows = invoices(client)
    assert client.post("/api/admin/invoices/amount", json={"invoice_id": rows[0]["id"], "amount": "9.99"}).status_code == 200
    with module.app.app_context():
        module.get_db().execute("UPDATE assignments SET status=1 WHERE window_id=1")
        module.get_db().commit()
    assert client.delete("/api/user/item/1/old/files", json={"kind": "invoice", "filename": rows[1]["filename"]}).status_code == 200
    remaining = invoices(client)
    assert len(remaining) == 1
    assert remaining[0]["amount_cents"] == 999
    assert next(t for t in client.get("/api/user/tasks").json if t["id"] == 1)["final_amount"] == 9.99


def test_non_ascii_invalid_admin_password_returns_validation_message(site):
    _, client, _ = site
    response = client.post("/admin/login", data={"password": "错误密码"})
    assert response.status_code == 200
    assert "管理员密码错误" in response.get_data(as_text=True)


def test_invalid_print_request_is_rejected(site, legacy):
    _, client, _ = site
    pid = project(client)
    assert client.post(f"/admin/projects/{pid}/print", json=["bad request"]).status_code == 400
