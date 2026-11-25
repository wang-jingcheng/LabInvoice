"""Verify the funding rail and archive lifecycle against the isolated browser fixture."""
import argparse
import os
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[2]
from playwright.sync_api import expect, sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument('--base-url', required=True)
args = parser.parse_args()
OUT = ROOT / 'test-results' / 'browser' / 'project-archive'
OUT.mkdir(parents=True, exist_ok=True)


def task_action(page, task_id, action, completed, closed):
    page.locator('[data-nav=tasks]').click()
    page.locator(f'[data-action=task-detail][data-id="{task_id}"]').click()
    page.locator(f'[data-task-state={action}]').click()
    page.locator('#confirmAction').click()
    page.wait_for_function(
        """([id, completed, closed]) => admin.data.windows.some(
            t => t.id === id && Boolean(t.is_completed) === completed
                && Boolean(t.is_collection_closed) === closed)""",
        arg=[task_id, completed, closed],
    )
    expect(page.get_by_role('dialog')).not_to_be_visible()
    page.locator('[data-nav=invoices]').click()


with sync_playwright() as pw:
    browser = pw.chromium.launch(
        executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),
        headless=True,
    )
    page = browser.new_page(viewport={'width':1600,'height':1080}, accept_downloads=True)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(args.base_url + '/admin/login')
    page.locator('[name=password]').fill('preview-admin-2026')
    page.get_by_role('button', name='进入管理工作台', exact=True).click()
    page.wait_for_function('admin.data && !admin.data.loading')
    project_list = page.locator('#projectList')
    archive_button = page.locator('#projectArchiveButton')
    archive_count = page.locator('#projectArchiveCount')
    dialog = page.get_by_role('dialog')

    # Seeded data represent an existing deployment with a completed September task.
    expect(project_list.locator('[data-drop-project]')).to_have_count(0)
    expect(project_list).to_contain_text('暂无待整理经费项目')
    expect(archive_count).to_have_text('2')
    archive_button.click()
    expect(dialog).to_contain_text('已归档经费项目')
    archived = dialog.locator('[data-archived-project="1"]')
    expect(archived).to_contain_text('2026 年 09 月')
    expect(archived.locator('[data-drop-project], [data-action=edit-project]')).to_have_count(0)
    archived.get_by_role('button', name='汇总明细').click()
    expect(dialog).to_contain_text('按成员汇总')
    dialog.get_by_role('button', name='返回已归档项目').click()
    with page.expect_download() as download:
        archived.get_by_role('button', name='打包下载').click()
    with zipfile.ZipFile(download.value.path()) as archive:
        assert '报销汇总.csv' in archive.namelist()
        assert any(name.endswith('.pdf') for name in archive.namelist())
    archived.get_by_role('button', name='合并打印').click()
    expect(dialog.get_by_role('button', name='生成合并 PDF')).to_be_visible()
    with page.expect_response(lambda response: response.url.endswith('/admin/projects/1/print')) as response:
        dialog.get_by_role('button', name='生成合并 PDF').click()
    assert response.value.ok and response.value.headers['content-type'].startswith('application/pdf')
    with page.expect_download() as pdf_download:
        dialog.get_by_role('link', name='下载 PDF').click()
    with pdf_download.value.path().open('rb') as pdf:
        assert pdf.read(4) == b'%PDF'
    dialog.get_by_role('button', name='关闭窗口').click()

    task_action(page, 1, 'uncomplete', False, True)
    expect(project_list.locator('.project-card')).to_have_count(2)
    expect(archive_count).to_have_text('0')

    # Fill the independent rail with additional project cards and measure its height.
    spare_projects = page.evaluate("""async () => {
        const ids = [];
        for (let i = 1; i <= 10; i++) {
          const {id} = await api('/api/admin/projects', {
            method:'POST', body:{name:'经费窗口测试 ' + i}
          });
          ids.push(id);
        }
        await loadAdmin();
        return ids;
    }""")
    assert project_list.evaluate('node => node.clientHeight >= 820 && node.scrollHeight > node.clientHeight')
    project_list.scroll_into_view_if_needed()
    project_list.hover()
    outer_scroll = page.evaluate('scrollY')
    page.mouse.wheel(0, 500)
    page.wait_for_function('document.querySelector("#projectList").scrollTop > 0')
    assert page.evaluate('scrollY') == outer_scroll
    project_list.evaluate('node => node.scrollTop = 0')
    page.screenshot(path=str(OUT / 'funding-rail.png'))

    # A manual expense in October keeps the shared project active after September closes.
    page.evaluate("""async ids => {
        for (const id of ids) await api('/api/admin/projects/' + id, {method:'DELETE'});
        await api('/api/admin/manual-expenses', {method:'POST', body:{
          task_id:2,user_id:1,project_id:1,category:'劳务费',amount:'42.50'
        }});
        await loadAdmin();
    }""", spare_projects)
    page.locator('#projectList [data-action=open-project][data-id="2"]').click()
    expect(page.locator('#projectBanner')).to_be_visible()
    task_action(page, 1, 'complete', True, True)
    expect(project_list.locator('.project-card[data-drop-project="1"]')).to_be_visible()
    expect(project_list.locator('.project-card[data-drop-project="2"]')).to_have_count(0)
    expect(page.locator('#projectBanner')).to_be_empty()
    expect(archive_count).to_have_text('1')
    page.locator('[data-action=new-manual-expense]').click()
    expect(dialog.locator('[name=project_id] option[value="1"]')).to_have_count(1)
    expect(dialog.locator('[name=project_id] option[value="2"]')).to_have_count(0)
    dialog.get_by_role('button', name='关闭窗口').click()

    task_action(page, 2, 'close', False, True)
    expect(project_list.locator('.project-card[data-drop-project="1"]')).to_be_visible()
    task_action(page, 2, 'complete', True, True)
    expect(project_list.locator('.project-card')).to_have_count(0)
    expect(archive_count).to_have_text('2')
    page.set_viewport_size({'width':320,'height':844})
    archive_button.click()
    shared = dialog.locator('[data-archived-project="1"]')
    expect(shared).to_contain_text('2026 年 10 月')
    assert dialog.locator('.history-records').evaluate('node => node.scrollWidth <= node.clientWidth')
    shared.get_by_role('button', name='汇总明细').click()
    back = dialog.get_by_role('button', name='返回已归档项目')
    assert back.evaluate('node => node.getBoundingClientRect().left >= node.closest("dialog").getBoundingClientRect().left')
    back.click()
    page.set_viewport_size({'width':390,'height':844})
    assert dialog.evaluate('node => node.scrollWidth <= node.clientWidth')
    page.screenshot(path=str(OUT / 'project-archive-mobile.png'))
    dialog.get_by_role('button', name='关闭窗口').click()
    task_action(page, 2, 'uncomplete', False, True)
    expect(project_list.locator('.project-card[data-drop-project="1"]')).to_be_visible()
    expect(archive_count).to_have_text('1')
    assert not errors, errors
    browser.close()

print('v2.8.2 browser archive lifecycle, export and independent rail checks passed')

