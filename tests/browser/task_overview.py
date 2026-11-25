"""Multi-task finance overview, category folding and independent task lifecycle."""
import argparse
import os
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
from playwright.sync_api import sync_playwright,expect
from preview_server import app,get_db,DEMO_DIR,pdf
from storage import atomic_json

parser=argparse.ArgumentParser();parser.add_argument('--base-url',required=True);args=parser.parse_args()
OUT=ROOT/'test-results' / 'browser' / 'task-overview';OUT.mkdir(parents=True,exist_ok=True)
# Extend only the unique demo dataset created by run_browser_checks.
with app.app_context():
    db=get_db()
    db.execute('UPDATE windows SET is_completed=0,is_collection_closed=0 WHERE id=1')
    db.execute('UPDATE assignments SET status=1 WHERE window_id=1 AND user_id=6')
    db.execute("INSERT INTO windows(id,title,task_type) VALUES(3,'2026年9-10月国内差旅报销','国内差旅费')")
    db.execute('INSERT INTO assignments(window_id,user_id,status) VALUES(3,1,2)')
    db.commit()
many=DEMO_DIR/'uploads/task_1/林晓/item_pagination';many.mkdir(parents=True)
names=[f'主发票_分页验收_{i:02}.pdf' for i in range(33)]
for name in names:(many/name).write_bytes(pdf(1))
atomic_json(many/'meta.json',{'type':'专用材料费','invoice_files':names,'invoice_amounts':{n:1 for n in names},'attachments':[],'auto_amount':33})
travel=DEMO_DIR/'uploads/task_3/林晓/item_travel';travel.mkdir(parents=True)
name='主发票_差旅验收.pdf';(travel/name).write_bytes(pdf(120))
atomic_json(travel/'meta.json',{'type':'交通费','invoice_files':[name],'invoice_amounts':{name:120},'attachments':[],'auto_amount':120})


def ready(page):page.wait_for_load_state('networkidle')


