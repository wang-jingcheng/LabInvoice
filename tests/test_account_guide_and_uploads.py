import io
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image
from werkzeug.security import check_password_hash, generate_password_hash

from conftest import pdf_bytes


def test_bundled_markdown_does_not_require_installed_mistune():
    result=subprocess.run([sys.executable,'-c',"import sys;sys.modules['mistune']=None;from guide import render_guide;print(render_guide('**offline**','markdown'))"],cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=20)
    assert result.returncode == 0, result.stderr
    assert '<strong>offline</strong>' in result.stdout


def draft_user(module):
    with module.app.app_context():
        module.get_db().execute("UPDATE users SET password=? WHERE id=1", (generate_password_hash("old123"),))
        module.get_db().execute("UPDATE assignments SET status=1 WHERE window_id=1")
        module.get_db().commit()


def test_member_can_change_password_only_with_current_password(site):
    module, client, _ = site
    draft_user(module)
    assert client.post('/api/user/password', json={'current_password':'wrong','new_password':'new123','confirm_password':'new123'}).status_code == 400
    assert client.post('/api/user/password', json={'current_password':'old123','new_password':'new123','confirm_password':'mismatch'}).status_code == 400
    response=client.post('/api/user/password', json={'current_password':'old123','new_password':'new123','confirm_password':'new123'})
    assert response.status_code == 200, response.data
    with module.app.app_context():
        saved=module.get_db().execute('SELECT password FROM users WHERE id=1').fetchone()[0]
        assert saved != 'new123' and check_password_hash(saved,'new123')
    assert client.get('/api/user/tasks').status_code == 200


def test_admin_reset_invalidates_existing_member_session_and_does_not_expose_hash(site):
    module, admin, _ = site
    draft_user(module)
    member=module.app.test_client()
    member.get('/login')
    with member.session_transaction() as session:
        token=session['csrf_token']
    result=member.post('/login',data={'username':'member','password':'old123','csrf_token':token},headers={'Accept':'application/json'})
    assert result.status_code == 200 and result.json['redirect'] == '/'
    response=admin.post('/api/admin/users/1/password',json={'new_password':'123456'})
    assert response.status_code == 200
    assert member.get('/api/user/tasks').status_code == 401
    assert 'password' not in admin.get('/api/admin/workbench').json['users'][0]
    with module.app.app_context():
        assert check_password_hash(module.get_db().execute('SELECT password FROM users WHERE id=1').fetchone()[0],'123456')


def test_markdown_guide_formats_text_and_does_not_execute_html(site):
    _, client, _ = site
    content='# 使用说明\n\n**核对金额**\n\n| 项目 | 要求 |\n| --- | --- |\n| 发票 | 上传 |\n\n<script>alert(1)</script>\n\n[危险链接](javascript:alert%281%29)'
    response=client.post('/api/admin/guide',json={'content':content,'format':'markdown'})
    assert response.status_code == 200
    assert client.get('/api/admin/guide').json['format'] == 'markdown'
    html=client.get('/help').get_data(as_text=True)
    assert '<h1>使用说明</h1>' in html and '<strong>核对金额</strong>' in html and '<table>' in html
    assert '<script>alert(1)</script>' not in html and 'href="javascript:' not in html
    preview=client.post('/api/admin/guide/preview',json={'content':content,'format':'markdown'})
    assert preview.status_code == 200 and '<strong>核对金额</strong>' in preview.json['html']
    assert client.post('/api/admin/guide',json={'content':'text','format':'invalid'}).status_code == 400


def test_existing_plain_guide_is_not_automatically_reinterpreted_as_markdown(site):
    _, client, _ = site
    client.post('/api/admin/guide',json={'content':'# Literal title\n<script>literal()</script>'})
    assert client.get('/api/admin/guide').json['format'] == 'text'
    html=client.get('/help').get_data(as_text=True)
    assert '# Literal title' in html and '<script>literal()</script>' not in html


def test_guide_image_is_public_and_supports_safe_layout_without_changing_invoices(site):
    _, client, root = site
    stream=io.BytesIO()
    Image.new('RGB',(80,60),'blue').save(stream,format='PNG')
    response=client.post('/api/admin/guide/images',data={'image':(io.BytesIO(stream.getvalue()),'说明.png')})
    assert response.status_code == 200, response.data
    url=response.json['url']
    assert url.startswith('/guide-media/')
    content=f'![操作示意]({url} "width=50;align=right")'
    client.post('/api/admin/guide',json={'content':content,'format':'markdown'})
    html=client.get('/help').get_data(as_text=True)
    assert 'width:50%' in html and 'md-image-right' in html and url in html
    anonymous=client.application.test_client()
    assert anonymous.get(url).status_code == 200
    assert anonymous.post('/api/admin/guide/images',data={}).status_code in (401,403)
    assert client.post('/api/admin/guide/images',data={'image':(io.BytesIO(b'fake'),'bad.png')}).status_code == 400
    assert list((root/'uploads').glob('task_*')) == []


def batch_data(bad_second=False):
    return {'groups':json.dumps([{'type':'市内交通费','remark':'交通备注'},{'type':'专用材料费','remark':'材料备注'}]),
            'invoices_0':(io.BytesIO(pdf_bytes()),'交通.pdf'), 'attachments_0':(io.BytesIO(pdf_bytes()),'行程单.pdf'),
            'invoices_1':(io.BytesIO(b'broken' if bad_second else pdf_bytes()),'材料.pdf')}


def test_multiple_categories_saved_in_one_request_keep_original_ownership(site):
    module, client, _ = site
    draft_user(module)
    response=client.post('/api/user/upload_items/1',data=batch_data())
    assert response.status_code == 200, response.data
    items=next(t for t in client.get('/api/user/tasks').json if t['id']==1)['items']
    assert len(items) == 2
    by_type={item['type']:item for item in items}
    assert by_type['市内交通费']['invoice_files'] == ['主发票_交通.pdf']
    assert by_type['市内交通费']['attachments'] == ['附件_行程单.pdf']
    assert by_type['专用材料费']['invoice_files'] == ['主发票_材料.pdf']
    assert by_type['专用材料费']['remark'] == '材料备注'


def test_invalid_category_in_batch_leaves_all_groups_unsaved(site):
    module, client, root = site
    draft_user(module)
    response=client.post('/api/user/upload_items/1',data=batch_data(bad_second=True))
    assert response.status_code == 400
    assert next(t for t in client.get('/api/user/tasks').json if t['id']==1)['items'] == []
    assert not list((root/'uploads').glob('task_*/*/item_*'))
