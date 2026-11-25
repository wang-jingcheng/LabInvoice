"""Regression checks against the isolated preview server, never production data."""
import argparse
import os
import io
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
from PIL import Image
from playwright.sync_api import expect, sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument("--dialog-only", action="store_true")
parser.add_argument("--base-url", default="http://127.0.0.1:8791")
parser.add_argument("--old-dialog", action="store_true", help="Reproduce the previous modal close-event race")
args = parser.parse_args()
OUT = ROOT / "test-results" / 'browser' / 'responsive-dialogs'
OUT.mkdir(parents=True, exist_ok=True)


def ready(page):
    page.wait_for_load_state("networkidle")


with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True)
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    if args.old_dialog:
        source=(ROOT / "static" / "ui.js").read_text(encoding="utf-8")
        source=source.replace("if (dialogCleanup) { dialogCleanup(); dialogCleanup = null; }", "if (dialog.open) { if (dialogCleanup) { dialogCleanup(); dialogCleanup = null; } dialog.close(); }")
        page.route("**/static/ui.js*", lambda route: route.fulfill(content_type="application/javascript", body=source))
    page.set_default_timeout(7000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    base = args.base_url
    if not args.dialog_only:
        page.goto(base + "/login"); ready(page)
        page.screenshot(path=str(OUT / "login-blue.png"), full_page=True)
        page.locator('[name=username]').fill("demo")
        page.locator('[name=password]').fill("demo-only")
        page.get_by_role("button", name="进入工作台", exact=True).click(); ready(page)
        page.locator('input[name=type][value="专用材料费"]').check()
        stream = io.BytesIO()
        Image.new("RGB", (500, 700), "white").save(stream, format="PNG")
        page.locator('[data-picker=invoices]').set_input_files({"name": "付款订单截图.png", "mimeType": "image/png", "buffer": stream.getvalue()})
        expect(page.locator('#picked-invoices li')).to_have_count(1)
        page.locator('#saveDraftBtn').click()
        expect(page.get_by_role("dialog")).not_to_be_visible()
        expect(page.locator('.draft-item summary .amount')).to_have_text("¥ —")
        expect(page.locator('#memberTotal')).to_have_text("¥—")
        page.locator('#submitFinal').click()
        expect(page.locator('#toastRegion')).to_contain_text("填写")
        page.screenshot(path=str(OUT / "image-amount-pending.png"), full_page=True)
        page.locator('[data-file-amount]').fill("38.60")
        page.locator('[data-member-action=file-amount]').click()
        expect(page.locator('.draft-item summary .amount')).to_have_text("¥ 38.60")
        for width, height in [(390, 844), (768, 1024)]:
            page.set_viewport_size({"width": width, "height": height})
            assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), f"member overflow {width}"
            page.screenshot(path=str(OUT / f"member-{width}.png"), full_page=True)
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.locator('#submitFinal').click()
        page.get_by_role("button", name="确认提交", exact=True).click()
        expect(page.locator('.task-desk-head')).to_contain_text("已提交")
    page.goto(base + "/admin/login"); ready(page)
    page.locator('[name=password]').fill("preview-admin-2026")
    page.get_by_role("button", name="进入管理工作台", exact=True).click(); ready(page)
    if not args.dialog_only:
        # September's seeded task is closed, with both assigned and unassigned invoices.
        expect(page.locator('#invoiceRows [data-invoice]')).to_have_count(1)
        expect(page.locator('#taskFilter option[value="1"]')).to_have_count(0)
        page.screenshot(path=str(OUT / "workbench-blue.png"), full_page=True)
    page.locator('[data-nav=tasks]').click()
    page.locator('[data-action=task-detail][data-id="2"]').click()
    page.locator('[data-task-state=close]').click()
    page.locator('#confirmAction').click()
    expect(page.get_by_role('dialog')).not_to_be_visible()
    page.locator('[data-action=task-detail][data-id="2"]').click()
    page.locator('[data-task-state=complete]').click()
    expect(page.locator('#confirmAction')).to_be_visible()
    ready(page)
    page.locator('#confirmAction').click()
    expect(page.get_by_role("dialog")).not_to_be_visible()
    expect(page.locator('.task-card').filter(has=page.locator('[data-action=task-detail][data-id="2"]'))).to_contain_text("已结项")
    if not args.dialog_only:
        page.locator('[data-nav=invoices]').click()
        expect(page.locator('#invoiceRows [data-invoice]')).to_have_count(0)
        expect(page.locator('#collectionTotal')).to_have_text("¥0.00")
        page.locator('[data-nav=tasks]').click()
        page.locator('[data-action=task-detail][data-id="2"]').click()
        page.locator('[data-task-state=uncomplete]').click()
        page.get_by_role("button", name="取消", exact=True).click()
        page.get_by_role("dialog").wait_for(state="hidden")
        # Cancellation must leave the task closed.
        page.locator('[data-action=task-detail][data-id="2"]').click()
        expect(page.locator('[data-task-state=uncomplete]')).to_have_text("撤销结项")
        page.locator('[data-task-state=uncomplete]').click()
        page.locator('#confirmAction').click()
        expect(page.get_by_role("dialog")).not_to_be_visible()
        # Reopen September and verify that all twelve original invoices return.
        page.locator('[data-action=task-detail][data-id="1"]').click()
        page.locator('[data-task-state=uncomplete]').click()
        page.locator('#confirmAction').click()
        expect(page.get_by_role("dialog")).not_to_be_visible()
        page.locator('[data-nav=invoices]').click()
        expect(page.locator('#invoiceRows [data-invoice]')).to_have_count(13)
        for width, height in [(1440, 1000), (768, 1024), (390, 844)]:
            page.set_viewport_size({"width": width, "height": height})
            assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), f"admin overflow {width}"
            page.evaluate("document.querySelector('#toastRegion').replaceChildren(); window.scrollTo(0,0)")
            page.screenshot(path=str(OUT / f"workbench-{width}.png"), full_page=True)
        page.set_viewport_size({"width": 1440, "height": 1000})
        metrics = page.locator('.filename').first.evaluate("e => ({size: getComputedStyle(e).fontSize, color: getComputedStyle(e).color})")
        assert float(metrics["size"].removesuffix("px")) >= 14, metrics
    assert not errors, errors
    (OUT / "result.json").write_text(json.dumps({"passed": True, "page_errors": errors}, indent=2), encoding="utf-8")
    print("Browser regression checks passed")
    context.close()
    browser.close()