with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
    context=browser.new_context(viewport={'width':1600,'height':1080})
    page=context.new_page();errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.on('console',lambda m:errors.append(m.text) if m.type=='error' else None)
    page.goto(args.base_url+'/admin/login');ready(page)
    page.locator('[name=password]').fill('preview-admin-2026')
    page.get_by_role('button',name='进入管理工作台',exact=True).click();ready(page)
    page.wait_for_function('admin.data && !admin.data.loading')
    expect(page.locator('#collectionTotal')).to_have_text('¥2,238.50')
    expect(page.locator('#reimbursementTotal')).to_have_text('¥1,196.30')
    expect(page.locator('[data-task-summary]')).to_have_count(3)
    empty=page.locator('[data-task-summary="2"]')
    expect(empty.locator('dd')).to_have_count(6)
    assert all(v=='0.00' for v in empty.locator('dd').all_text_contents())
    widths=page.locator('.collection-overview').evaluate('e=>Array.from(e.children).map(x=>x.getBoundingClientRect().width)')
    assert widths[0]>widths[1]*1.8,widths
    for width,height in [(1600,1080),(1024,1000),(768,1024),(390,844)]:
        page.set_viewport_size({'width':width,'height':height})
        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth'),width
        page.evaluate('window.scrollTo(0,0)')
        page.screenshot(path=str(OUT/f'overview-{width}.png'),full_page=True)
        page.locator('.collection-overview').screenshot(path=str(OUT/f'amount-modules-{width}.png'))
    page.set_viewport_size({'width':1600,'height':1080})
    member_context=browser.new_context(viewport={'width':1280,'height':900})
    member_page=member_context.new_page();member_page.on('pageerror',lambda e:errors.append(str(e)))
    member_page.goto(args.base_url+'/login');ready(member_page)
    member_page.locator('[name=username]').fill('member6');member_page.locator('[name=password]').fill('demo-only')
    member_page.get_by_role('button',name='进入工作台',exact=True).click();ready(member_page)
    member_page.locator('[data-task="1"]').click()
    expect(member_page.locator('#uploadForm')).to_be_visible()
    page.locator('#taskFilter').select_option('3')
    expect(page.locator('#collectionTotal')).to_have_text('¥120.00')
    expect(page.locator('#reimbursementTotal')).to_have_text('¥0.00')
    expect(page.locator('[data-task-summary]')).to_have_count(1)
    expect(page.locator('[data-task-summary="3"] dd')).to_have_count(3)
    expect(page.locator('#invoiceRows [data-invoice]')).to_have_count(1)
    page.locator('#showUnassigned').click()
    expect(page.locator('#taskFilter')).to_have_value('3')
    page.locator('#showAll').click()
    expect(page.locator('#taskFilter')).to_have_value('3')
    # Folding applies to complete categories even when more than one page long.
    page.locator('#taskFilter').select_option('1')
    expect(page.locator('#invoiceRows [data-invoice]')).to_have_count(30)
    total=page.locator('#filteredTotal').inner_text()
    page.locator('[data-action=collapse-categories]').click()
    expect(page.locator('#invoiceRows [data-invoice]')).to_have_count(0)
    # This task has five populated categories; the sixth zero category appears
    # in its finance summary rather than as an empty invoice group.
    assert page.locator('[data-action=toggle-category-fold]').count()==5
    assert all(x=='false' for x in page.locator('[data-action=toggle-category-fold]').evaluate_all('els=>els.map(e=>e.getAttribute("aria-expanded"))'))
    page.locator('[data-select-group="专用材料费"]').check()
    assert page.evaluate('admin.selected.size')==37
    expect(page.locator('#filteredTotal')).to_have_text(total)
    page.locator('[data-action=clear-selection]').click()
    page.locator('[data-action=toggle-category-fold][data-category="市内交通费"]').click()
    expect(page.locator('#invoiceRows [data-invoice]')).to_have_count(2)
    page.locator('[data-action=expand-categories]').click()
    expect(page.locator('#invoiceRows [data-invoice]')).to_have_count(30)
    page.locator('[data-action=page-next]').click()
    expect(page.locator('#invoiceRows [data-invoice]')).to_have_count(15)
    page.locator('[data-action=collapse-categories]').click()
    expect(page.locator('#invoiceRows [data-invoice]')).to_have_count(0)
    page.locator('[data-action=expand-categories]').click()
    expect(page.locator('#invoiceRows [data-invoice]')).to_have_count(30)
    # Renaming keeps row identity and refreshes every task label.
    original_ids=page.evaluate('admin.data.invoices.filter(r=>r.task_id===1).map(r=>r.id)')
    page.locator('[data-nav=tasks]').click()
    page.locator('[data-action=task-detail][data-id="1"]').click()
    page.locator('[data-action=rename-task]').click()
    page.get_by_role('dialog').locator('[name=title]').fill('2026年日常报销业务 · 已重命名')
    page.get_by_role('button',name='保存任务名称',exact=True).click()
    expect(page.locator('.task-card').filter(has=page.locator('[data-action=task-detail][data-id="1"]'))).to_contain_text('已重命名')
    assert page.evaluate('admin.data.invoices.filter(r=>r.task_id===1).map(r=>r.id)')==original_ids
    # End collection retains the workbench. Completion is a separate operation.
    page.locator('[data-action=task-detail][data-id="1"]').click()
    expect(page.get_by_role('dialog').locator('[data-task-state=complete]')).to_have_count(0)
    page.locator('[data-task-state=close]').click();page.locator('#confirmAction').click()
    expect(page.get_by_role('dialog')).not_to_be_visible()
    member_page.reload();ready(member_page);member_page.locator('[data-task="1"]').click()
    expect(member_page.locator('#uploadForm')).to_have_count(0)
    expect(member_page.locator('#memberDesk .alert')).to_contain_text('尚未结项')
    page.locator('[data-nav=invoices]').click()
    expect(page.locator('#invoiceRows [data-invoice]')).to_have_count(30)
    expect(page.locator('#collectionTotal')).to_have_text('¥2,118.50')
    page.locator('[data-nav=tasks]').click();page.locator('[data-action=task-detail][data-id="1"]').click()
    expect(page.get_by_role('dialog')).to_contain_text('收集已结束 · 待结项')
    page.locator('[data-task-state=complete]').click();page.locator('#confirmAction').click()
    expect(page.get_by_role('dialog')).not_to_be_visible()
    page.locator('[data-nav=invoices]').click()
    expect(page.locator('#taskFilter option[value="1"]')).to_have_count(0)
    expect(page.locator('#invoiceRows [data-invoice]')).to_have_count(1)
    expect(page.locator('#collectionTotal')).to_have_text('¥120.00')
    # Undo completion returns to closed collection, then reopening permits edits.
    page.locator('[data-nav=tasks]').click();page.locator('[data-action=task-detail][data-id="1"]').click()
    page.locator('[data-task-state=uncomplete]').click();page.locator('#confirmAction').click()
    expect(page.get_by_role('dialog')).not_to_be_visible()
    page.locator('[data-action=task-detail][data-id="1"]').click()
    expect(page.locator('[data-task-state=reopen]')).to_be_visible()
    page.locator('[data-task-state=reopen]').click();page.locator('#confirmAction').click()
    expect(page.get_by_role('dialog')).not_to_be_visible()
    page.locator('[data-nav=invoices]').click()
    page.locator('#clearFilters').click()
    expect(page.locator('#collectionTotal')).to_have_text('¥2,238.50')
    member_page.reload();ready(member_page);member_page.locator('[data-task="1"]').click()
    expect(member_page.locator('#uploadForm')).to_be_visible()
    assert not errors,errors
    (OUT/'result.json').write_text(json.dumps({'passed':True,'page_errors':errors,'submitted_only':True,'folding_across_pages':True,'task_lifecycle':True,'rename_keeps_ids':True,'viewports':[1600,1024,768,390]},indent=2),encoding='utf-8')
    print('v2.6 browser checks passed')
    member_context.close();context.close();browser.close()
