import io
import sqlite3
import zipfile

from conftest import invoices, project


def sign_in(client, username, password):
    client.get('/login')
    with client.session_transaction() as session:
        token = session['csrf_token']
    return client.post('/login', data={'username': username, 'password': password, 'csrf_token': token},
                       headers={'Accept': 'application/json'})


def test_admin_login_requires_an_explicit_password(site):
    module, _, _ = site
    client = module.app.test_client()
    configured_password = module.app.config['ADMIN_PASSWORD']
    client.get('/admin/login')
    with client.session_transaction() as session:
        token = session['csrf_token']

    module.app.config['ADMIN_PASSWORD'] = None
    assert 'LABINVOICE_ADMIN_PASSWORD' in client.get('/admin/login').get_data(as_text=True)
    assert client.post('/admin/login', data={'password': '', 'csrf_token': token}).status_code == 200
    assert client.get('/api/admin/workbench').status_code == 401

    module.app.config['ADMIN_PASSWORD'] = configured_password
    assert client.post('/admin/login', data={'password': configured_password, 'csrf_token': token}).status_code == 302
    assert client.get('/api/admin/workbench').status_code == 200
    module.app.config['ADMIN_PASSWORD'] = None
    assert client.get('/api/admin/workbench').status_code == 401


def test_edit_and_delete_preserve_records_and_revoke_access(site, legacy):
    module, admin, _ = site
    original_files = {p: p.read_bytes() for p in legacy[0].iterdir()}
    pid = project(admin)
    original = invoices(admin)
    ids = [row['id'] for row in original]
    assert admin.post('/api/admin/allocate', json={'invoice_ids': ids, 'project_id': pid}).status_code == 200
    expense = {'task_id': 1, 'user_id': 1, 'category': '劳务费', 'amount': '12', 'project_id': pid}
    mid = admin.post('/api/admin/manual-expenses', json=expense).json['id']
    assert admin.post('/api/admin/users/1/password', json={'new_password': 'member-pass'}).status_code == 200
    member = module.app.test_client()
    assert sign_in(member, 'member', 'member-pass').status_code == 200

    identity = {'username': 'new-login', 'real_name': '更正姓名'}
    assert admin.patch('/api/admin/users/1', json=identity).status_code == 200
    assert sign_in(module.app.test_client(), 'member', 'member-pass').status_code == 400
    assert sign_in(module.app.test_client(), 'new-login', 'member-pass').status_code == 200
    assert identity['real_name'] in member.get('/').get_data(as_text=True)
    assert next(t for t in member.get('/api/user/tasks').json if t['id'] == 1)['final_amount'] == 100.01
    member_url = '/api/user/view_file/1/old/' + original[0]['filename']
    assert member.get(member_url).data == original_files[legacy[0] / original[0]['filename']]
    assert admin.get('/api/admin/window/1/user_details/更正姓名').json['user_total'] == 100.01
    assert admin.get('/api/admin/window/1/status').json['users'][0]['invoice_amount'] == 100.01

    assert admin.post('/api/admin/users/delete/1').status_code == 200
    assert member.get(member_url).status_code == 401
    assert sign_in(module.app.test_client(), 'new-login', 'member-pass').status_code == 400
    for endpoint in ('workbench', 'overview'):
        assert all(u['id'] != 1 for u in admin.get('/api/admin/' + endpoint).json['users'])
    assert admin.patch('/api/admin/users/1', json=identity).status_code == 404
    assert admin.post('/api/admin/users/1/password', json={'new_password': 'changed'}).status_code == 404
    assert admin.post('/api/admin/window/create', json={'title': '新任务', 'user_ids': [2, 1]}).status_code == 400
    assert admin.post('/api/admin/manual-expenses', json=expense).status_code == 404
    assert admin.patch(f'/api/admin/manual-expenses/{mid}', json={**expense, 'remark': '核对完成'}).status_code == 200
    data = admin.get('/api/admin/workbench').json
    assert sorted(row['id'] for row in data['invoices']) == sorted(ids)
    assert all(r['project_id'] == pid and r['real_name'] == '更正姓名' and r['user_id'] == 1 for r in data['invoices'])
    assert data['projects'][0]['users'] == [dict(real_name='更正姓名', invoice_count=2, manual_count=1, amount_cents=11201)]
    assert admin.get(original[0]['url']).status_code == 200
    with zipfile.ZipFile(io.BytesIO(admin.get(f'/admin/projects/{pid}/download').data)) as archive:
        assert '更正姓名,2,1,112.01' in archive.read('人员合计.csv').decode('utf-8-sig')
    assert original_files == {p: p.read_bytes() for p in legacy[0].iterdir()}

    # Reuse both the former login and the legacy directory's name in the very same task.
    replacement = {'username': 'new-login', 'real_name': '测试成员', 'password': 'fresh-pass'}
    assert admin.post('/register', data=replacement).status_code == 302
    uid = next(u['id'] for u in admin.get('/api/admin/overview').json['users'] if u['username'] == 'new-login')
    with module.app.app_context():
        db = module.get_db()
        db.execute('INSERT INTO assignments(window_id,user_id) VALUES(1,?)', (uid,))
        db.commit()
    fresh = module.app.test_client()
    assert sign_in(fresh, 'new-login', 'fresh-pass').status_code == 200
    assert fresh.get('/api/user/tasks').json[0]['items'] == []
    assert fresh.get(member_url).status_code == 404
    assert fresh.get('/api/user/manual-expenses').json == []
    assert admin.get('/api/admin/window/1/user_details/测试成员').json['items'] == []
    assert admin.get(original[0]['url']).status_code == 200


def test_identity_validation_and_admin_only_changes(site):
    _, client, _ = site
    for username, name in [('other', '改名'), ('renamed', '另一成员'), ('renamed', '../错误')]:
        assert client.patch('/api/admin/users/1', json={'username': username, 'real_name': name}).status_code == 400
    original = next(u for u in client.get('/api/admin/overview').json['users'] if u['id'] == 1)
    assert (original['username'], original['real_name']) == ('member', '测试成员')
    assert client.post('/api/admin/users/delete/2').status_code == 200
    assert client.post('/api/admin/users/delete/2').status_code == 404
    with client.session_transaction() as session:
        del session['admin_logged_in']
    assert client.patch('/api/admin/users/1', json=original).status_code == 401
    assert client.post('/api/admin/users/delete/1').status_code == 401


def test_migrate_v31_users_without_changing_existing_columns(site):
    module, _, root = site
    old_path = root / 'pre-upgrade.db'
    with module.app.app_context(), sqlite3.connect(old_path) as old:
        module.get_db().backup(old)
        old.execute('ALTER TABLE users DROP COLUMN storage_owner')
        old.execute('ALTER TABLE users DROP COLUMN archived_username')
        old.execute("INSERT INTO users VALUES(9,'legacy','original-hash','原姓名')")
    module.app.config['DATABASE'] = str(old_path)
    module.init_db()
    module.init_db()
    assert len(list((root / '.backups').glob('database-pre-v3.1.1-*.db'))) == 1
    with module.app.app_context():
        row = module.get_db().execute('SELECT * FROM users WHERE id=9').fetchone()
        assert (row['username'], row['password'], row['real_name']) == ('legacy', 'original-hash', '原姓名')
        assert row['storage_owner'] == '原姓名' and row['archived_username'] is None
