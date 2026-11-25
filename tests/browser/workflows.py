"""Real-browser acceptance checks; uses only the explicitly isolated demo server.

Run via scripts/run_browser_checks.py. Artifacts go to test-results/.
"""
import argparse
import os
import io
import json
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
from playwright.sync_api import expect, sync_playwright
from pypdf import PdfReader
from preview_server import FIXTURE_DIR as FIX

parser = argparse.ArgumentParser()
parser.add_argument('--browser', default=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'))
parser.add_argument('--base-url', default='http://127.0.0.1:8791')
parser.add_argument('--inspect-only', action='store_true')
args = parser.parse_args()
OUT = ROOT / 'test-results' / 'browser' / 'workflows'
OUT.mkdir(parents=True, exist_ok=True)


def overflow(page):
    return page.evaluate('document.documentElement.scrollWidth > innerWidth')


def ready(page):
    page.wait_for_load_state('networkidle')


def check_centered_card(page):
    card = page.locator('.auth-card').bounding_box()
    viewport = page.viewport_size
    assert card is not None and viewport is not None
    assert abs(card['x'] + card['width'] / 2 - viewport['width'] / 2) < 2, card
    assert abs(card['y'] + card['height'] / 2 - viewport['height'] / 2) < 2, card


with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=args.browser, headless=True, timeout=20000)
    context = browser.new_context(viewport={'width':1440,'height':1000},device_scale_factor=1,accept_downloads=True)
    page = context.new_page()
    page.set_default_timeout(12000)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('console', lambda message: errors.append(message.text) if message.type=='error' else None)
    page.goto(args.base_url+'/login'); ready(page)
    expect(page.get_by_role('link', name='使用说明', exact=True)).to_have_attribute('href','/help')
    expect(page.locator('.brand-badge')).to_have_text(re.compile(r'^v\d+\.\d+(?:\.\d+)?$'))
    check_centered_card(page)
    page.screenshot(path=str(OUT/'login-desktop.png'),full_page=True)
    page.set_viewport_size({'width':390,'height':844})
    assert not overflow(page)
    check_centered_card(page)
    page.screenshot(path=str(OUT/'login-mobile.png'),full_page=True)
    page.set_viewport_size({'width':1440,'height':1000})
    if not args.inspect_only:
        page.locator('[name=username]').fill('demo')
        page.locator('[name=password]').fill('demo-only')
        page.get_by_role('button',name='进入工作台',exact=True).click();ready(page)
        expect(page.get_by_role('navigation', name='成员导航')).to_be_visible()
        expect(page.get_by_role('button', name='修改密码')).to_be_visible()
        expect(page.locator('#memberTasks')).to_contain_text('2026 年 10 月')
        page.locator('input[name=type][value="专用材料费"]').check()
        page.locator('[data-picker=invoices]').set_input_files(str(FIX/'browser-first.pdf'))
        page.locator('[data-picker=invoices]').set_input_files(str(FIX/'browser-second.pdf'))
        expect(page.locator('#picked-invoices li')).to_have_count(2)
        page.get_by_role('button',name='移除 browser-first.pdf',exact=True).click()
        expect(page.locator('#picked-invoices li')).to_have_count(1)
        (FIX/'support.docx').write_bytes(b'Browser test fixture')
        page.locator('[data-picker=attachments]').set_input_files([str(FIX/'browser-attachment.pdf'),str(FIX/'support.docx')])
        expect(page.locator('#picked-attachments li')).to_have_count(2)
        page.get_by_role('button',name='移除 support.docx',exact=True).click()
        page.screenshot(path=str(OUT/'member-upload.png'),full_page=True)
        page.locator('#saveDraftBtn').click()
        expect(page.locator('.draft-item')).to_have_count(1)
        expect(page.get_by_role('dialog')).not_to_be_visible()
        expect(page.locator('[data-file-amount]')).to_have_value('46.80')
        page.locator('[data-file-amount]').fill('55.55')
        page.locator('[data-member-action=file-amount]').click()
        expect(page.locator('.draft-item summary .amount')).to_have_text('¥ 55.55')
        page.locator('[data-picker=invoices]').set_input_files(str(FIX/'browser-extra.pdf'))
        page.locator('#saveDraftBtn').click()
        expect(page.locator('.draft-file-link a',has_text='browser-extra')).to_have_count(1)
        expect(page.locator('.draft-item')).to_have_count(1)
        page.locator('[data-member-action=remove-saved][data-file*="browser-extra"]').click()
        page.get_by_role('button',name='确认移除',exact=True).click()
        expect(page.locator('.draft-file-link a',has_text='browser-extra')).to_have_count(0)
        expect(page.locator('.draft-item summary .amount')).to_have_text('¥ 55.55')
        page.locator('[data-picker=attachments]').set_input_files(str(FIX/'support.docx'))
        page.locator('#saveDraftBtn').click()
        expect(page.locator('.draft-file-link a',has_text='support.docx')).to_have_count(1)
        page.screenshot(path=str(OUT/'member-drafts.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':844})
        assert not overflow(page)
        page.screenshot(path=str(OUT/'member-mobile.png'),full_page=True)
        page.set_viewport_size({'width':1440,'height':1000})
        page.locator('#submitFinal').click()
        page.get_by_role('button',name='确认提交',exact=True).click()
        expect(page.locator('.task-desk-head')).to_contain_text('已提交')
    page.goto(args.base_url+'/admin/login')
    check_centered_card(page)
    page.locator('[name=password]').fill('preview-admin-2026')
    page.get_by_role('button',name='进入管理工作台',exact=True).click();ready(page)
    expect(page.locator('#invoiceRows [data-invoice]').first).to_be_visible()
    expect(page.get_by_role('navigation', name='管理员导航')).to_be_visible()
    expect(page.locator('#adminNav [data-nav=invoices]')).to_have_attribute('aria-current', 'page')
    assert not overflow(page)
    page.screenshot(path=str(OUT/'admin-desktop.png'),full_page=True)
    if not args.inspect_only:
        page.locator('.heading-actions [data-action=new-project]').click()
        page.get_by_role('dialog').locator('[name=name]').fill('项目 C · 专项报销')
        page.get_by_role('dialog').locator('[name=code]').fill('TEST-C-2026')
        page.get_by_role('button',name='创建项目',exact=True).click()
        expect(page.locator('#projectBanner')).to_contain_text('项目 C')
        page.locator('#showAll').click()
        page.locator('#taskFilter').select_option('2')
        expect(page.locator('#invoiceRows [data-invoice]')).to_have_count(1)
        page.locator('#selectAllInvoices').check()
        option=page.locator('#targetProject option').filter(has_text='项目 C')
        pid=option.get_attribute('value')
        page.locator('#targetProject').select_option(pid)
        page.locator('[data-action=allocate-selected]').click()
        expect(page.locator('#bulkBar')).not_to_be_visible()
        project_card=page.locator(f'.project-card[data-drop-project="{pid}"]')
        expect(project_card).to_contain_text('专用材料费')
        project_card.locator('[data-action=new-category]').click()
        page.get_by_role('dialog').locator('[name=name]').fill('设备配件费')
        page.get_by_role('button',name='创建报销项',exact=True).click()
        expect(project_card).to_contain_text('设备配件费')
        page.locator('#selectAllInvoices').check()
        page.locator('#targetProject').select_option(pid)
        cid=page.locator('#targetCategory option').filter(has_text='设备配件费').get_attribute('value')
        page.locator('#targetCategory').select_option(cid)
        page.locator('[data-action=allocate-selected]').click()
        expect(page.locator('#invoiceRows .group-row')).to_contain_text('设备配件费')
        # Exercise the actual drag/drop path, then verify the server result.
        page.locator('#invoiceRows [data-invoice]').drag_to(project_card.locator('[data-action=open-category]').filter(has_text='专用材料费'))
        expect(page.locator('#invoiceRows .group-row')).to_contain_text('专用材料费')
        project_card.locator('[data-action=open-project]').click()
        page.locator('#projectBanner [data-action=project-summary]').click()
        expect(page.get_by_role('dialog')).to_contain_text('55.55')
        expect(page.get_by_role('dialog')).to_contain_text('林晓')
        page.get_by_role('button',name='关闭窗口').click()
        with page.expect_download() as download_info:
            page.locator('#projectBanner [data-action=download-project]').click()
        archive_path=OUT/'browser-project.zip'
        download_info.value.save_as(str(archive_path))
        with zipfile.ZipFile(archive_path) as archive:
            assert sum('主发票_' in name for name in archive.namelist())==1
            assert '55.55' in archive.read('报销汇总.csv').decode('utf-8-sig')
        page.locator('#projectBanner [data-action=print-project]').click()
        expect(page.locator('input[name=file_ids]:checked')).to_have_count(1)
        expect(page.locator('input[name=file_ids]:disabled')).to_have_count(1)
        page.locator('#selectPrintAttachments').check()
        expect(page.locator('input[name=file_ids]:checked')).to_have_count(2)
        with page.expect_response(lambda response:'/admin/projects/' in response.url and response.url.endswith('/print') and response.request.method=='POST') as response_info:
            page.get_by_role('button',name='生成合并 PDF',exact=True).click()
        response=response_info.value
        assert response.status==200
        expect(page.get_by_role('dialog')).to_contain_text('合并 PDF 已生成')
        expect(page.locator('.pdf-frame')).to_be_visible()
        # Validate the exact Blob exposed to the user. Chromium's PDF plugin
        # may consume the protocol response body before Playwright reads it.
        blob_url=page.locator('.pdf-frame').get_attribute('src')
        merged=bytes(page.evaluate('async url => Array.from(new Uint8Array(await (await fetch(url)).arrayBuffer()))',blob_url))
        assert len(PdfReader(io.BytesIO(merged)).pages)==2
        (OUT/'browser-project-print.pdf').write_bytes(merged)
        page.screenshot(path=str(OUT/'project-print.png'),full_page=True)
        page.get_by_role('button',name='关闭窗口').click()
        page.locator('[data-nav=tasks]').click()
        expect(page.locator('#adminNav [data-nav=tasks]')).to_have_attribute('aria-current', 'page')
        expect(page.locator('.task-card')).to_have_count(2)
        page.locator('[data-action=task-detail][data-id="2"]').click()
        expect(page.get_by_role('dialog')).to_contain_text('林晓')
        page.get_by_role('button',name='关闭窗口').click()
        page.locator('[data-nav=guide]').click()
        expect(page.locator('#guideEditor')).to_be_visible()
        page.locator('#guideEditor').fill('实验室使用说明\n浏览器验证：请先核对金额，再正式提交。\n<script>unsafe()</script>')
        page.locator('[data-action=save-guide]').click()
        expect(page.locator('#guideSaveState')).to_contain_text('已保存')
        page.goto(args.base_url+'/help');ready(page)
        expect(page.locator('.guide-content')).to_contain_text('<script>unsafe()</script>')
        assert page.locator('.guide-content script').count()==0
        page.goto(args.base_url+'/admin');ready(page)
    page.set_viewport_size({'width':390,'height':844})
    assert not overflow(page)
    page.screenshot(path=str(OUT/'admin-mobile.png'),full_page=True)
    page.set_viewport_size({'width':768,'height':1024})
    assert not overflow(page)
    page.screenshot(path=str(OUT/'admin-tablet.png'),full_page=True)
    assert not errors,errors
    (OUT/'browser-check.json').write_text(json.dumps({'passed':True,'mode':'inspect' if args.inspect_only else 'full','console_errors':errors,'viewports':[1440,768,390]},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':True,'console_errors':errors,'artifacts':str(OUT)},ensure_ascii=False))
    context.close();browser.close()
