"""Compact category drafts and responsive loading checks on isolated demo data."""
import argparse
import os
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
from playwright.sync_api import sync_playwright,expect

parser=argparse.ArgumentParser();parser.add_argument('--base-url',required=True);args=parser.parse_args()
OUT=ROOT/'test-results' / 'browser' / 'upload-drafts';OUT.mkdir(parents=True,exist_ok=True)
from preview_server import FIXTURE_DIR as FIX


def ready(page):page.wait_for_load_state('networkidle')


with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
    context=browser.new_context(viewport={'width':1440,'height':1000})
    page=context.new_page();page.set_default_timeout(12000)
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(args.base_url+'/login');ready(page)
    expect(page.get_by_text('“记住账号密码”会保存在本浏览器，仅建议在个人电脑勾选。',exact=True)).to_have_count(0)
    page.locator('[name=username]').fill('demo');page.locator('[name=password]').fill('demo-only')
    page.get_by_role('button',name='进入工作台',exact=True).click();ready(page)
    page.locator('input[name=type][value="专用材料费"]').check()
    expect(page.get_by_text('PDF / 图片',exact=True)).to_have_count(1)
    expect(page.locator('#uploadSummary')).to_have_text('每次最多 128 MB')
    assert page.locator('.dropzone').first.bounding_box()['height']<155
    page.locator('[data-picker=invoices]').set_input_files(str(FIX/'browser-first.pdf'))
    page.locator('[data-picker=attachments]').set_input_files(str(FIX/'browser-attachment.pdf'))
    page.locator('#saveDraftBtn').click()
    expect(page.locator('.draft-item')).to_have_count(1)
    expect(page.locator('.draft-item summary .amount')).to_have_text('¥ 31.25')
    expect(page.get_by_role('dialog')).not_to_be_visible()
    expect(page.locator('.draft-actions,.saved-append')).to_have_count(0)
    expect(page.get_by_text('本张金额（元）',exact=True)).to_have_count(0)
    columns=page.locator('.draft-columns').evaluate('e=>Array.from(e.children).map(x=>x.getBoundingClientRect().width)')
    assert abs(columns[0]/columns[1]-1.618)<.03,columns
    row=page.locator('.draft-invoice').first
    link=row.locator('.draft-file-link').bounding_box();amount=row.locator('[data-file-amount]').bounding_box()
    assert amount['x']>=link['x']+link['width'] and amount['height']<=34
    page.locator('[data-picker=invoices]').set_input_files(str(FIX/'browser-extra.pdf'))
    page.locator('#saveDraftBtn').click()
    expect(page.locator('.draft-invoice')).to_have_count(2)
    expect(page.locator('.draft-item')).to_have_count(1)
    expect(page.locator('.draft-item summary .amount')).to_have_text('¥ 43.35')
    page.locator('.draft-invoice').filter(has=page.locator('a',has_text='browser-first')).locator('[data-file-amount]').fill('40.00')
    page.locator('.draft-invoice').filter(has=page.locator('a',has_text='browser-first')).locator('[data-member-action=file-amount]').click()
    expect(page.locator('.draft-item summary .amount')).to_have_text('¥ 52.10')
    (FIX/'proof.docx').write_bytes(b'additional attachment')
    page.locator('[data-picker=attachments]').set_input_files(str(FIX/'proof.docx'))
    page.locator('#saveDraftBtn').click()
    expect(page.locator('.draft-attachment')).to_have_count(2)
    for width,height in [(1440,1000),(768,1024),(390,844)]:
        page.set_viewport_size({'width':width,'height':height})
        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth'),f'overflow {width}'
        page.evaluate("document.querySelector('#toastRegion').replaceChildren();window.scrollTo(0,0)")
        page.screenshot(path=str(OUT/f'compact-drafts-{width}.png'),full_page=True)
    page.set_viewport_size({'width':1440,'height':1000})
    for filename in ['browser-extra','browser-first']:
        page.locator(f'[data-member-action=remove-saved][data-file*="{filename}"]').click()
        page.get_by_role('button',name='确认移除',exact=True).click()
        expect(page.locator(f'.draft-invoice a',has_text=filename)).to_have_count(0)
    expect(page.locator('.draft-attachment')).to_have_count(2)
    page.locator('[data-picker=invoices]').set_input_files(str(FIX/'browser-second.pdf'))
    page.locator('#saveDraftBtn').click()
    expect(page.locator('.draft-item')).to_have_count(1)
    expect(page.locator('.draft-item summary .amount')).to_have_text('¥ 46.80')
    page.locator('[data-member-action=delete-category]').click()
    page.get_by_role('button',name='确认删除',exact=True).click()
    expect(page.locator('.draft-item')).to_have_count(0)
    # Delay only the test browser's first full workbench fetch; metadata navigation
    # must still work before that promise resolves.
    admin=context.new_page();admin.on('pageerror',lambda e:errors.append(str(e)))
    admin.add_init_script("""{
      const realFetch=window.fetch.bind(window);let delayed=false;
      window.fetch=(url,...options)=>{
        if(String(url)==='/api/admin/workbench'&&!delayed){delayed=true;return new Promise(resolve=>setTimeout(()=>resolve(realFetch(url,...options)),6000));}
        return realFetch(url,...options);
      };
    }""")
    admin.goto(args.base_url+'/admin/login');ready(admin)
    admin.locator('[name=password]').fill('preview-admin-2026')
    admin.get_by_role('button',name='进入管理工作台',exact=True).click()
    admin.locator('[data-nav=members]').click()
    expect(admin.locator('#managementView')).to_contain_text('林晓')
    assert admin.evaluate('admin.data.loading') is True
    admin.locator('[data-nav=guide]').click()
    expect(admin.locator('#guideEditor')).to_be_visible()
    admin.wait_for_function('admin.data && !admin.data.loading')
    assert not errors,errors
    (OUT/'result.json').write_text(json.dumps({'passed':True,'page_errors':errors,'viewports':[1440,768,390],'navigation_while_loading':True},indent=2),encoding='utf-8')
    print('v2.3 browser checks passed')
    context.close();browser.close()
