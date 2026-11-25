"""Exercise member edits and deletions through the admin UI on the isolated preview server."""
import argparse
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
from playwright.sync_api import expect, sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument('--base-url', required=True)
base = parser.parse_args().base_url

with sync_playwright() as pw:
    browser = pw.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'))
    page = browser.new_page(viewport={'width': 1440, 'height': 900})
    member = browser.new_page()
    errors = []
    page.on('pageerror', lambda exc: errors.append(str(exc)))
    page.goto(base + '/admin/login')
    page.locator('[name=password]').fill('preview-admin-2026')
    page.get_by_role('button', name='进入管理工作台').click()
    page.get_by_role('button', name='成员管理').click()
    row = page.get_by_role('row').filter(has=page.get_by_role('cell', name='demo', exact=True))
    expect(row.get_by_role('cell', name='2 项')).to_be_visible()
    row.get_by_role('button', name='编辑 林晓 的账户').click()
    dialog = page.get_by_role('dialog')
    expect(dialog.get_by_label('真实姓名')).to_have_value('林晓')
    dialog.get_by_label('真实姓名').fill('改名验收')
    dialog.get_by_label('登录账号').fill('edited-member')
    dialog.get_by_role('button', name='保存修改').click()
    row = page.get_by_role('row').filter(has=page.get_by_role('cell', name='edited-member', exact=True))
    expect(row.get_by_role('cell', name='改名验收', exact=True)).to_be_visible()

    member.goto(base + '/login')
    member.get_by_label('登录账号').fill('edited-member')
    member.locator('[name=password]').fill('demo-only')
    member.get_by_role('button', name='进入工作台').click()
    member.wait_for_url(base + '/')
    expect(member.locator('body')).to_contain_text('改名验收')

    page.set_viewport_size({'width': 390, 'height': 844})
    delete = row.get_by_role('button', name='删除 改名验收 的账户')
    delete.click()
    expect(dialog).to_contain_text('改名验收（edited-member）')
    dialog.get_by_role('button', name='取消').click()
    expect(delete).to_be_enabled()
    delete.click()
    dialog.get_by_role('button', name='删除账户', exact=True).click()
    expect(row).to_have_count(0)
    page.reload()
    page.get_by_role('button', name='成员管理').click()
    expect(page.get_by_role('row').filter(has_text='edited-member')).to_have_count(0)
    member.reload()
    member.wait_for_url(base + '/login')
    assert not errors, errors
    browser.close()
print('Desktop edit, member login, mobile cancellation/deletion and session revocation passed.')
