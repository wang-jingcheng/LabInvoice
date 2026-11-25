"""Funding lifecycle, saved input history and removed filters on isolated data."""
import argparse
import os
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
from playwright.sync_api import sync_playwright,expect

parser=argparse.ArgumentParser();parser.add_argument('--base-url',required=True);args=parser.parse_args()
OUT=ROOT/'test-results' / 'browser' / 'project-editing';OUT.mkdir(parents=True,exist_ok=True)


def ready(page):page.wait_for_load_state('networkidle')


with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
    context=browser.new_context(viewport={'width':1440,'height':1000})
    page=context.new_page();errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.on('console',lambda message:errors.append(message.text) if message.type=='error' else None)
    page.goto(args.base_url+'/admin/login');ready(page)
    page.locator('[name=password]').fill('preview-admin-2026')
    page.get_by_role('button',name='进入管理工作台',exact=True).click();ready(page)
    page.wait_for_function('admin.data && !admin.data.loading')
    expect(page.locator('#statusFilter')).to_have_count(0)
    page.evaluate("async()=>{await api('/api/admin/window/1/uncomplete',{method:'POST'});await loadAdmin();}")
    page.evaluate('admin.data.invoices[0].legacy_allocated=true;renderTable()')
    expect(page.get_by_text('历史合计分摊 · 待核对',exact=True)).to_have_count(0)
    page.locator('[data-action=invoice-detail]').first.click()
    expect(page.get_by_role('dialog')).not_to_contain_text('历史批次只有合计')
    page.get_by_role('button',name='关闭窗口',exact=True).click()
    page.locator('.heading-actions [data-action=new-project]').click()
    page.locator('#project-name').fill('浏览器经费项目')
    page.locator('#project-code').fill('V24-001')
    page.get_by_role('button',name='创建项目',exact=True).click()
    expect(page.locator('#projectBanner')).to_contain_text('浏览器经费项目')
    pid=page.evaluate('admin.projectId')
    card=page.locator(f'.project-card[data-drop-project="{pid}"]')
    card.locator('[data-action=new-category]').click()
    page.get_by_role('dialog').locator('[name=name]').fill('临时报销项')
    page.get_by_role('button',name='创建报销项',exact=True).click()
    expect(card).to_contain_text('临时报销项')
    card.locator('[data-action=rename-category]').click()
    page.get_by_role('dialog').locator('[name=name]').fill('设备耗材费')
    page.get_by_role('button',name='保存名称',exact=True).click()
    expect(card).to_contain_text('设备耗材费')
    cid=page.evaluate(f'projectFor({pid}).categories[0].id')
    # History selection fills the name and code as a pair, using mouse and keyboard.
    page.locator('.heading-actions [data-action=new-project]').click()
    page.locator('#project-name').fill('浏览器')
    expect(page.locator('#history-name')).to_be_visible()
    page.locator('#history-name [data-history-pick]').first.click()
    expect(page.locator('#project-name')).to_have_value('浏览器经费项目')
    expect(page.locator('#project-code')).to_have_value('V24-001')
    page.locator('#project-code').fill('V24')
    page.locator('#project-code').press('ArrowDown');page.locator('#project-code').press('Enter')
    expect(page.locator('#project-code')).to_have_value('V24-001')
    page.get_by_role('button',name='管理历史记录',exact=True).click()
    page.locator('[data-action=edit-project-history]').first.click()
    page.locator('#project-name').fill('修改后的历史候选')
    page.locator('#project-code').fill('HIST-002')
    page.get_by_role('button',name='保存历史记录',exact=True).click()
    expect(page.locator('.history-record')).to_contain_text('HIST-002')
    assert page.evaluate(f'projectFor({pid}).name')=='浏览器经费项目'
    page.get_by_role('button',name='关闭',exact=True).click()
    page.locator('.heading-actions [data-action=new-project]').click()
    page.locator('#project-code').fill('HIST')
    expect(page.locator('#history-code')).to_contain_text('修改后的历史候选')
    for width,height in [(1440,1000),(768,1024),(390,844)]:
        page.set_viewport_size({'width':width,'height':height})
        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth'),width
        assert page.get_by_role('dialog').evaluate('e=>e.scrollWidth<=e.clientWidth'),width
        page.screenshot(path=str(OUT/f'project-history-{width}.png'),full_page=True)
    page.set_viewport_size({'width':1440,'height':1000})
    page.get_by_role('button',name='管理历史记录',exact=True).click()
    page.locator('[data-action=delete-project-history]').first.click()
    page.get_by_role('button',name='取消',exact=True).click()
    assert len(page.evaluate("api('/api/admin/project-history')"))==1
    # Cancellation leaves history unchanged; delete only the suggestion.
    page.locator('.heading-actions [data-action=new-project]').click()
    page.get_by_role('button',name='管理历史记录',exact=True).click()
    page.locator('[data-action=delete-project-history]').first.click()
    page.get_by_role('button',name='删除历史记录',exact=True).click()
    expect(page.locator('.history-record')).to_have_count(0)
    assert page.evaluate(f'projectFor({pid}).code')=='V24-001'
    page.get_by_role('button',name='关闭',exact=True).click()
    # Nonempty category deletion returns its invoice without losing calibration.
    page.locator('#showAll').click()
    row=page.locator('#invoiceRows [data-invoice]').first
    invoice_id=row.get_attribute('data-invoice')
    row.locator('[data-select-invoice]').check()
    page.locator('#targetProject').select_option(str(pid));page.locator('#targetCategory').select_option(str(cid))
    page.locator('[data-action=allocate-selected]').click()
    expect(page.locator('#bulkBar')).not_to_be_visible()
    page.locator(f'[data-invoice="{invoice_id}"] [data-action=invoice-detail]').click()
    page.get_by_role('dialog').locator('[name=amount]').fill('99.99')
    page.get_by_role('button',name='保存修改',exact=True).click()
    expect(page.locator(f'[data-invoice="{invoice_id}"] .amount')).to_have_text('99.99')
    card.locator('[data-action=rename-category]').click()
    page.get_by_role('button',name='删除报销项',exact=True).click()
    page.get_by_role('button',name='取消',exact=True).click()
    assert page.evaluate(f'invoiceFor("{invoice_id}").project_id')==pid
    card.locator('[data-action=rename-category]').click()
    page.get_by_role('button',name='删除报销项',exact=True).click()
    page.get_by_role('button',name='确认删除报销项',exact=True).click()
    expect(card.locator('[data-action=rename-category]')).to_have_count(0)
    row_data=page.evaluate(f'invoiceFor("{invoice_id}")')
    assert row_data['project_id'] is None and row_data['amount_cents']==9999
    # Nonempty project deletion, with a cancelled attempt first.
    page.locator(f'[data-invoice="{invoice_id}"] [data-select-invoice]').check()
    page.locator('#targetProject').select_option(str(pid))
    page.locator('[data-action=allocate-selected]').click()
    expect(page.locator('#bulkBar')).not_to_be_visible()
    card.locator('[data-action=edit-project]').click()
    page.locator('[data-action=delete-project]').click()
    page.get_by_role('button',name='取消',exact=True).click()
    assert page.evaluate(f'invoiceFor("{invoice_id}").project_id')==pid
    card.locator('[data-action=edit-project]').click()
    page.locator('[data-action=delete-project]').click()
    page.get_by_role('button',name='确认撤销项目',exact=True).click()
    expect(card).to_have_count(0)
    row_data=page.evaluate(f'invoiceFor("{invoice_id}")')
    assert row_data['project_id'] is None and row_data['amount_cents']==9999
    # Also exercise removal of a newly created empty project.
    page.locator('.heading-actions [data-action=new-project]').click()
    page.locator('#project-name').fill('空项目撤销测试')
    page.get_by_role('button',name='创建项目',exact=True).click()
    expect(page.locator('#projectBanner')).to_contain_text('空项目撤销测试')
    page.locator('#projectBanner [data-action=edit-project]').click()
    page.locator('[data-action=delete-project]').click()
    page.get_by_role('button',name='确认撤销项目',exact=True).click()
    expect(page.locator('#projectBanner')).to_be_empty()
    assert not errors,errors
    (OUT/'result.json').write_text(json.dumps({'passed':True,'page_errors':errors,'viewports':[1440,768,390],'invoice_amount_retained':9999,'history_mouse_keyboard':True,'cancel_is_noop':True},indent=2),encoding='utf-8')
    print('v2.4 browser checks passed')
    context.close();browser.close()
