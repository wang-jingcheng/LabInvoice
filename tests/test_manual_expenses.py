import io
import json
import zipfile

from conftest import invoices,project


def manual(client,**changes):
    data={'task_id':1,'user_id':1,'category':'劳务费','amount':'150.25','remark':'参与实验劳务'}
    data.update(changes)
    response=client.post('/api/admin/manual-expenses',json=data)
    assert response.status_code==200,response.data
    return response.json['id']


def test_admin_adjusts_invoice_category_and_amount_without_changing_source(site,legacy):
    _,client,_=site
    before={p.name:p.read_bytes() for p in legacy[0].iterdir()}
    row=invoices(client)[0]
    response=client.post('/api/admin/invoices/edit',json={'invoice_id':row['id'],'category':'设备维修费','amount':'88.12','version':row['version']})
    assert response.status_code==200,response.data
    changed=next(r for r in invoices(client) if r['id']==row['id'])
    assert changed['category']=='设备维修费' and changed['original_category']=='专用材料费'
    assert changed['amount_cents']==8812 and changed['project_id'] is None
    assert client.post('/api/admin/invoices/amount',json={'invoice_id':row['id'],'amount':'90'}).status_code==200
    assert next(r for r in invoices(client) if r['id']==row['id'])['category']=='设备维修费'
    assert client.post('/api/admin/invoices/edit',json={'invoice_id':row['id'],'category':'其他','amount':'1','version':row['version']}).status_code==409
    assert {p.name:p.read_bytes() for p in legacy[0].iterdir()}==before
    pid=project(client)
    client.post('/api/admin/allocate',json={'invoice_ids':[row['id']],'project_id':pid})
    changed=next(r for r in invoices(client) if r['id']==row['id'])
    assert changed['category']=='设备维修费'
    assert client.post('/api/admin/invoices/edit',json={'invoice_id':row['id'],'category':'耗材费','amount':'99'}).status_code==200
    changed=next(r for r in invoices(client) if r['id']==row['id'])
    assert changed['category']=='耗材费' and changed['project_id']==pid


def test_manual_expenses_have_separate_counts_and_member_task_project_totals(site,legacy):
    _,client,_=site
    pid=project(client);mid=manual(client,project_id=pid)
    data=client.get('/api/admin/workbench').json
    assert len(data['invoices'])==2 and len(data['manual_expenses'])==1
    assert data['manual_expenses'][0]['id']==mid
    summary=next(t for t in data['task_summaries'] if t['id']==1)
    assert summary['amount_cents']==10001
    assert summary['manual_amount_cents']==15025
    assert summary['reimbursement_amount_cents']==25026
    p=data['projects'][0]
    assert p['total_cents']==15025 and p['invoice_count']==0 and p['manual_count']==1
    assert p['users'][0]['amount_cents']==15025 and p['categories'][0]['name']=='劳务费'
    status=client.get('/api/admin/window/1/status').json
    assert status['grand_total']==250.26 and status['users'][0]['manual_amount']==150.25
    assert client.get('/api/user/manual-expenses').json[0]['amount_cents']==15025


def test_manual_any_member_without_assignment_and_private_member_listing(site):
    module,client,_=site
    mid=manual(client,user_id=2)
    assert client.get('/api/user/manual-expenses').json==[]
    with client.session_transaction() as session:session['user_id']=2;session['real_name']='另一成员'
    assert client.get('/api/user/manual-expenses').json[0]['id']==mid
    with module.app.app_context():
        assert not module.get_db().execute('SELECT 1 FROM assignments WHERE user_id=2').fetchone()
    assert client.post('/api/admin/users/delete/2').status_code==200
    assert client.get('/api/user/manual-expenses').status_code==401
    assert client.get('/api/admin/workbench').json['manual_expenses'][0]['id']==mid


def test_manual_edit_conflict_soft_delete_and_closed_task_lock(site):
    module,client,_=site
    mid=manual(client)
    entry=client.get('/api/admin/workbench').json['manual_expenses'][0]
    url=f'/api/admin/manual-expenses/{mid}'
    assert client.patch(url,json={'task_id':1,'user_id':1,'category':'测试劳务','amount':'200','remark':'更正','version':entry['version']}).status_code==200
    assert client.patch(url,json={'task_id':1,'user_id':1,'category':'坏修改','amount':'1','version':entry['version']}).status_code==409
    client.post('/api/admin/window/1/complete')
    assert client.patch(url,json={'task_id':1,'user_id':1,'category':'劳务费','amount':'1'}).status_code==409
    assert client.delete(url).status_code==409
    client.post('/api/admin/window/1/uncomplete')
    assert client.delete(url).status_code==200
    assert client.get('/api/admin/workbench').json['manual_expenses']==[]
    with module.app.app_context():
        row=module.get_db().execute('SELECT * FROM manual_expenses WHERE id=?',(mid,)).fetchone()
        assert row['deleted_at'] and row['amount_cents']==20000


