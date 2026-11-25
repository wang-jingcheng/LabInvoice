"""Independent project scrolling, invoice correction and no-invoice reimbursements."""
import argparse
import os
import io
import json
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
from playwright.sync_api import sync_playwright,expect

parser=argparse.ArgumentParser();parser.add_argument('--base-url',required=True);args=parser.parse_args()
OUT=ROOT/'test-results'/'browser'/'manual-expenses';OUT.mkdir(parents=True,exist_ok=True)


def ready(page):page.wait_for_load_state('networkidle')


with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
    context=browser.new_context(viewport={'width':1600,'height':1080},accept_downloads=True)
    page=context.new_page();errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.on('console',lambda m:errors.append(m.text) if m.type=='error' else None)
    page.goto(args.base_url+'/admin/login');ready(page)
    page.locator('[name=password]').fill('preview-admin-2026')
    page.get_by_role('button',name='进入管理工作台',exact=True).click();ready(page)
    page.wait_for_function('admin.data&&!admin.data.loading')
    page.evaluate("async()=>{await api('/api/admin/window/1/uncomplete',{method:'POST'});await api('/api/admin/users/add',{method:'POST',body:{username:'manual-member',real_name:'手工登记成员',password:'demo-only-7'}});for(let i=0;i<10;i++)await api('/api/admin/projects',{method:'POST',body:{name:'滚动验收项目 '+i,code:'SCROLL-'+i}});await loadAdmin();}")
    page.locator('#taskFilter').select_option('1')
    rail=page.locator('#projectList')
    assert rail.evaluate('e=>e.scrollHeight>e.clientHeight')
    rail.scroll_into_view_if_needed();rail.hover();outer=page.evaluate('scrollY');page.mouse.wheel(0,600)
    page.wait_for_function('document.querySelector("#projectList").scrollTop>0')
    assert page.evaluate('scrollY')==outer
    # Correct a pending invoice category independently of project allocation.
    pending_id=page.evaluate('admin.data.invoices.find(r=>r.item_id==="demo-003").id')
    row=page.locator(f'[data-invoice="{pending_id}"]')
    row.locator('[data-action=invoice-detail]').click()
    page.get_by_role('dialog').locator('[name=category]').fill('资料寄送费')
    page.get_by_role('dialog').locator('[name=amount]').fill('30.50')
    page.get_by_role('button',name='保存修改',exact=True).click()
    expect(page.locator(f'[data-invoice="{pending_id}"] .amount')).to_have_text('30.50')
    assert page.evaluate(f'invoiceFor("{pending_id}").category')=='资料寄送费'
    assigned_id=page.evaluate('admin.data.invoices.find(r=>r.item_id==="demo-001").id')
    page.locator(f'[data-invoice="{assigned_id}"] [data-action=invoice-detail]').click()
    page.get_by_role('dialog').locator('[name=category]').fill('实验交通费')
    page.get_by_role('dialog').locator('[name=amount]').fill('88.88')
    page.get_by_role('button',name='保存修改',exact=True).click()
    expect(page.locator('#collectionTotal')).to_have_text('¥2,349.08')
    expect(page.locator('#reimbursementTotal')).to_have_text('¥1,210.38')
    # Give a non-assigned member a manual reimbursement, without assigning a task.
    uid=page.evaluate('admin.data.users.find(u=>u.username==="manual-member").id')
    page.locator('[data-action=new-manual-expense]').click()
    dialog=page.get_by_role('dialog')
    dialog.locator('[name=task_id]').select_option('1');dialog.locator('[name=user_id]').select_option(str(uid))
    dialog.locator('[name=category]').fill('劳务费');dialog.locator('[name=amount]').fill('150.25')
    dialog.locator('[name=project_id]').select_option('2');dialog.locator('[name=remark]').fill('参与实验劳务说明')
    dialog.get_by_role('button',name='保存报销项',exact=True).click()
    expect(page.locator('[data-manual-expense]')).to_have_count(1)
    expect(page.locator('#collectionTotal')).to_have_text('¥2,349.08')
    expect(page.locator('#reimbursementTotal')).to_have_text('¥1,360.63')
    expect(page.locator('#collectionSummaryNote')).to_contain_text('150.25')
    assert not page.evaluate(f'admin.data.assignments.some(a=>a.window_id===1&&a.user_id==={uid})')
    expect(page.locator('#manualExpenseSection')).to_contain_text('无电子发票报销项')
    expect(page.get_by_text('无发票报销项',exact=True)).to_have_count(0)
    # Explicit assignment entry can move items in/out of funding projects.
    page.locator('[data-action=allocate-manual-expense]').click()
    dialog.locator('[name=project_id]').select_option('')
    dialog.get_by_role('button',name='保存归类',exact=True).click()
    expect(page.locator('#reimbursementTotal')).to_have_text('¥1,210.38')
    expect(page.locator('[data-action=allocate-manual-expense]')).to_have_text('归入项目')
    page.locator('[data-action=allocate-manual-expense]').click()
    dialog.locator('[name=project_id]').select_option('2')
    dialog.get_by_role('button',name='保存归类',exact=True).click()
    expect(page.locator('#reimbursementTotal')).to_have_text('¥1,360.63')
    # Drag to a project's reimbursement category; keep amount/note/ownership.
    transfer=page.evaluate_handle('new DataTransfer()')
    page.locator('[data-manual-expense]').dispatch_event('dragstart',{'dataTransfer':transfer})
    target_category=page.locator('.project-card[data-drop-project="1"] [data-drop-category="2"]')
    target_category.dispatch_event('dragover',{'dataTransfer':transfer})
    target_category.dispatch_event('drop',{'dataTransfer':transfer})
    page.wait_for_function('admin.data.manual_expenses[0].project_id===1&&admin.data.manual_expenses[0].category==="专用材料费"')
    assert page.evaluate('admin.data.manual_expenses[0].amount_cents')==15025
    assert page.evaluate('admin.data.manual_expenses[0].remark')=='参与实验劳务说明'
    page.locator('[data-action=allocate-manual-expense]').click()
    dialog.locator('[name=project_id]').select_option('2');dialog.locator('[name=category]').fill('劳务费')
    dialog.get_by_role('button',name='保存归类',exact=True).click()
    expect(page.locator('#reimbursementTotal')).to_have_text('¥1,360.63')
    # An unassigned member sees only their own administrator-entered expense.
    member_context=browser.new_context(viewport={'width':1280,'height':900})
    member=member_context.new_page();member.goto(args.base_url+'/login');ready(member)
    member.locator('[name=username]').fill('manual-member');member.locator('[name=password]').fill('demo-only-7')
    member.get_by_role('button',name='进入工作台',exact=True).click();ready(member)
    expect(member.locator('[data-task="1"]')).to_have_attribute('aria-pressed','true')
    expect(member.locator('#memberDesk #memberManualExpenses')).to_contain_text('劳务费')
    expect(member.locator('#uploadForm, #submitFinal, #draftList')).to_have_count(0)
    expect(member.locator('#memberPending')).to_have_text('0项')
    expect(member.locator('#memberManualExpenses')).to_contain_text('150.25')
    expect(member.locator('#memberTotal')).to_have_text('¥0.00')
    # Project scope has one heading and clear, persistent navigation.
    page.locator('.project-card[data-drop-project="2"] [data-action=open-project]').click()
    expect(page.locator('#projectBanner h2')).to_have_text('实验平台建设示例')
    expect(page.locator('#listTitle')).to_contain_text('项目内票据')
    expect(page.get_by_role('heading',name='实验平台建设示例',exact=True,level=2)).to_have_count(1)
    expect(page.locator('#showCurrentProject')).to_have_attribute('aria-pressed','true')
    page.locator('[data-action=project-summary]').first.click()
    expect(page.get_by_role('dialog')).to_contain_text('276.25')
    expect(page.get_by_role('dialog')).to_contain_text('手工登记成员')
    page.get_by_role('button',name='关闭窗口',exact=True).click()
    page.locator('[data-action=return-all-invoices]').click()
    expect(page.locator('#showAll')).to_have_attribute('aria-pressed','true')
    expect(page.locator('#taskFilter')).to_have_value('1')
    # Edit and move the expense to a project that has no physical invoices.
    expense=page.locator('[data-manual-expense]')
    expense.locator('[data-action=edit-manual-expense]').click()
    dialog.locator('[name=amount]').fill('200.00');dialog.locator('[name=remark]').fill('2人 × 100元')
    target=page.evaluate('admin.data.projects.find(p=>p.code==="SCROLL-0").id')
    dialog.locator('[name=project_id]').select_option(str(target))
    dialog.get_by_role('button',name='保存报销项',exact=True).click()
    expect(page.locator('#reimbursementTotal')).to_have_text('¥1,410.38')
    page.locator(f'.project-card[data-drop-project="{target}"] [data-action=open-project]').click()
    expect(page.locator('#projectBanner')).to_contain_text('0 张发票 + 1 项无电子发票')
    with page.expect_download() as download:
        page.locator('#projectBanner [data-action=download-project]').click()
    bundle_path=OUT/'manual-only-project.zip';download.value.save_as(str(bundle_path))
    with zipfile.ZipFile(bundle_path) as z:
        assert '2人 × 100元' in z.read('无电子发票报销项.csv').decode('utf-8-sig')
        assert not any(n.endswith('.pdf') for n in z.namelist())
    page.locator('#projectBanner [data-action=print-project]').click()
    expect(page.locator('#toastRegion')).to_contain_text('没有可打印的发票')
    page.locator('[data-action=collapse-categories]').click()
    expect(page.locator('[data-manual-expense]')).to_have_count(0)
    page.locator('[data-action=expand-categories]').click()
    expect(page.locator('[data-manual-expense]')).to_have_count(1)
    page.locator('#projectBanner').scroll_into_view_if_needed()
    for width,height in [(1600,1080),(1024,1000),(768,1024),(390,844)]:
        page.set_viewport_size({'width':width,'height':height})
        if page.evaluate('document.documentElement.scrollWidth>innerWidth'):
            diagnostics=page.evaluate('''Array.from(document.querySelectorAll('body *')).map(e=>({tag:e.tagName,id:e.id,cls:e.className,left:e.getBoundingClientRect().left,right:e.getBoundingClientRect().right,width:e.getBoundingClientRect().width,overflow:getComputedStyle(e).overflowX})).filter(e=>e.right>innerWidth+1&&e.width>300).slice(0,18)''')
            print('Overflow diagnostics:',width,json.dumps(diagnostics,ensure_ascii=False))
            page.screenshot(path=str(OUT/f'overflow-{width}.png'),full_page=True)
            raise AssertionError(f'overflow {width}')
        page.locator('#projectBanner').scroll_into_view_if_needed()
        page.evaluate('document.querySelector("#toastRegion").replaceChildren()')
        page.screenshot(path=str(OUT/f'project-scope-{width}.png'),full_page=True)
    page.set_viewport_size({'width':1600,'height':1080})
    page.locator('[data-action=return-all-invoices]').click()
    page.locator('[data-action=delete-manual-expense]').click()
    page.get_by_role('button',name='确认删除报销项',exact=True).click()
    expect(page.locator('[data-manual-expense]')).to_have_count(0)
    expect(page.locator('#reimbursementTotal')).to_have_text('¥1,210.38')
    member.reload();ready(member)
    expect(member.locator('#memberManualExpenses')).to_have_count(0)
    expect(member.locator('[data-task="1"]')).to_have_count(0)

    # The same member has administrator-entered expenses in two different tasks.
    page.evaluate('''async () => {
      const user_id = admin.data.users.find(u => u.username === 'demo').id;
      for (const entry of [
        {task_id:1, category:'九月劳务费', amount:'121.36', remark:'九月登记凭证'},
        {task_id:2, category:'十月材料费', amount:'12.34', remark:'十月登记凭证'},
        {task_id:2, category:'十月印刷费', amount:'25.11', remark:''},
      ]) {
        await api('/api/admin/manual-expenses', {method:'POST', body:{user_id,...entry}});
      }
      await api('/api/admin/window/1/complete', {method:'POST'});
    }''')
    member.goto(args.base_url+'/logout')
    member.locator('[name=username]').fill('demo')
    member.locator('[name=password]').fill('demo-only')
    member.get_by_role('button',name='进入工作台',exact=True).click()
    local_expenses=member.locator('#memberDesk #memberManualExpenses')
    expect(local_expenses).to_contain_text('十月材料费')
    expect(local_expenses).to_contain_text('十月登记凭证')
    expect(local_expenses.locator('.panel-heading strong')).to_contain_text('37.45')
    expect(local_expenses).not_to_contain_text('九月劳务费')
    expect(member.locator('#uploadForm')).to_be_visible()
    expect(member.locator('#memberTotal')).to_have_text('¥136.88')
    member.locator('[data-task="1"]').click()
    expect(local_expenses).to_contain_text('九月劳务费')
    expect(local_expenses).to_contain_text('121.36')
    expect(local_expenses).not_to_contain_text('十月材料费')
    expect(member.locator('#memberDesk .task-desk-head')).to_contain_text('已结项')
    expect(member.locator('#uploadForm')).to_have_count(0)
    member.set_viewport_size({'width':375,'height':812})
    assert member.evaluate('document.documentElement.scrollWidth <= innerWidth')
    local_expenses.screenshot(path=str(OUT/'member-task-expenses.png'))
    member.locator('[data-task="2"]').click()
    expect(local_expenses).to_contain_text('37.45')
    expect(local_expenses).not_to_contain_text('九月劳务费')
    assert not errors,errors
    (OUT/'result.json').write_text(json.dumps({'passed':True,'page_errors':errors,'independent_scroll':True,'no_duplicate_project_title':True,'manual_and_invoice_totals_separate':True,'manual_only_zip':True,'manual_assignment_button_and_drag':True,'viewports':[1600,1024,768,390]},indent=2),encoding='utf-8')
    print('Project workflow and v2.8.1 task-scoped member expenses passed')
    member_context.close();context.close();browser.close()
