import io
import json
import shutil
import zipfile

from conftest import pdf_bytes, project, invoices


def make_editable(module):
    with module.app.app_context():
        module.get_db().execute('UPDATE assignments SET status=1 WHERE window_id=1')
        module.get_db().commit()


def test_single_legacy_invoice_uses_saved_total_without_opening_pdf(site, legacy, monkeypatch):
    import storage
    directory, meta=legacy
    meta['invoice_files']=meta['invoice_files'][:1]
    meta['auto_amount']=88.12
    (directory/'meta.json').write_text(json.dumps(meta),encoding='utf-8')
    calls=[]
    monkeypatch.setattr(storage,'pdf_amount',lambda path: calls.append(path) or 0)
    rows=invoices(site[1])
    assert rows[0]['amount_cents']==8812
    assert calls==[]


def test_pdf_amount_cache_survives_memory_cache_clear_and_invalidates_on_file_change(site, monkeypatch):
    import storage
    module,_,root=site
    path=root/'test.pdf';path.write_bytes(pdf_bytes())
    with module.app.app_context():
        assert storage.pdf_amount(path)==0
        storage._pdf_amount.cache_clear()
        calls=[]
        class Reader:
            pages=[]
            def __init__(self,path):calls.append(path)
        monkeypatch.setattr(storage,'PdfReader',Reader)
        assert storage.pdf_amount(path)==0
        assert calls==[]
        path.write_bytes(pdf_bytes()+b'\n')
        assert storage.pdf_amount(path)==0
        assert len(calls)==1


def test_category_groups_keep_original_item_ids_and_sum_each_invoice(site,legacy):
    module,client,root=site
    make_editable(module)
    second=legacy[0].with_name('item_second');shutil.copytree(legacy[0],second)
    response=client.get('/api/user/tasks')
    task=next(t for t in response.json if t['id']==1)
    assert len(task['items'])==2
    assert len(task['groups'])==1
    group=task['groups'][0]
    assert group['type']=='专用材料费'
    assert group['amount']==200.02
    assert {f['item_id'] for f in group['invoices']}=={'old','second'}
    assert len(group['attachments'])==4


def test_legacy_missing_category_can_append_attachments_and_delete_as_other(site,legacy):
    module,client,_=site
    make_editable(module)
    directory,meta=legacy
    meta.pop('type',None)
    (directory/'meta.json').write_text(json.dumps(meta),encoding='utf-8')
    task=next(t for t in client.get('/api/user/tasks').json if t['id']==1)
    assert task['groups'][0]['type']=='其他'
    response=client.post('/api/user/upload_item/1',data={'type':'其他','attachments':(io.BytesIO(b'extra'),'proof.docx')})
    assert response.status_code==200,response.data
    response=client.post('/api/user/category/1/delete',json={'type':'其他'})
    assert response.status_code==200,response.data
    assert not directory.exists()
    task=next(t for t in client.get('/api/user/tasks').json if t['id']==1)
    assert task['groups']==[]


def test_can_remove_last_invoice_without_losing_attachments_and_later_append(site):
    module,client,root=site
    make_editable(module)
    data={'type':'专用材料费','invoice_files':(io.BytesIO(pdf_bytes()),'one.pdf'),'attachments':(io.BytesIO(pdf_bytes()),'proof.pdf')}
    item=client.post('/api/user/upload_item/1',data=data).json['item_id']
    response=client.delete(f'/api/user/item/1/{item}/files',json={'kind':'invoice','filename':'主发票_one.pdf'})
    assert response.status_code==200,response.data
    task=next(t for t in client.get('/api/user/tasks').json if t['id']==1)
    assert task['groups'][0]['invoices']==[]
    assert len(task['groups'][0]['attachments'])==1
    assert client.post('/api/user/submit/1').status_code==409
    response=client.post('/api/user/upload_item/1',data={'type':'专用材料费','invoice_files':(io.BytesIO(pdf_bytes()),'two.pdf')})
    assert response.status_code==200
    rows=invoices(client)
    assert len(rows)==1 and rows[0]['attachments'][0]['name']=='附件_proof.pdf'


def test_delete_whole_category_validates_every_batch_before_mutating(site,legacy):
    module,client,root=site
    second=legacy[0].with_name('item_second');shutil.copytree(legacy[0],second)
    pid=project(client)
    client.post('/api/admin/allocate',json={'invoice_ids':[invoices(client)[0]['id']],'project_id':pid})
    make_editable(module)
    response=client.post('/api/user/category/1/delete',json={'type':'专用材料费'})
    assert response.status_code==409
    assert legacy[0].exists() and second.exists()
    client.post('/api/admin/allocate',json={'invoice_ids':[invoices(client)[0]['id']],'project_id':None})
    response=client.post('/api/user/category/1/delete',json={'type':'专用材料费'})
    assert response.status_code==200,response.data
    assert not legacy[0].exists() and not second.exists()
    assert len(list((root/'.trash').glob('*/item_*')))==2


def test_appended_same_name_attachments_keep_distinct_archive_paths_and_metadata(site):
    module,client,_=site
    make_editable(module)
    data={'type':'专用材料费','invoice_files':(io.BytesIO(pdf_bytes()),'main.pdf'),'attachments':(io.BytesIO(b'first'),'proof.docx')}
    first=client.post('/api/user/upload_item/1',data=data).json['item_id']
    response=client.post('/api/user/upload_item/1',data={'type':'专用材料费','attachments':(io.BytesIO(b'second'),'proof.docx')})
    assert response.status_code==200,response.data
    second=response.json['item_id']
    client.post(f'/api/user/item/1/{first}/amount',json={'amount':'12.00'})
    assert client.post('/api/user/submit/1').status_code==200
    row=invoices(client)[0];pid=project(client)
    client.post('/api/admin/allocate',json={'invoice_ids':[row['id']],'project_id':pid})
    for url in [f'/admin/projects/{pid}/download','/admin/download/1']:
        response=client.get(url)
        assert response.status_code==200,response.data
        with zipfile.ZipFile(io.BytesIO(response.data)) as bundle:
            assert len(bundle.namelist())==len(set(bundle.namelist()))
            attachments=[n for n in bundle.namelist() if n.endswith('proof.docx')]
            assert len(attachments)==2
            assert sorted(bundle.read(n) for n in attachments)==[b'first',b'second']
            if url=='/admin/download/1':
                assert any(f'item_{second}/meta.json' in n for n in bundle.namelist())
