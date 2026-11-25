"""Administrator-entered reimbursement items, separate from physical invoices."""
import uuid
from datetime import datetime,timezone

from flask import jsonify,request,session

from storage import BusinessError,cents,collect_manual_expenses,get_db,require_active_projects


def version_stamp():
    return datetime.now(timezone.utc).isoformat()+'-'+uuid.uuid4().hex[:8]


def require_open_task(task_id):
    row=get_db().execute('SELECT * FROM windows WHERE id=?',(task_id,)).fetchone()
    if row is None:raise BusinessError('收集任务不存在',404)
    if row['is_completed']:raise BusinessError('任务已结项，请先撤销结项再修改报销项',409)


def register_expenses(app,admin_required,login_required,payload,text_field):
    def values(data, existing=None):
        tid,uid,pid=data.get('task_id'),data.get('user_id'),data.get('project_id')
        if type(tid) is not int or type(uid) is not int:raise BusinessError('请选择收集任务和成员')
        require_open_task(tid)
        if existing is None or (uid,tid) != (existing['user_id'],existing['task_id']):
            if not get_db().execute('SELECT 1 FROM users WHERE id=? AND archived_username IS NULL',(uid,)).fetchone():
                raise BusinessError('成员不存在或已删除',404)
        category=text_field(data,'category',80)
        amount=cents(data.get('amount'))
        remark=text_field(data,'remark',4000,required=False)
        cid=None
        if pid is not None:
            if type(pid) is not int or not get_db().execute('SELECT 1 FROM projects WHERE id=?',(pid,)).fetchone():raise BusinessError('经费项目不存在',404)
            require_active_projects((pid,))
            get_db().execute('INSERT OR IGNORE INTO project_categories(project_id,name) VALUES(?,?)',(pid,category))
            cid=get_db().execute('SELECT id FROM project_categories WHERE project_id=? AND name=?',(pid,category)).fetchone()[0]
        return tid,uid,category,amount,remark,pid,cid

    @app.route('/api/admin/manual-expenses',methods=['POST'])
    @admin_required
    def create_manual_expense():
        db=get_db();db.execute('BEGIN IMMEDIATE')
        data=values(payload());mid=uuid.uuid4().hex;stamp=version_stamp()
        db.execute('INSERT INTO manual_expenses(id,task_id,user_id,category,amount_cents,remark,project_id,category_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(mid,*data,stamp,stamp))
        db.commit()
        return jsonify(status='success',id=mid)

    @app.route('/api/admin/manual-expenses/<expense_id>',methods=['PATCH','DELETE'])
    @admin_required
    def edit_manual_expense(expense_id):
        db=get_db();db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT * FROM manual_expenses WHERE id=? AND deleted_at IS NULL',(expense_id,)).fetchone()
        if row is None:raise BusinessError('无电子发票报销项不存在或已删除',404)
        require_open_task(row['task_id'])
        data=payload() if request.method=='PATCH' else (request.get_json(silent=True) or {})
        if not isinstance(data,dict):raise BusinessError('请求内容格式错误')
        if 'version' in data and data['version']!=row['updated_at']:raise BusinessError('此报销项已更新，请刷新后重试',409)
        stamp=version_stamp()
        if request.method=='DELETE':
            db.execute('UPDATE manual_expenses SET deleted_at=?,updated_at=? WHERE id=?',(stamp,stamp,expense_id))
        else:
            result=values(data, row)
            db.execute('UPDATE manual_expenses SET task_id=?,user_id=?,category=?,amount_cents=?,remark=?,project_id=?,category_id=?,updated_at=? WHERE id=?',(*result,stamp,expense_id))
        db.commit()
        return jsonify(status='success')

    @app.route('/api/user/manual-expenses')
    @login_required
    def member_manual_expenses():
        return jsonify(collect_manual_expenses(user_id=session['user_id']))
