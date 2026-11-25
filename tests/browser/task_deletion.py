"""Task removal with recoverable archive and member status colors."""
import argparse
import os
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
from playwright.sync_api import sync_playwright,expect

parser=argparse.ArgumentParser();parser.add_argument('--base-url',required=True);args=parser.parse_args()
OUT=ROOT/'test-results' / 'browser' / 'task-deletion';OUT.mkdir(parents=True,exist_ok=True)

with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
    context=browser.new_context(viewport={'width':1440,'height':1000})
    page=context.new_page();errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(args.base_url+'/admin/login');page.wait_for_load_state('networkidle')
    page.locator('[name=password]').fill('preview-admin-2026')
    page.get_by_role('button',name='进入管理工作台',exact=True).click();page.wait_for_load_state('networkidle')
    page.wait_for_function('admin.data && !admin.data.loading')
    # Return one member to draft through the real API. Another task has all
    # pending members; the completed task retains five completed members.
    page.evaluate("async()=>{await api('/api/admin/window/1/revert/6',{method:'POST'});await loadAdmin();}")
    page.locator('[data-nav=tasks]').click()
    page.locator('[data-action=task-detail][data-id="1"]').click()
    expect(page.get_by_text('草稿中',exact=True)).to_have_count(1)
    expect(page.get_by_text('已完成',exact=True)).to_have_count(5)
    status_colors = {
        page.locator('.submission-draft').evaluate('tag => getComputedStyle(tag).backgroundColor'),
        page.locator('.submission-completed').first.evaluate('tag => getComputedStyle(tag).backgroundColor'),
    }
    assert len(status_colors) == 2
    for width,height in [(1440,1000),(390,844)]:
        page.set_viewport_size({'width':width,'height':height})
        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
        page.screenshot(path=str(OUT/f'task-status-{width}.png'),full_page=True)
    page.set_viewport_size({'width':1440,'height':1000})
    page.get_by_role('button',name='删除收集任务',exact=True).click()
    page.get_by_role('button',name='取消',exact=True).click()
    assert page.evaluate('admin.data.windows.some(w=>w.id===1)') is True
    page.locator('[data-action=task-detail][data-id="1"]').click()
    page.get_by_role('button',name='删除收集任务',exact=True).click()
    with page.expect_response(lambda r:r.url.endswith('/api/admin/window/delete/1')) as response_info:
        page.get_by_role('button',name='确认删除任务',exact=True).click()
    response=response_info.value
    assert response.ok
    archive=response.json()['archive']
    page.wait_for_function('!admin.data.windows.some(w=>w.id===1)')
    expect(page.locator('[data-action=task-detail][data-id="1"]')).to_have_count(0)
    assert page.evaluate('admin.data.invoices.length')==0
    assert page.evaluate('admin.data.projects.every(p=>p.total_cents===0)') is True
    expect(page.get_by_role('dialog')).not_to_be_visible()
    page.locator('[data-action=task-detail][data-id="2"]').click()
    expect(page.get_by_text('待填写',exact=True)).to_have_count(6)
    pending_color = page.get_by_role('dialog').locator('tbody .tag.gray').first.evaluate('tag => getComputedStyle(tag).backgroundColor')
    assert pending_color not in status_colors
    page.get_by_role('button',name='删除收集任务',exact=True).click()
    page.get_by_role('button',name='确认删除任务',exact=True).click()
    expect(page.locator('[data-action=task-detail]')).to_have_count(0)
    # Check the archive on the isolated server's own filesystem.
    from preview_server import DEMO_DIR
    snapshot=json.loads((DEMO_DIR/archive/'task-records.json').read_text(encoding='utf-8'))
    assert len(snapshot['assignments'])==6 and len(snapshot['allocations'])==4
    assert len(list((DEMO_DIR/archive/'task_1').glob('*/item_*/meta.json')))==12
    assert not errors,errors
    (OUT/'result.json').write_text(json.dumps({'passed':True,'page_errors':errors,'colors_verified':True,'cancel_is_noop':True,'archived_records':12},indent=2),encoding='utf-8')
    print('v2.5 browser checks passed')
    context.close();browser.close()
