import hashlib
import io
import zipfile

import pytest
from PIL import Image
from pypdf import PdfReader

from conftest import invoices, project


def image_bytes(fmt="PNG", frames=1):
    output = io.BytesIO()
    image = Image.new("RGB", (160, 220), "white")
    options = {"save_all": True, "append_images": [Image.new("RGB", image.size, (80 + i, 120, 180)) for i in range(frames - 1)]} if frames > 1 else {}
    image.save(output, format=fmt, **options)
    return output.getvalue()


def open_draft(module):
    with module.app.app_context():
        module.get_db().execute("UPDATE assignments SET status=1 WHERE window_id=1")
        module.get_db().commit()


def upload_image(client, fmt="PNG", suffix="png"):
    response = client.post("/api/user/upload_item/1", data={"type": "专用材料费", "invoice_files": (io.BytesIO(image_bytes(fmt)), "付款截图." + suffix)})
    assert response.status_code == 200, response.data
    return response.json["item_id"]


def item_for(client, item_id):
    task = next(t for t in client.get("/api/user/tasks").json if t["id"] == 1)
    return next(i for i in task["items"] if i["id"] == item_id)


@pytest.mark.parametrize("fmt,suffix", [("PNG", "png"), ("JPEG", "JPG"), ("JPEG", "jfif"), ("WEBP", "webp"), ("BMP", "bmp"), ("GIF", "gif"), ("TIFF", "tiff")])
def test_image_upload_unknown_amount_requires_user_entry(site, fmt, suffix):
    module, client, _ = site
    open_draft(module)
    item_id = upload_image(client, fmt, suffix)
    item = item_for(client, item_id)
    assert item["auto_amount"] is None
    assert item["amount_pending"] is True
    row = invoices(client)[0]
    assert row["amount_pending"] is True
    assert client.post("/api/user/submit/1").status_code == 409
    response = client.post(f"/api/user/item/1/{item_id}/amount", json={"filename": item["invoice_files"][0], "amount": "26.58"})
    assert response.status_code == 200, response.data
    updated = item_for(client, item_id)
    assert updated["auto_amount"] == 26.58
    assert updated["amount_pending"] is False
    assert client.post("/api/user/submit/1").status_code == 200
    assert invoices(client)[0]["amount_cents"] == 2658


def test_appending_unknown_image_keeps_known_amount_and_removing_it_resolves_pending(site):
    module, client, _ = site
    open_draft(module)
    item_id = upload_image(client)
    item = item_for(client, item_id)
    filename = item["invoice_files"][0]
    client.post(f"/api/user/item/1/{item_id}/amount", json={"filename": filename, "amount": "12.34"})
    assert client.post(f"/api/user/item/1/{item_id}/files", data={"invoice_files": (io.BytesIO(image_bytes()), "第二张.png")}).status_code == 200
    item = item_for(client, item_id)
    assert item["auto_amount"] is None
    assert item["invoice_amounts"][filename] == 12.34
    second = next(f for f in item["invoice_files"] if f != filename)
    assert item["invoice_amounts"][second] is None
    # Adding attachments must not accidentally turn an unknown amount into zero.
    assert client.post(f"/api/user/item/1/{item_id}/files", data={"attachments": (io.BytesIO(image_bytes()), "附件.png")}).status_code == 200
    assert item_for(client, item_id)["auto_amount"] is None
    assert client.delete(f"/api/user/item/1/{item_id}/files", json={"kind": "invoice", "filename": second}).status_code == 200
    assert item_for(client, item_id)["auto_amount"] == 12.34


def test_each_image_amount_is_saved_independently_and_zero_is_explicitly_valid(site):
    module, client, _ = site
    open_draft(module)
    item_id = upload_image(client)
    client.post(f"/api/user/item/1/{item_id}/files", data={"invoice_files": (io.BytesIO(image_bytes()), "other.png")})
    names = item_for(client, item_id)["invoice_files"]
    assert client.post(f"/api/user/item/1/{item_id}/amount", json={"amount": "20"}).status_code == 400
    for name, amount in zip(names, ["19.99", "0"]):
        assert client.post(f"/api/user/item/1/{item_id}/amount", json={"filename": name, "amount": amount}).status_code == 200
    assert item_for(client, item_id)["auto_amount"] == 19.99
    assert client.post(f"/api/user/item/1/{item_id}/amount", json={"filename": "missing.png", "amount": "5"}).status_code == 404
    assert client.post("/api/user/submit/1").status_code == 200


def test_corrupt_image_cannot_create_a_draft(site):
    module, client, _ = site
    open_draft(module)
    response = client.post("/api/user/upload_item/1", data={"type": "其他", "invoice_files": (io.BytesIO(b"not an image"), "fake.png")})
    assert response.status_code == 400
    assert next(t for t in client.get("/api/user/tasks").json if t["id"] == 1)["items"] == []


@pytest.mark.parametrize("fmt,suffix", [("TIFF", "tif"), ("GIF", "gif")])
def test_images_keep_originals_in_zip_and_all_frames_in_print(site, fmt, suffix):
    module, client, _ = site
    open_draft(module)
    data = image_bytes(fmt, frames=2)
    response = client.post("/api/user/upload_item/1", data={"type": "其他", "invoice_files": (io.BytesIO(data), "多页." + suffix)})
    assert response.status_code == 200, response.data
    item_id = response.json["item_id"]
    client.post(f"/api/user/item/1/{item_id}/amount", json={"amount": "18.88"})
    client.post("/api/user/submit/1")
    row = invoices(client)[0]
    pid = project(client)
    assert client.post("/api/admin/allocate", json={"invoice_ids": [row["id"]], "project_id": pid}).status_code == 200
    printed = client.post(f"/admin/projects/{pid}/print", json={"file_ids": [row["id"]]})
    assert printed.status_code == 200, printed.data
    assert len(PdfReader(io.BytesIO(printed.data)).pages) == 2
    assert client.get(row["url"]).data == data
    response = client.get(f"/admin/projects/{pid}/download")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.data)) as bundle:
        original = next(name for name in bundle.namelist() if name.endswith("." + suffix))
        assert bundle.read(original) == data


def test_closing_and_reopening_retains_invoices_allocations_and_files(site, legacy):
    _, client, _ = site
    pid = project(client)
    before = invoices(client)
    client.post("/api/admin/allocate", json={"invoice_ids": [before[0]["id"]], "project_id": pid})
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in legacy[0].iterdir()}
    assert client.post("/api/admin/window/1/complete").status_code == 200
    closed = invoices(client)
    assert len(closed) == 2
    assert all(row["task_completed"] for row in closed)
    assert client.get("/admin/download/1").status_code == 200
    assert client.get("/api/admin/workbench").json["projects"][0]["total_cents"] == 5001
    assert client.post("/api/admin/window/1/uncomplete").status_code == 200
    reopened = invoices(client)
    assert all(not row["task_completed"] for row in reopened)
    assert {row["id"] for row in before} == {row["id"] for row in reopened}
    assert next(row for row in reopened if row["id"] == before[0]["id"])["project_id"] == pid
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in legacy[0].iterdir()} == hashes
