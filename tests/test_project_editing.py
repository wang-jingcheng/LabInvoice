from conftest import invoices, project


def test_history_remembers_pairs_and_survives_project_removal(site):
    _,client,_=site
    pid=project(client,'经费一','P-001')
    project(client,'经费一','P-001')
    history=client.get('/api/admin/project-history').json
    assert len(history)==1
    assert (history[0]['name'],history[0]['code'])==('经费一','P-001')
    assert client.delete(f'/api/admin/projects/{pid}').status_code==200
    assert client.get('/api/admin/project-history').json==history
    assert client.patch('/api/admin/projects/2',json={'name':'经费二','code':'P-002'}).status_code==200
    assert len(client.get('/api/admin/project-history').json)==2


def test_history_edit_delete_do_not_modify_live_projects_or_reseed_on_restart(site):
    module,client,_=site
    pid=project(client)
    entry=client.get('/api/admin/project-history').json[0]
    url=f'/api/admin/project-history/{entry["id"]}'
    assert client.patch(url,json={'name':'新候选','code':'NEXT'}).status_code==200
    assert client.get('/api/admin/workbench').json['projects'][0]['name']=='项目A'
    assert client.patch(url,json={'name':'','code':''}).status_code==400
    assert client.delete(url).status_code==200
    with module.app.app_context():
        module.migrate()
    assert client.get('/api/admin/project-history').json==[]
    assert client.get('/api/admin/workbench').json['projects'][0]['id']==pid


def test_history_seeds_existing_projects_only_on_first_migration(site):
    module,client,_=site
    pid=project(client)
    with module.app.app_context():
        db=module.get_db()
        db.execute('DROP TABLE project_input_history');db.commit()
        module.migrate()
    history=client.get('/api/admin/project-history').json
    assert len(history)==1 and history[0]['code']=='A-2026'


def test_history_duplicates_and_authorization(site):
    _,client,_=site
    project(client,'一','A');project(client,'二','B')
    entries=client.get('/api/admin/project-history').json
    assert client.patch(f'/api/admin/project-history/{entries[0]["id"]}',json={'name':entries[1]['name'],'code':entries[1]['code']}).status_code==400
    assert len(client.get('/api/admin/project-history').json)==2
    with client.session_transaction() as session:
        session.pop('admin_logged_in')
    assert client.get('/api/admin/project-history').status_code==401
    assert client.delete(f'/api/admin/project-history/{entries[0]["id"]}').status_code==401


def test_remove_nonempty_project_only_with_explicit_unassign_preserves_files_and_amounts(site,legacy):
    _,client,_=site
    rows=invoices(client);pid=project(client)
    client.post('/api/admin/allocate',json={'invoice_ids':[r['id'] for r in rows],'project_id':pid})
    client.post('/api/admin/invoices/amount',json={'invoice_id':rows[0]['id'],'amount':'88.12'})
    assert client.post('/api/admin/window/1/complete').status_code==200
    snapshot={p.name:p.read_bytes() for p in legacy[0].iterdir()}
    assert client.delete(f'/api/admin/projects/{pid}?unassign=1').status_code==409
    assert client.post('/api/admin/window/1/uncomplete').status_code==200
    assert client.delete(f'/api/admin/projects/{pid}').status_code==409
    assert client.delete(f'/api/admin/projects/{pid}?unassign=1').status_code==200
    result=invoices(client)
    assert len(result)==2 and all(r['project_id'] is None and r['category_id'] is None for r in result)
    assert result[0]['amount_cents']==8812
    assert {p.name:p.read_bytes() for p in legacy[0].iterdir()}==snapshot
    assert client.get('/api/admin/workbench').json['projects']==[]
    assert len(client.get('/api/admin/project-history').json)==1


def test_remove_nonempty_category_unassigns_only_its_invoices(site,legacy):
    _,client,_=site
    pid=project(client);other=project(client,'另一经费','B')
    rows=invoices(client)
    client.post('/api/admin/allocate',json={'invoice_ids':[rows[0]['id']],'project_id':pid})
    client.post('/api/admin/allocate',json={'invoice_ids':[rows[1]['id']],'project_id':other})
    category=client.get('/api/admin/workbench').json['projects'][0]['categories'][0]
    assert client.patch(f'/api/admin/categories/{category["id"]}',json={'name':'新报销项'}).status_code==200
    assert client.delete(f'/api/admin/categories/{category["id"]}').status_code==409
    assert client.delete(f'/api/admin/categories/{category["id"]}?unassign=1').status_code==200
    result=invoices(client)
    assert result[0]['project_id'] is None and result[0]['category']=='专用材料费'
    assert result[1]['project_id']==other
    data=client.get('/api/admin/workbench').json
    assert data['projects'][0]['categories']==[]
    assert sum(r['amount_cents'] for r in result)==10001
