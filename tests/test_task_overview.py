import json

from conftest import invoices, project


def test_end_collection_locks_member_but_keeps_invoices_visible_then_complete_and_undo(site,legacy):
    module,client,_=site
    with module.app.app_context():
        module.get_db().execute('UPDATE assignments SET status=1 WHERE window_id=1');module.get_db().commit()
    before=invoices(client)
    assert client.post('/api/admin/window/1/close').status_code==200
    task=next(t for t in client.get('/api/user/tasks').json if t['id']==1)
    assert task['is_collection_closed']==1 and task['is_completed']==0
    assert client.post('/api/user/item/1/old/amount',json={'amount':'1'}).status_code==409
    assert all(not r['task_completed'] and r['collection_closed'] for r in invoices(client))
    assert client.post('/api/admin/window/1/complete').status_code==200
    assert all(r['task_completed'] for r in invoices(client))
    assert client.post('/api/admin/window/1/reopen').status_code==409
    assert client.post('/api/admin/window/1/uncomplete').status_code==200
    task=next(t for t in client.get('/api/user/tasks').json if t['id']==1)
    assert task['is_collection_closed']==1 and task['is_completed']==0
    assert client.post('/api/admin/window/1/reopen').status_code==200
    assert client.post('/api/user/item/1/old/amount',json={'amount':'1'}).status_code==200
    assert {r['id'] for r in before}=={r['id'] for r in invoices(client)}


def test_rename_preserves_files_task_id_assignments_and_allocations(site,legacy):
    module,client,_=site
    row=invoices(client)[0];pid=project(client)
    client.post('/api/admin/allocate',json={'invoice_ids':[row['id']],'project_id':pid})
    snapshot={p.name:p.read_bytes() for p in legacy[0].iterdir()}
    assert client.patch('/api/admin/window/1',json={'title':'2026年9-10月差旅报销'}).status_code==200
    assert invoices(client)[0]['task_title']=='2026年9-10月差旅报销'
    assert invoices(client)[0]['project_id']==pid
    assert {p.name:p.read_bytes() for p in legacy[0].iterdir()}==snapshot
    assert client.patch('/api/admin/window/1',json={'title':''}).status_code==400
    assert client.patch('/api/admin/window/1',json={'title':'x'*121}).status_code==400
    assert client.patch('/api/admin/window/999',json={'title':'合法名称'}).status_code==404
    assert client.post('/api/admin/window/999/close').status_code==404
    with client.session_transaction() as session:session.pop('admin_logged_in')
    assert client.patch('/api/admin/window/1',json={'title':'越权'}).status_code==401


def test_task_summaries_include_zero_categories_only_formal_and_current_categorization(site,legacy):
    module,client,_=site
    rows=invoices(client);pid=project(client)
    category=client.post(f'/api/admin/projects/{pid}/categories',json={'name':'自定义耗材'}).json['id']
    client.post('/api/admin/allocate',json={'invoice_ids':[rows[0]['id']],'project_id':pid,'category_id':category})
    data=client.get('/api/admin/workbench').json
    summaries=data['task_summaries'];first=next(s for s in summaries if s['id']==1);empty=next(s for s in summaries if s['id']==2)
    assert first['amount_cents']==10001 and first['project_amount_cents']==5001
    amounts={c['name']:c['amount_cents'] for c in first['categories']}
    assert amounts['市内交通费']==0 and amounts['自定义耗材']==5001 and amounts['专用材料费']==5000
    assert empty['amount_cents']==0 and len(empty['categories'])==6
    with module.app.app_context():
        module.get_db().execute('UPDATE assignments SET status=1 WHERE window_id=1');module.get_db().commit()
    first=next(s for s in client.get('/api/admin/workbench').json['task_summaries'] if s['id']==1)
    assert first['amount_cents']==0 and first['project_amount_cents']==0 and first['invoice_count']==0
    assert all(c['amount_cents']==0 for c in first['categories'])


def test_travel_zero_categories_and_pending_not_reported_as_zero(site,legacy):
    module,client,_=site
    directory,meta=legacy
    meta['type']='交通费';meta['invoice_amounts']={name:None for name in meta['invoice_files']};meta['auto_amount']=None
    (directory/'meta.json').write_text(json.dumps(meta),encoding='utf-8')
    with module.app.app_context():
        module.get_db().execute("UPDATE windows SET task_type='国内差旅费' WHERE id=1");module.get_db().commit()
    task=next(s for s in client.get('/api/admin/workbench').json['task_summaries'] if s['id']==1)
    assert task['amount_cents'] is None and task['pending_amount_count']==2
    assert {c['name']:c['amount_cents'] for c in task['categories']}=={'交通费':None,'住宿费':0,'其他费用':0}


def test_v25_migration_adds_closed_flag_without_changing_existing_task_fields(site):
    import storage
    module,client,root=site
    # Emulate the actual pre-upgrade windows schema, then apply the new migration.
    with module.app.app_context():
        db=module.get_db()
        db.execute('ALTER TABLE windows DROP COLUMN is_collection_closed')
        db.execute('UPDATE windows SET is_completed=1 WHERE id=1');db.commit()
        before=[tuple(r) for r in db.execute('SELECT id,title,created_at,is_completed,task_type FROM windows ORDER BY id')]
        storage.migrate();storage.migrate()
        after=[tuple(r) for r in db.execute('SELECT id,title,created_at,is_completed,task_type FROM windows ORDER BY id')]
        assert after==before
        assert [r[0] for r in db.execute('SELECT is_collection_closed FROM windows ORDER BY id')]==[1,0]
