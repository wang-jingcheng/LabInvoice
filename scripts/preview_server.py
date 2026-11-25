"""Local demo/test server. Seeds ONLY .test-data/preview (never real data)."""
import io
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DEMO_ROOT = (ROOT / '.test-data').resolve()
DEMO_DIR = (DEMO_ROOT / os.environ.get('LABINVOICE_PREVIEW_NAME', 'preview')).resolve()
if DEMO_DIR == DEMO_ROOT or not DEMO_DIR.is_relative_to(DEMO_ROOT):
    raise RuntimeError('Preview data must stay inside .test-data')
FIXTURE_DIR = DEMO_DIR / 'fixtures'
os.environ['LABINVOICE_DATA_DIR'] = str(DEMO_DIR)
os.environ['LABINVOICE_ADMIN_PASSWORD'] = 'preview-admin-2026'
os.environ.pop('LABINVOICE_SECRET_KEY', None)
from app import app, get_db
app.jinja_env.auto_reload = True
from storage import atomic_json
from reportlab.pdfgen import canvas
from werkzeug.security import generate_password_hash


def pdf(amount):
    data = io.BytesIO()
    document = canvas.Canvas(data, pagesize=(420, 260))
    document.setFont('Helvetica-Bold', 18)
    document.drawString(35, 210, 'LABINVOICE / TEST INVOICE')
    document.setFont('Helvetica', 13)
    document.drawString(35, 150, 'Total amount: ' + chr(165) + f'{amount:.2f}')
    document.setFont('Helvetica', 9)
    document.drawString(35, 70, 'Demonstration data only - not a real invoice')
    document.save()
    return data.getvalue()


def seed(*, complete_first_task=True):
    with app.app_context():
        db=get_db()
        if db.execute('SELECT count(*) FROM users').fetchone()[0]:
            return
        names=['林晓','陈雨','周予安','许知遥','沈星','顾宁']
        for index,name in enumerate(names,1):
            db.execute('INSERT INTO users(id,username,password,real_name) VALUES(?,?,?,?)',(index,'demo' if index==1 else f'member{index}',generate_password_hash('demo-only'),name))
        for tid,title,completed in [(1,'2026 年 09 月 · 日常票据收集',int(complete_first_task)),(2,'2026 年 10 月 · 日常票据收集',0)]:
            db.execute('INSERT INTO windows(id,title,task_type,is_completed) VALUES(?,?,?,?)',(tid,title,'日常业务报销',completed))
            for index in range(1,7):
                db.execute('INSERT INTO assignments(window_id,user_id,status) VALUES(?,?,?)',(tid,index,2 if tid==1 else 0))
        db.execute("INSERT INTO projects(id,name,code) VALUES(1,'认知实验示例','DEMO-001')")
        db.execute("INSERT INTO projects(id,name,code) VALUES(2,'实验平台建设示例','DEMO-002')")
        for cid,pid,name in [(1,1,'市内交通费'),(2,1,'专用材料费'),(3,2,'印刷费')]:
            db.execute('INSERT INTO project_categories(id,project_id,name) VALUES(?,?,?)',(cid,pid,name))
        kinds=['市内交通费','专用材料费','邮电费','专用材料费','印刷费','其他','市内交通费','专用材料费','印刷费','专用材料费','邮电费','其他']
        values=[74.8,680,28,315.5,126,82,48,259,93.2,425,36,165]
        for index,(kind,value) in enumerate(zip(kinds,values),1):
            owner=names[(index-1)%6]
            item_id=f'demo-{index:03}'
            filename=f'主发票_{["市内出行电子发票","实验试剂采购发票","快递服务电子发票","移液器吸头采购发票","资料印刷电子发票","实验配件电子发票"][((index-1)%6)]}_{index:02}.pdf'
            directory=DEMO_DIR/'uploads'/'task_1'/owner/f'item_{item_id}'
            directory.mkdir(parents=True,exist_ok=True)
            (directory/filename).write_bytes(pdf(value))
            (directory/'附件_业务证明.pdf').write_bytes(pdf(0))
            atomic_json(directory/'meta.json',{'id':item_id,'type':kind,'remark':'实验室日常工作支出','invoice_files':[filename],'attachments':['附件_业务证明.pdf'],'invoice_amounts':{filename:value},'auto_amount':value})
            if index in [1,2,4,5]:
                pid,cid=(2,3) if index==5 else (1,1 if kind=='市内交通费' else 2)
                db.execute('INSERT INTO invoice_allocations VALUES(?,?,?,?,?,?,?,?)',(1,owner,item_id,filename,pid,cid,None,'seed-1'))
        db.commit()
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for name,value in [('browser-first.pdf',31.25),('browser-second.pdf',46.8),('browser-extra.pdf',12.1),('browser-attachment.pdf',0)]:
        (FIXTURE_DIR/name).write_bytes(pdf(value))


if __name__=='__main__':
    seed(complete_first_task=False)
    app.run(host='127.0.0.1',port=8791,debug=False)
