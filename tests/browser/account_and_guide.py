"""Exercise the requested v2.2 changes using the isolated preview server."""
import argparse
import os
import io
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
from PIL import Image
from playwright.sync_api import sync_playwright, expect

parser=argparse.ArgumentParser()
parser.add_argument('--base-url',required=True)
args=parser.parse_args()
OUT=ROOT/'test-results' / 'browser' / 'account-and-guide';OUT.mkdir(parents=True,exist_ok=True)
from preview_server import FIXTURE_DIR as FIX
key='labinvoice.rememberedCredentials.v1'


def ready(page):
    page.wait_for_load_state('networkidle')


with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
    context=browser.new_context(viewport={'width':1440,'height':1050})
    page=context.new_page();page.set_default_timeout(10000)
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(args.base_url+'/login');ready(page)
    expect(page.locator('[name=password]')).to_have_value('')
    for width,height in [(390,844),(768,1024)]:
        page.set_viewport_size({'width':width,'height':height})
        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth'),f'login overflow {width}'
    page.set_viewport_size({'width':1440,'height':1050})
    page.locator('[name=username]').fill('demo')
    page.locator('#rememberCredentials').check()
    page.locator('[name=password]').fill('incorrect-demo-password')
    page.get_by_role('button',name='进入工作台',exact=True).click()
    expect(page.locator('#loginError')).to_be_visible()
    assert page.evaluate('(key)=>localStorage.getItem(key)',key) is None
    page.locator('[name=password]').fill('demo-only')
    page.get_by_role('button',name='进入工作台',exact=True).click();ready(page)
    expect(page.locator('#memberDesk')).to_contain_text('2026 年 10 月')
    page.goto(args.base_url+'/logout');ready(page)
    expect(page.locator('[name=username]')).to_have_value('demo')
    expect(page.locator('[name=password]')).to_have_value('demo-only')
    expect(page.locator('#rememberCredentials')).to_be_checked()
    page.locator('#rememberCredentials').uncheck()
    assert page.evaluate('(key)=>localStorage.getItem(key)',key) is None
    page.locator('#rememberCredentials').check()
    page.get_by_role('button',name='进入工作台',exact=True).click();ready(page)
    # Files selected under one category retain their original ownership.
    page.locator('input[name=type][value="市内交通费"]').check()
    page.locator('[data-picker=invoices]').set_input_files(str(FIX/'browser-first.pdf'))
    page.locator('[data-picker=attachments]').set_input_files(str(FIX/'browser-attachment.pdf'))
    page.locator('#uploadForm textarea').fill('市内交通备注')
    page.locator('input[name=type][value="专用材料费"]').check()
    expect(page.locator('#picked-invoices li')).to_have_count(0)
    image=io.BytesIO();Image.new('RGB',(120,160),'blue').save(image,format='PNG')
    page.locator('[data-picker=invoices]').set_input_files({'name':'material.png','mimeType':'image/png','buffer':image.getvalue()})
    page.locator('#uploadForm textarea').fill('专用材料备注')
    expect(page.locator('#pendingGroups .pending-group')).to_have_count(2)
    page.locator('input[name=type][value="市内交通费"]').check()
    expect(page.locator('#picked-invoices')).to_contain_text('browser-first.pdf')
    expect(page.locator('#picked-invoices')).not_to_contain_text('material.png')
    expect(page.locator('#uploadForm textarea')).to_have_value('市内交通备注')
    page.screenshot(path=str(OUT/'multiple-categories.png'),full_page=True)
    for width,height in [(390,844),(768,1024)]:
        page.set_viewport_size({'width':width,'height':height})
        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth'),f'upload overflow {width}'
        page.screenshot(path=str(OUT/f'multiple-categories-{width}.png'),full_page=True)
    page.set_viewport_size({'width':1440,'height':1050})
    expect(page.locator('#saveDraftBtn')).to_contain_text('保存至草稿箱')
    page.locator('#saveDraftBtn').click()
    expect(page.get_by_role('dialog')).not_to_be_visible()
    expect(page.locator('.draft-item')).to_have_count(2)
    tasks=page.evaluate("async()=>await (await fetch('/api/user/tasks')).json()")
    items=next(task for task in tasks if task['id']==2)['items']
    by_type={item['type']:item for item in items}
    assert by_type['市内交通费']['invoice_files']==['主发票_browser-first.pdf']
    assert by_type['市内交通费']['remark']=='市内交通备注'
    assert by_type['专用材料费']['invoice_files']==['主发票_material.png']
    assert by_type['专用材料费']['remark']=='专用材料备注'
    # Own password change updates remembered credentials only for this account.
    page.locator('[data-account=password]').click()
    page.locator('[name=current_password]').fill('demo-only')
    page.locator('[name=new_password]').fill('new-password')
    page.locator('[name=confirm_password]').fill('new-password')
    page.get_by_role('button',name='保存新密码',exact=True).click()
    expect(page.get_by_role('dialog')).not_to_be_visible()
    page.goto(args.base_url+'/logout');ready(page)
    expect(page.locator('[name=password]')).to_have_value('new-password')
    page.get_by_role('button',name='进入工作台',exact=True).click();ready(page)
    # Keep this member session open while admin resets its password later.
    admin_context=browser.new_context(viewport={'width':1440,'height':1050})
    admin=admin_context.new_page();admin.on('pageerror',lambda e:errors.append(str(e)))
    admin.goto(args.base_url+'/admin/login');ready(admin)
    admin.locator('[name=password]').fill('preview-admin-2026')
    admin.get_by_role('button',name='进入管理工作台',exact=True).click();ready(admin)
    admin.locator('[data-nav=tasks]').click()
    for action,state in [('close','收集已结束'),('complete','已结项'),('uncomplete','收集已结束'),('reopen','收集中')]:
        admin.locator('[data-action=task-detail][data-id="2"]').click()
        admin.locator(f'[data-task-state={action}]').click()
        admin.locator('#confirmAction').click()
        expect(admin.get_by_role('dialog')).not_to_be_visible()
        card=admin.locator('.task-card').filter(has=admin.locator('[data-action=task-detail][data-id="2"]'))
        expect(card).to_contain_text(state)
    admin.locator('[data-nav=guide]').click()
    admin.locator('#guideFormat').select_option('markdown')
    admin.locator('#guideEditor').fill('# 实验室使用说明\n\n**核对发票金额**\n\n| 报销项 | 材料 |\n| --- | --- |\n| 交通费 | 行程单 |\n\n<script>bad()</script>\n')
    expect(admin.locator('#guidePreview strong')).to_have_text('核对发票金额')
    admin.locator('#guideImageWidth').select_option('50')
    admin.locator('#guideImageAlign').select_option('right')
    admin.locator('#guideImageInput').set_input_files({'name':'操作示意.png','mimeType':'image/png','buffer':image.getvalue()})
    expect(admin.locator('#guidePreview img')).to_have_count(1)
    expect(admin.locator('#guidePreview .md-image')).to_have_attribute('style','width:50%')
    admin.locator('[data-action=save-guide]').click()
    expect(admin.locator('#guideSaveState')).to_contain_text('已保存')
    admin.evaluate("document.querySelector('#toastRegion').replaceChildren();window.scrollTo(0,0)")
    admin.screenshot(path=str(OUT/'markdown-editor.png'),full_page=True)
    for width,height in [(768,1024),(390,844)]:
        admin.set_viewport_size({'width':width,'height':height})
        assert not admin.evaluate('document.documentElement.scrollWidth>innerWidth'),f'editor overflow {width}'
    guide=admin_context.new_page();guide.goto(args.base_url+'/help');ready(guide)
    expect(guide.locator('.guide-content h1')).to_have_text('实验室使用说明')
    expect(guide.locator('.guide-content img')).to_have_count(1)
    expect(guide.locator('.guide-content script')).to_have_count(0)
    guide.screenshot(path=str(OUT/'markdown-guide.png'),full_page=True)
    admin.set_viewport_size({'width':1440,'height':1050})
    admin.locator('[data-nav=members]').click()
    admin.locator('[data-action=member-password][data-id="1"]').click()
    expect(admin.locator('[name=new_password]')).to_have_value('')
    admin.locator('[data-toggle-password]').click()
    expect(admin.locator('[name=new_password]')).to_have_attribute('type','text')
    admin.locator('[name=new_password]').fill('reset-password')
    admin.get_by_role('button',name='保存新密码',exact=True).click()
    expect(admin.get_by_role('dialog')).not_to_be_visible()
    assert page.evaluate("async()=> (await fetch('/api/user/tasks')).status")==401
    assert not errors,errors
    (OUT/'result.json').write_text(json.dumps({'passed':True,'page_errors':errors,'viewports':[1440,768,390]},indent=2),encoding='utf-8')
    print('v2.2 browser checks passed')
    context.close();admin_context.close();browser.close()
