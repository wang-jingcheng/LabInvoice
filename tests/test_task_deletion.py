import json

from conftest import invoices, project


def test_delete_task_archives_files_allocations_and_removes_only_target_task(site,legacy):
    module,client,root=site
    pid=project(client)
    rows=invoices(client)
    client.post('/api/admin/allocate',json={'invoice_ids':[r['id'] for r in rows],'project_id':pid})
    client.post('/api/admin/invoices/amount',json={'invoice_id':rows[0]['id'],'amount':'88.12'})
    original={p.name:p.read_bytes() for p in legacy[0].iterdir()}
    with module.app.app_context():
        module.get_db().execute('UPDATE windows SET is_completed=1 WHERE id=1');module.get_db().commit()
    response=client.post('/api/admin/window/delete/1',json={'confirmed':True})
    assert response.status_code==200,response.data
    archive=root/response.json['archive']
    snapshot=json.loads((archive/'task-records.json').read_text(encoding='utf-8'))
    assert snapshot['task']['id']==1 and snapshot['task']['is_completed']==1
    assert len(snapshot['assignments'])==1
    assert len(snapshot['allocations'])==2
    assert snapshot['allocations'][0]['amount_cents']==8812
    archived=archive/'task_1'/'测试成员'/'item_old'
    assert {p.name:p.read_bytes() for p in archived.iterdir()}==original
    assert not (root/'uploads/task_1').exists()
    data=client.get('/api/admin/workbench').json
    assert len(data['windows'])==1 and data['windows'][0]['id']==2
    assert data['invoices']==[]
    assert data['projects'][0]['total_cents']==0
    assert len(data['users'])==2
    assert [t['id'] for t in client.get('/api/user/tasks').json]==[2]
    assert client.post('/api/admin/window/delete/1',json={'confirmed':True}).status_code==404
    assert len(list((root/'.trash').glob('task-delete-*')))==1


def test_delete_empty_task_and_require_admin(site):
    module,client,root=site
    with client.session_transaction() as session:
        session.pop('admin_logged_in')
    assert client.post('/api/admin/window/delete/1',json={'confirmed':True}).status_code==401
    with client.session_transaction() as session:
        session['admin_logged_in']=True
    assert client.post('/api/admin/window/delete/999',json={'confirmed':True}).status_code==404
    assert not (root/'.trash').exists()
    response=client.post('/api/admin/window/delete/1',json={'confirmed':True})
    assert response.status_code==200
    archive=root/response.json['archive']
    assert (archive/'task-records.json').is_file()
    assert not (archive/'task_1').exists()


def test_delete_task_rolls_back_database_and_files_when_database_delete_fails(site,legacy):
    module,client,root=site
    with module.app.app_context():
        db=module.get_db()
        db.execute("CREATE TRIGGER reject_task_delete BEFORE DELETE ON windows BEGIN SELECT RAISE(ABORT,'test failure'); END")
        db.commit()
    import sqlite3
    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        client.post('/api/admin/window/delete/1',json={'confirmed':True})
    assert legacy[0].exists()
    assert len(invoices(client))==2
    with module.app.app_context():
        assert module.get_db().execute('SELECT count(*) FROM assignments WHERE window_id=1').fetchone()[0]==1
        assert module.get_db().execute('SELECT id FROM windows WHERE id=1').fetchone()


def test_delete_task_does_not_remove_database_records_when_archive_write_fails(site,legacy,monkeypatch):
    module,client,root=site
    def fail(*args,**kwargs):raise OSError('cannot archive')
    monkeypatch.setattr(module,'atomic_json',fail)
    import pytest
    with pytest.raises(OSError):client.post('/api/admin/window/delete/1',json={'confirmed':True})
    assert legacy[0].exists()
    assert len(invoices(client))==2