def test_manual_validation_permissions_and_project_removal_preserves_entry(site):
    _,client,_=site
    for changes in [{'amount':'-1'},{'amount':'nan'},{'user_id':999},{'task_id':999},{'category':''},{'project_id':999}]:
        data={'task_id':1,'user_id':1,'category':'劳务费','amount':'1',**changes}
        assert client.post('/api/admin/manual-expenses',json=data).status_code in {400,404}
    pid=project(client);mid=manual(client,project_id=pid)
    assert client.delete(f'/api/admin/projects/{pid}').status_code==409
    assert client.delete(f'/api/admin/projects/{pid}?unassign=1').status_code==200
    entry=client.get('/api/admin/workbench').json['manual_expenses'][0]
    assert entry['project_id'] is None and entry['amount_cents']==15025
    with client.session_transaction() as session:session.pop('admin_logged_in')
    assert client.post('/api/admin/manual-expenses',json={}).status_code==401
    assert client.delete(f'/api/admin/manual-expenses/{mid}').status_code==401


def test_manual_only_project_zip_and_print_does_not_create_fake_invoice(site):
    _,client,_=site
    pid=project(client);manual(client,project_id=pid,remark='劳务备注')
    response=client.get(f'/admin/projects/{pid}/download')
    assert response.status_code==200,response.data
    with zipfile.ZipFile(io.BytesIO(response.data)) as z:
        assert '无电子发票报销项.csv' in z.namelist()
        assert '劳务备注' in z.read('无电子发票报销项.csv').decode('utf-8-sig')
        assert '150.25' in z.read('人员合计.csv').decode('utf-8-sig')
        assert not any(n.endswith('.pdf') for n in z.namelist())
        manifest=json.loads(z.read('归档清单.json'))
        assert manifest['total_cents']==15025 and len(manifest['manual_expenses'])==1
    manifest=client.get(f'/api/admin/projects/{pid}/print-manifest').json
    assert manifest['files']==[] and manifest['manual_count']==1
    response=client.get('/admin/download/1')
    assert response.status_code==200
    with zipfile.ZipFile(io.BytesIO(response.data)) as z:assert '无电子发票报销项.csv' in z.namelist()


def test_delete_task_archives_manual_and_invoice_override_records(site,legacy):
    _,client,root=site
    mid=manual(client,user_id=2)
    client.post('/api/admin/invoices/edit',json={'invoice_id':invoices(client)[0]['id'],'category':'维修费','amount':'12'})
    response=client.post('/api/admin/window/delete/1',json={'confirmed':True})
    assert response.status_code==200,response.data
    snapshot=json.loads((root/response.json['archive']/'task-records.json').read_text(encoding='utf-8'))
    assert snapshot['manual_expenses'][0]['id']==mid
    assert {u['id'] for u in snapshot['users']}=={1,2}
    assert snapshot['category_overrides'][0]['category']=='维修费'
    assert client.get('/api/admin/workbench').json['manual_expenses']==[]


def test_mixed_invoice_manual_project_totals_and_print_manifest(site,legacy):
    _,client,_=site
    pid=project(client);row=invoices(client)[0]
    client.post('/api/admin/allocate',json={'invoice_ids':[row['id']],'project_id':pid})
    manual(client,project_id=pid)
    summary=client.get('/api/admin/workbench').json['projects'][0]
    assert summary['total_cents']==20026
    assert summary['invoice_count']==1 and summary['manual_count']==1
    assert summary['users'][0]['amount_cents']==20026
    response=client.get(f'/admin/projects/{pid}/download')
    with zipfile.ZipFile(io.BytesIO(response.data)) as z:
        data=json.loads(z.read('归档清单.json'))
        assert data['total_cents']==20026 and len(data['invoices'])==1 and len(data['manual_expenses'])==1
        assert '200.26' in z.read('人员合计.csv').decode('utf-8-sig')
    manifest=client.get(f'/api/admin/projects/{pid}/print-manifest').json
    assert manifest['invoice_count']==1 and manifest['manual_count']==1
    assert sum(f['kind']=='invoice' for f in manifest['files'])==1


def test_manual_category_removal_keeps_money_and_soft_deleted_foreign_keys_allow_project_delete(site):
    _,client,_=site
    pid=project(client);mid=manual(client,project_id=pid)
    category=client.get('/api/admin/workbench').json['projects'][0]['categories'][0]
    client.patch(f'/api/admin/categories/{category["id"]}',json={'name':'参与补贴'})
    assert client.get('/api/admin/workbench').json['manual_expenses'][0]['category']=='参与补贴'
    assert client.delete(f'/api/admin/categories/{category["id"]}').status_code==409
    assert client.delete(f'/api/admin/categories/{category["id"]}?unassign=1').status_code==200
    expense=client.get('/api/admin/workbench').json['manual_expenses'][0]
    assert expense['category']=='参与补贴' and expense['amount_cents']==15025 and expense['project_id'] is None
    assert client.patch(f'/api/admin/manual-expenses/{mid}',json={'task_id':1,'user_id':1,'category':'参与补贴','amount':'150.25','project_id':pid}).status_code==200
    assert client.delete(f'/api/admin/manual-expenses/{mid}').status_code==200
    assert client.delete(f'/api/admin/projects/{pid}').status_code==200


def test_zero_manual_amount_and_csv_note_formula_is_escaped(site):
    _,client,_=site
    pid=project(client);manual(client,project_id=pid,amount='0',remark='=1+2')
    expense=client.get('/api/admin/workbench').json['manual_expenses'][0]
    assert expense['amount_cents']==0
    with zipfile.ZipFile(io.BytesIO(client.get(f'/admin/projects/{pid}/download').data)) as z:
        assert "'=1+2" in z.read('无电子发票报销项.csv').decode('utf-8-sig')
