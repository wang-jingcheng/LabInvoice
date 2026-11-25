import importlib.util
import io
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from pypdf import PdfWriter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def pdf_bytes():
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=400)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.setenv("LABINVOICE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LABINVOICE_ADMIN_PASSWORD", "local-test-admin")
    monkeypatch.chdir(tmp_path)
    spec = importlib.util.spec_from_file_location("isolated_labinvoice", ROOT / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.app.template_folder = str(ROOT / "templates")
    module.app.config.update(TESTING=True)
    with module.app.app_context():
        db = module.get_db()
        db.execute("INSERT INTO users(id,username,password,real_name) VALUES(1,'member','unused','测试成员')")
        db.execute("INSERT INTO users(id,username,password,real_name) VALUES(2,'other','unused','另一成员')")
        db.execute("INSERT INTO windows(id,title,task_type) VALUES(1,'十月收集','日常业务报销')")
        db.execute("INSERT INTO windows(id,title,task_type) VALUES(2,'九月收集','日常业务报销')")
        db.execute("INSERT INTO assignments(window_id,user_id,status) VALUES(1,1,2)")
        db.execute("INSERT INTO assignments(window_id,user_id,status) VALUES(2,1,2)")
        db.commit()
    client = module.app.test_client()
    client.get("/login")
    with client.session_transaction() as session:
        session["admin_logged_in"] = True
        session["user_id"] = 1
        session["real_name"] = "测试成员"
        session["username"] = "member"
        csrf = session.get("csrf_token", "")
    client.environ_base["HTTP_X_CSRF_TOKEN"] = csrf
    return module, client, tmp_path


@pytest.fixture
def legacy(site):
    module, client, root = site
    directory = root / "uploads" / "task_1" / "测试成员" / "item_old"
    directory.mkdir(parents=True, exist_ok=True)
    meta = {"id": "old", "type": "专用材料费", "remark": "历史批次", "auto_amount": 100.01,
            "is_admin_calibrated": True, "invoice_files": ["主发票_一.pdf", "主发票_二.pdf"],
            "attachments": ["附件_证明.pdf", "附件_说明.docx"]}
    for name in meta["invoice_files"] + meta["attachments"]:
        (directory / name).write_bytes(pdf_bytes() if name.endswith(".pdf") else b"test-document")
    (directory / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return directory, meta


def project(client, name="项目A", code="A-2026"):
    response = client.post("/api/admin/projects", json={"name": name, "code": code})
    assert response.status_code == 200, response.data
    return response.json["id"]


def invoices(client):
    response = client.get("/api/admin/workbench")
    assert response.status_code == 200, response.data
    return response.json["invoices"]
