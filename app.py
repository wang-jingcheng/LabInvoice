"""LabInvoice: original collections plus a non-destructive project ledger."""
import os
import json
import re
import secrets
import shutil
import sqlite3
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

from flask import Flask, abort, g, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from storage import (ATTACHMENT_EXTENSIONS, DAILY_CATEGORIES, DEFAULT_GUIDE, INVOICE_EXTENSIONS,
                     TRAVEL_CATEGORIES, BusinessError, atomic_json, base_amounts, cents, close_db,
                     collect_invoices, collect_manual_expenses, distribute, extension, get_db, invoice_selection, member_owner, migrate,
                     pdf_amount, project_summaries, projects_with_archive_status, public, read_meta,
                     require_active_projects, save_allocation, secure_zh_filename, task_summaries, warm_invoice_cache)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("LABINVOICE_DATA_DIR", BASE_DIR)).resolve()
UPLOAD_FOLDER = DATA_DIR / "uploads"
DATABASE = DATA_DIR / "labinvoice.db"
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
app = Flask(__name__, template_folder=str(BASE_DIR / "templates"), static_folder=str(BASE_DIR / "static"))
app.config.update(SECRET_KEY=os.environ.get("LABINVOICE_SECRET_KEY") or None,
                  ADMIN_PASSWORD=os.environ.get("LABINVOICE_ADMIN_PASSWORD"),
                  APP_VERSION="3.1.1", ASSET_VERSION="3.1.1.2",
                  DATABASE=str(DATABASE), DATA_DIR=str(DATA_DIR), UPLOAD_FOLDER=str(UPLOAD_FOLDER),
                  PERMANENT_SESSION_LIFETIME=timedelta(days=30), MAX_CONTENT_LENGTH=128 * 1024 * 1024,
                  SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
app.teardown_appcontext(close_db)


def init_db():
    with app.app_context():
        migrate()


init_db()
with app.app_context():
    warm_invoice_cache()

# Persist a random key once, shared by all workers. Existing accounts and files
# remain unchanged; old browser sessions need to sign in again after upgrade.
if not app.secret_key:
    with app.app_context():
        db = get_db()
        db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('session_secret',?)", (secrets.token_urlsafe(48),))
        db.commit()
        app.secret_key = db.execute("SELECT value FROM settings WHERE key='session_secret'").fetchone()[0]


@app.context_processor
def template_context():
    return {"csrf_token": session.setdefault("csrf_token", secrets.token_urlsafe(32)),
            "asset_version": app.config["ASSET_VERSION"],
            "app_version": app.config["APP_VERSION"]}


@app.before_request
def protect_mutations():
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        expected = session.get("csrf_token", "")
        provided = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
        if not expected or not secrets.compare_digest(expected.encode('utf-8'), provided.encode('utf-8')):
            raise BusinessError("页面验证已过期，请刷新后重试", 403)


@app.errorhandler(BusinessError)
def business_error(error):
    return jsonify(status="error", message=error.message), error.status


@app.errorhandler(413)
def upload_too_large(_error):
    return jsonify(status="error", message="本次上传超过 128 MB，请分批上传；部署网关也可能设置了更低的限制"), 413


def role_required(role):
    def decorator(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            if role == "admin_logged_in" and not app.config["ADMIN_PASSWORD"]:
                session.pop(role, None)
            if role == "user_id" and session.get("user_id"):
                g.member = get_db().execute("""
                    SELECT u.username,u.real_name,u.storage_owner,COALESCE(s.value,'0') AS password_version
                    FROM users u LEFT JOIN settings s ON s.key='password_version:' || u.id
                    WHERE u.id=? AND u.archived_username IS NULL
                """, (session['user_id'],)).fetchone()
                if not g.member or session.get("password_version", "0") != g.member["password_version"]:
                    for key in ("user_id", "username", "real_name", "password_version"):
                        session.pop(key, None)
                elif session.get('real_name') != g.member['real_name'] or session.get('username') != g.member['username']:
                    session.update(real_name=g.member['real_name'], username=g.member['username'])
            if not session.get(role):
                if request.path.startswith("/api/") or request.is_json:
                    raise BusinessError("请先登录", 401)
                return redirect(url_for("admin_login" if role == "admin_logged_in" else "login"))
            return function(*args, **kwargs)
        return wrapped
    return decorator


login_required = role_required("user_id")
admin_required = role_required("admin_logged_in")


def payload():
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise BusinessError("请求内容格式错误")
    return value


def text_field(data, name, limit=120, required=True):
    value = data.get(name, "")
    if not isinstance(value, str):
        raise BusinessError("字段格式错误：" + name)
    value = value.strip()
    if len(value) > limit or (required and not value):
        raise BusinessError(f"请填写有效内容（{name}，最多 {limit} 字）")
    return value


def existing_project(project_id):
    if type(project_id) is not int:
        raise BusinessError("请选择有效项目")
    row = get_db().execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if row is None:
        raise BusinessError("项目不存在", 404)
    return dict(row)


def user_owner():
    return member_owner(g.member)


def editable_task(task_id):
    row = get_db().execute("SELECT w.*,a.status FROM assignments a JOIN windows w ON w.id=a.window_id WHERE a.window_id=? AND a.user_id=?", (task_id, session["user_id"])).fetchone()
    if row is None:
        raise BusinessError("任务不存在或未分配给您", 403)
    if row["is_completed"] or row['is_collection_closed'] or row["status"] == 2:
        raise BusinessError("任务已锁定，请联系管理员退回或重新开放", 409)
    return row


def item_directory(task_id, owner, item_id):
    if item_id != secure_zh_filename(item_id) or owner != secure_zh_filename(owner):
        raise BusinessError("无效的记录路径")
    root = Path(app.config["UPLOAD_FOLDER"]).resolve()
    path = root / f"task_{task_id}" / owner / f"item_{item_id}"
    if not path.resolve().is_relative_to(root) or path.is_symlink():
        raise BusinessError("无效的记录路径")
    return path


def ensure_item_editable(task_id, item_id):
    editable_task(task_id)
    if get_db().execute("SELECT 1 FROM invoice_allocations WHERE window_id=? AND owner=? AND item_id=? AND project_id IS NOT NULL", (task_id, user_owner(), item_id)).fetchone():
        raise BusinessError("此记录已归入经费项目，请联系管理员先移回待归类", 409)
    directory = item_directory(task_id, user_owner(), item_id)
    if not (directory / "meta.json").is_file():
        raise BusinessError("记录不存在", 404)
    return directory


def trash_path(path):
    """Requested deletions remain recoverable; no recursive live-data deletion."""
    path = Path(path).resolve()
    if not path.is_relative_to(Path(app.config["UPLOAD_FOLDER"]).resolve()):
        raise BusinessError("无效文件路径")
    data_root = Path(app.config["DATA_DIR"]).resolve()
    destination = (data_root / ".trash" / uuid.uuid4().hex / path.name).resolve()
    if not destination.is_relative_to(data_root):
        raise BusinessError("无效回收目录")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(destination))
    return destination


def incoming_files(invoice_field="invoice_files", attachment_field="attachments"):
    invoices = [f for f in request.files.getlist(invoice_field) if f and f.filename]
    attachments = [f for f in request.files.getlist(attachment_field) if f and f.filename]
    for file, allowed in [(f, INVOICE_EXTENSIONS) for f in invoices] + [(f, ATTACHMENT_EXTENSIONS) for f in attachments]:
        if extension(file.filename) not in allowed:
            raise BusinessError("不支持此文件类型：" + file.filename)
    return invoices, attachments


def save_files(directory, files, prefix):
    saved, amounts = [], {}
    for file in files:
        filename = prefix + secure_zh_filename(file.filename)
        stem, suffix = os.path.splitext(filename)
        counter = 2
        while (directory / filename).exists():
            filename = f"{stem} ({counter}){suffix}"
            counter += 1
        file.save(directory / filename)
        saved.append(filename)
        if prefix == "主发票_":
            try:
                if extension(filename) == "pdf":
                    from pypdf import PdfReader
                    reader = PdfReader(directory / filename)
                    if not reader.pages:
                        raise ValueError("empty PDF")
                    recognized = pdf_amount(directory / filename)
                    amounts[filename] = recognized / 100 if recognized else None
                else:
                    from PIL import Image
                    with Image.open(directory / filename) as image:
                        image.verify()
                    amounts[filename] = None
            except Exception:
                raise BusinessError("主发票不是可读取的 PDF 或图片（可能损坏、加密或格式不支持）：" + file.filename)
    return saved, amounts


def amount_total(amounts):
    return None if any(value is None for value in amounts.values()) else sum(cents(value, allow_negative=True) for value in amounts.values()) / 100


def set_user_password(user_id, password):
    if not isinstance(password, str) or not 6 <= len(password.strip()) <= 120:
        raise BusinessError("新密码需为 6 至 120 个字符")
    db = get_db()
    version = uuid.uuid4().hex
    with db:
        changed = db.execute("UPDATE users SET password=? WHERE id=? AND archived_username IS NULL", (generate_password_hash(password.strip()), user_id))
        if not changed.rowcount:
            raise BusinessError("成员不存在", 404)
        db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (f"password_version:{user_id}", version))
    return version


@app.route("/api/user/password", methods=["POST"])
@login_required
def change_own_password():
    data = payload()
    current = data.get("current_password", "")
    user = get_db().execute("SELECT password FROM users WHERE id=?", (session['user_id'],)).fetchone()
    if not isinstance(current, str) or not user or not check_password_hash(user['password'], current.strip()):
        raise BusinessError("当前密码不正确")
    if data.get('new_password') != data.get('confirm_password'):
        raise BusinessError("两次输入的新密码不一致")
    session['password_version'] = set_user_password(session['user_id'], data.get('new_password'))
    return jsonify(status='success')


@app.route("/api/admin/users/<int:user_id>/password", methods=["POST"])
@admin_required
def reset_member_password(user_id):
    set_user_password(user_id, payload().get('new_password'))
    return jsonify(status='success')


@app.route("/login", methods=["GET", "POST"])
def login():
    wants_json = request.accept_mimetypes.best == "application/json"
    if session.get("user_id") and request.method == "GET":
        return redirect(url_for("index"))
    if request.method == "POST":
        user = get_db().execute("SELECT * FROM users WHERE username=? AND archived_username IS NULL", (request.form.get("username", "").strip(),)).fetchone()
        if user and check_password_hash(user["password"], request.form.get("password", "").strip()):
            session.permanent = bool(request.form.get("remember"))
            session.update(user_id=user["id"], username=user["username"], real_name=user["real_name"])
            row = get_db().execute("SELECT value FROM settings WHERE key=?", (f"password_version:{user['id']}",)).fetchone()
            session["password_version"] = row[0] if row else "0"
            if wants_json:
                return jsonify(status="success", redirect=url_for("index"))
            return redirect(url_for("index"))
        if wants_json:
            raise BusinessError("用户名或密码错误", 400)
        return render_template("login.html", error="用户名或密码错误")
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        values = {key: request.form.get(key, "").strip() for key in ["username", "password", "real_name"]}
        if not all(values.values()) or any(len(v) > 120 for v in values.values()) or values["real_name"] != secure_zh_filename(values["real_name"]):
            return render_template("register.html", error="请完整填写账号、密码和真实姓名；姓名不能包含路径符号")
        try:
            create_member(values['username'], values['real_name'], values['password'])
            return redirect(url_for("login"))
        except BusinessError as error:
            return render_template("register.html", error=error.message)
    return render_template("register.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/help")
def help_page():
    from guide import guide_document, render_guide
    content, mode = guide_document()
    return render_template("help.html", content=content, guide_format=mode, guide_html=render_guide(content, mode))


@app.route("/")
@login_required
def index():
    return render_template("index.html", real_name=session.get("real_name"))


@app.route("/api/user/tasks")
@login_required
def get_user_tasks():
    rows, warnings = collect_invoices(owner=user_owner())
    result = []
    for task in get_db().execute("SELECT w.*,a.status FROM assignments a JOIN windows w ON w.id=a.window_id WHERE a.user_id=? ORDER BY w.id DESC", (session["user_id"],)):
        data = dict(task)
        data["items"] = []
        directory = Path(app.config["UPLOAD_FOLDER"]) / f"task_{task['id']}" / user_owner()
        for folder in sorted(directory.glob("item_*")):
            if not (folder / "meta.json").is_file():
                continue
            try:
                meta = read_meta(folder)
            except (ValueError, OSError, TypeError):
                continue
            lines = [r for r in rows if r["task_id"] == task["id"] and r["item_id"] == meta["id"]]
            if lines:
                meta["invoice_amounts"] = {r["filename"]: r["amount"] for r in lines}
                meta["auto_amount"] = amount_total(meta["invoice_amounts"])
            meta["amount_pending"] = any(r["amount_pending"] for r in lines)
            meta["project_locked"] = any(r["project_id"] is not None for r in lines)
            data["items"].append(meta)
        data["pending_amount_count"] = sum(r["amount_pending"] for r in rows if r["task_id"] == task["id"])
        data["final_amount"] = sum(r["amount_cents"] for r in rows if r["task_id"] == task["id"]) / 100
        data['groups'] = user_category_groups(data['id'], data['items'])
        data["warnings"] = warnings
        result.append(data)
    return jsonify(result)


def user_category_groups(task_id, items):
    """Group the view without renaming files or changing historical allocation IDs."""
    from urllib.parse import quote
    groups = {}
    for item in items:
        kind = item.get('type') or '其他'
        group = groups.setdefault(kind, {'type':kind, 'invoices':[], 'attachments':[], 'item_ids':[], 'remarks':[], 'project_locked':False})
        group['item_ids'].append(item['id'])
        group['project_locked'] |= item['project_locked']
        if item.get('remark') and item['remark'] not in group['remarks']:
            group['remarks'].append(item['remark'])
        for field, destination in [('invoice_files','invoices'), ('attachments','attachments')]:
            for filename in item[field]:
                group[destination].append({'filename':filename, 'item_id':item['id'],
                    'amount':item.get('invoice_amounts',{}).get(filename), 'locked':item['project_locked'],
                    'url':f'/api/user/view_file/{task_id}/{quote(item["id"],safe="")}/{quote(filename,safe="")}'})
    for group in groups.values():
        group['amount'] = amount_total({str(i):file['amount'] for i,file in enumerate(group['invoices'])})
    return list(groups.values())


@app.route('/api/user/category/<int:task_id>/delete', methods=['POST'])
@login_required
def delete_user_category(task_id):
    db = get_db()
    db.execute('BEGIN IMMEDIATE')
    editable_task(task_id)
    kind = text_field(payload(), 'type')
    folder = Path(app.config['UPLOAD_FOLDER']) / f'task_{task_id}' / user_owner()
    records = [p for p in folder.glob('item_*') if (read_meta(p).get('type') or '其他') == kind]
    if not records:
        raise BusinessError('报销项不存在', 404)
    for record in records:
        ensure_item_editable(task_id, read_meta(record)['id'])
    moved = []
    try:
        for record in records:
            item_id = read_meta(record)['id']
            moved.append((record, trash_path(record)))
            db.execute('DELETE FROM invoice_allocations WHERE window_id=? AND owner=? AND item_id=?', (task_id,user_owner(),item_id))
        db.commit()
    except Exception:
        db.rollback()
        for source, destination in reversed(moved):
            if destination.exists():
                shutil.move(str(destination), str(source))
        raise
    return jsonify(status='success')


def serve_record_file(task_id, owner, item_id, filename):
    directory = item_directory(task_id, owner, item_id)
    try:
        meta = read_meta(directory)
    except (OSError, ValueError):
        abort(404)
    if filename not in meta["invoice_files"] + meta["attachments"] or not (directory / filename).is_file():
        abort(404)
    return send_file(directory / filename)


@app.route("/api/user/view_file/<int:task_id>/<item_id>/<filename>")
@login_required
def user_view_file(task_id, item_id, filename):
    if not get_db().execute("SELECT 1 FROM assignments WHERE window_id=? AND user_id=?", (task_id, session["user_id"])).fetchone():
        abort(403)
    return serve_record_file(task_id, user_owner(), item_id, filename)


@app.route("/api/user/upload_item/<int:task_id>", methods=["POST"])
@login_required
def user_upload_item(task_id):
    invoices, attachments = incoming_files()
    groups = [(request.form.get('type', '').strip(), request.form.get('remark', ''), invoices, attachments)]
    ids = save_upload_groups(task_id, groups)
    return jsonify(status='success', item_id=ids[0])


@app.route('/api/user/upload_items/<int:task_id>', methods=['POST'])
@login_required
def user_upload_items(task_id):
    try:
        manifest = json.loads(request.form.get('groups', ''))
    except (ValueError, TypeError):
        raise BusinessError('上传分组格式错误，请重新选择文件')
    if not isinstance(manifest, list) or not manifest or len(manifest) > 20:
        raise BusinessError('请至少准备一个报销项，每次最多保存 20 项')
    groups = []
    seen = set()
    for index, group in enumerate(manifest):
        if not isinstance(group, dict):
            raise BusinessError('上传分组格式错误')
        kind = text_field(group, 'type')
        if kind in seen:
            raise BusinessError('同一报销项请合并后保存')
        seen.add(kind)
        invoices, attachments = incoming_files(f'invoices_{index}', f'attachments_{index}')
        groups.append((kind, text_field(group, 'remark', 4000, required=False), invoices, attachments))
    return jsonify(status='success', item_ids=save_upload_groups(task_id, groups))


def save_upload_groups(task_id, groups):
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    task = editable_task(task_id)
    categories = TRAVEL_CATEGORIES if task["task_type"] == "国内差旅费" else DAILY_CATEGORIES
    folder = Path(app.config['UPLOAD_FOLDER']) / f'task_{task_id}' / user_owner()
    existing_invoices = set()
    for path in folder.glob('item_*'):
        meta = read_meta(path)
        if meta['invoice_files']:
            existing_invoices.add(meta.get('type') or '其他')
    for kind, remark, invoices, attachments in groups:
        if kind not in categories or not (invoices or attachments) or (not invoices and kind not in existing_invoices):
            raise BusinessError(f'{kind or "报销项"}：请至少上传一张主发票')
        if kind == '市内交通费' and invoices and not attachments:
            raise BusinessError('市内交通费请上传对应行程单附件')
    stages, published, ids = [], [], []
    try:
        for kind, remark, invoices, attachments in groups:
            item_id = uuid.uuid4().hex
            directory = item_directory(task_id, user_owner(), item_id)
            stage = directory.with_name('.pending-' + item_id)
            stage.mkdir(parents=True)
            stages.append((stage, directory))
            saved, amounts = save_files(stage, invoices, '主发票_')
            extra, _ = save_files(stage, attachments, '附件_')
            meta = {'id': item_id, 'type': kind, 'remark': remark.strip()[:4000],
                    'invoice_files': saved, 'attachments': extra, 'invoice_amounts': amounts,
                    'auto_amount': amount_total(amounts)}
            atomic_json(stage / 'meta.json', meta)
            ids.append(item_id)
        for stage, directory in stages:
            os.replace(stage, directory)
            published.append(directory)
        db.execute("UPDATE assignments SET status=1 WHERE window_id=? AND user_id=?", (task_id, session["user_id"]))
        db.commit()
    except Exception:
        db.rollback()
        for path in [stage for stage, _ in stages] + published:
            if path.exists():
                trash_path(path)
        raise
    return ids


@app.route("/api/user/item/<int:task_id>/<item_id>/files", methods=["POST", "DELETE"])
@login_required
def edit_item_files(task_id, item_id):
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    directory = ensure_item_editable(task_id, item_id)
    meta = read_meta(directory)
    lines, _ = collect_invoices(task_id, user_owner())
    effective = {r['filename']: None if r['amount_pending'] else r['amount_cents'] for r in lines if r['item_id'] == item_id}
    base, _ = base_amounts(meta, directory)
    old_amounts = [effective.get(name, value) for name, value in zip(meta['invoice_files'], base)]
    amounts = dict(zip(meta["invoice_files"], (None if v is None else v / 100 for v in old_amounts)))
    if request.method == "DELETE":
        data = payload()
        field = "invoice_files" if data.get("kind") == "invoice" else "attachments"
        filename = data.get("filename")
        if filename not in meta[field]:
            raise BusinessError("文件不存在", 404)
        meta[field].remove(filename)
        amounts.pop(filename, None)
        db.execute("DELETE FROM invoice_allocations WHERE window_id=? AND owner=? AND item_id=? AND filename=?", (task_id, user_owner(), item_id, filename))
    else:
        invoices, attachments = incoming_files()
        if not invoices and not attachments:
            raise BusinessError("请选择需要追加的文件")
        saved, new_amounts = save_files(directory, invoices, "主发票_")
        extra, _ = save_files(directory, attachments, "附件_")
        meta["invoice_files"].extend(saved)
        meta["attachments"].extend(extra)
        amounts.update(new_amounts)
    meta["invoice_amounts"] = amounts
    meta["auto_amount"] = amount_total(amounts)
    meta.pop("invoice_file", None)
    atomic_json(directory / "meta.json", meta)
    db.execute("UPDATE invoice_allocations SET amount_cents=NULL WHERE window_id=? AND owner=? AND item_id=?", (task_id, user_owner(), item_id))
    db.commit()
    return jsonify(status="success")


@app.route("/api/user/item/<int:task_id>/<item_id>/amount", methods=["POST"])
@login_required
def update_item_amount_user(task_id, item_id):
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    directory = ensure_item_editable(task_id, item_id)
    data = payload()
    amount = cents(data.get("amount"))
    meta = read_meta(directory)
    lines, _ = collect_invoices(task_id, user_owner())
    amounts = {r["filename"]: r["amount"] for r in lines if r["item_id"] == item_id}
    filename = data.get("filename")
    if filename is not None:
        if not isinstance(filename, str) or filename not in amounts:
            raise BusinessError("文件不存在", 404)
        amounts[filename] = amount / 100
    else:
        if len(amounts) > 1 and any(value is None for value in amounts.values()):
            raise BusinessError("含有待填写金额的发票，请逐张填写并保存")
        previous = [cents(amounts[name] or 0, allow_negative=True) for name in meta["invoice_files"]]
        amounts = dict(zip(meta["invoice_files"], (v / 100 for v in distribute(amount, previous))))
    meta["invoice_amounts"] = amounts
    meta["auto_amount"] = amount_total(amounts)
    meta.pop("is_admin_calibrated", None)
    atomic_json(directory / "meta.json", meta)
    db.execute("UPDATE invoice_allocations SET amount_cents=NULL WHERE window_id=? AND owner=? AND item_id=?", (task_id, user_owner(), item_id))
    db.commit()
    return jsonify(status="success")


@app.route("/api/user/item/<int:task_id>/<item_id>", methods=["DELETE"])
@login_required
def delete_item(task_id, item_id):
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    directory = ensure_item_editable(task_id, item_id)
    trash_path(directory)
    db.execute("DELETE FROM invoice_allocations WHERE window_id=? AND owner=? AND item_id=?", (task_id, user_owner(), item_id))
    db.commit()
    return jsonify(status="success")


@app.route("/api/user/submit/<int:task_id>", methods=["POST"])
@login_required
def submit_task(task_id):
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    editable_task(task_id)
    rows, warnings = collect_invoices(task_id, user_owner())
    if warnings:
        raise BusinessError("部分票据无法读取，请联系管理员检查后再提交", 409)
    if any(row["amount_pending"] for row in rows):
        raise BusinessError("请先逐张填写并保存未识别的发票金额，再正式提交", 409)
    folder = Path(app.config['UPLOAD_FOLDER']) / f'task_{task_id}' / user_owner()
    category_counts = defaultdict(int)
    for directory in folder.glob('item_*'):
        meta = read_meta(directory)
        category_counts[meta.get('type') or '其他'] += len(meta['invoice_files'])
    if any(count == 0 for count in category_counts.values()):
        raise BusinessError('有报销项尚无主发票，请补充或删除该报销项后提交',409)
    db.execute("UPDATE assignments SET status=2 WHERE window_id=? AND user_id=?", (task_id, session["user_id"]))
    db.commit()
    return jsonify(status="success")


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    password = app.config["ADMIN_PASSWORD"]
    if not password:
        return render_template("admin_login.html", setup_required=True)
    if request.method == "POST":
        if secrets.compare_digest(request.form.get("password", "").encode("utf-8"), password.encode("utf-8")):
            session["admin_logged_in"] = True
            return redirect(url_for("admin_dashboard"))
        return render_template("admin_login.html", error="管理员密码错误")
    return render_template("admin_login.html")


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("admin_login"))


@app.route("/admin")
@admin_required
def admin_dashboard():
    return render_template("admin_dashboard.html")


@app.route("/api/admin/workbench")
@admin_required
def workbench():
    db = get_db()
    with db:
        # Keep invoice visibility and project archive status on the same task snapshot.
        db.execute("BEGIN")
        rows, warnings = collect_invoices()
        manual=collect_manual_expenses()
        windows = [dict(r) for r in db.execute("SELECT * FROM windows ORDER BY id DESC")]
        users = [dict(r) for r in db.execute("SELECT id,username,real_name FROM users WHERE archived_username IS NULL ORDER BY id DESC")]
        assignments = [dict(r) for r in db.execute("SELECT a.*,u.real_name,u.archived_username IS NOT NULL AS account_deleted FROM assignments a JOIN users u ON u.id=a.user_id")]
        return jsonify(invoices=public(rows), manual_expenses=manual, projects=project_summaries(rows,manual), windows=windows, users=users, assignments=assignments, warnings=warnings,
                       task_summaries=task_summaries(rows, windows,manual))


@app.route('/api/admin/overview')
@admin_required
def admin_overview():
    db=get_db()
    projects=[dict(p, categories=[], users=[]) for p in projects_with_archive_status()]
    return jsonify(loading=True, invoices=[], projects=projects, warnings=[],
        windows=[dict(r) for r in db.execute('SELECT * FROM windows ORDER BY id DESC')],
        users=[dict(r) for r in db.execute('SELECT id,username,real_name FROM users WHERE archived_username IS NULL ORDER BY id DESC')],
        assignments=[dict(r) for r in db.execute('SELECT a.*,u.real_name,u.archived_username IS NOT NULL AS account_deleted FROM assignments a JOIN users u ON u.id=a.user_id')])


@app.route("/api/admin/projects", methods=["POST"])
@admin_required
def create_project():
    data = payload()
    name, code = text_field(data, "name", required=False), text_field(data, "code", required=False)
    if not name and not code:
        raise BusinessError("请填写项目名称或项目号")
    cursor = get_db().execute("INSERT INTO projects(name,code) VALUES(?,?)", (name or code, code))
    remember_project(name or code, code)
    get_db().commit()
    return jsonify(status="success", id=cursor.lastrowid)


@app.route("/api/admin/projects/<int:project_id>", methods=["PATCH", "DELETE"])
@admin_required
def edit_project(project_id):
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    existing_project(project_id)
    require_active_projects((project_id,))
    if request.method == "DELETE":
        if db.execute("SELECT 1 FROM invoice_allocations WHERE project_id=?", (project_id,)).fetchone() or db.execute('SELECT 1 FROM manual_expenses WHERE project_id=? AND deleted_at IS NULL',(project_id,)).fetchone():
            if request.args.get('unassign') != '1':
                raise BusinessError("项目仍有发票，请先移出发票再删除", 409)
            db.execute("UPDATE invoice_allocations SET project_id=NULL,category_id=NULL,updated_at=? WHERE project_id=?",
                       (datetime.now(timezone.utc).isoformat(), project_id))
        db.execute('UPDATE manual_expenses SET category=COALESCE((SELECT name FROM project_categories WHERE id=manual_expenses.category_id),category),project_id=NULL,category_id=NULL,updated_at=? WHERE project_id=?',(datetime.now(timezone.utc).isoformat(),project_id))
        db.execute("DELETE FROM project_categories WHERE project_id=?", (project_id,))
        db.execute("DELETE FROM projects WHERE id=?", (project_id,))
    else:
        data = payload()
        name, code = text_field(data, "name", required=False), text_field(data, "code", required=False)
        if not name and not code:
            raise BusinessError("请填写项目名称或项目号")
        db.execute("UPDATE projects SET name=?,code=? WHERE id=?", (name or code, code, project_id))
        remember_project(name or code, code)
    db.commit()
    return jsonify(status="success")


def remember_project(name, code):
    get_db().execute("""INSERT INTO project_input_history(name,code) VALUES(?,?)
        ON CONFLICT(name,code) DO UPDATE SET updated_at=CURRENT_TIMESTAMP""", (name, code))


@app.route('/api/admin/project-history')
@admin_required
def project_history():
    return jsonify([dict(r) for r in get_db().execute('SELECT * FROM project_input_history ORDER BY updated_at DESC,id DESC')])


@app.route('/api/admin/project-history/<int:history_id>', methods=['PATCH','DELETE'])
@admin_required
def edit_project_history(history_id):
    db = get_db()
    db.execute('BEGIN IMMEDIATE')
    if not db.execute('SELECT 1 FROM project_input_history WHERE id=?', (history_id,)).fetchone():
        raise BusinessError('历史记录不存在', 404)
    if request.method == 'DELETE':
        db.execute('DELETE FROM project_input_history WHERE id=?', (history_id,))
    else:
        data = payload()
        name, code = text_field(data, 'name', required=False), text_field(data, 'code', required=False)
        if not name and not code:
            raise BusinessError('请填写项目名称或项目号')
        try:
            db.execute('UPDATE project_input_history SET name=?,code=?,updated_at=CURRENT_TIMESTAMP WHERE id=?', (name or code,code,history_id))
        except sqlite3.IntegrityError:
            raise BusinessError('历史记录中已存在相同项目名称和项目号')
    db.commit()
    return jsonify(status='success')


@app.route("/api/admin/projects/<int:project_id>/categories", methods=["POST"])
@admin_required
def create_category(project_id):
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    existing_project(project_id)
    require_active_projects((project_id,))
    name = text_field(payload(), "name", 80)
    try:
        cursor = db.execute("INSERT INTO project_categories(project_id,name) VALUES(?,?)", (project_id, name))
        db.commit()
    except sqlite3.IntegrityError:
        raise BusinessError("该项目已存在同名报销项")
    return jsonify(status="success", id=cursor.lastrowid)


@app.route("/api/admin/categories/<int:category_id>", methods=["PATCH", "DELETE"])
@admin_required
def edit_category(category_id):
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    category = db.execute("SELECT project_id FROM project_categories WHERE id=?", (category_id,)).fetchone()
    if category is None:
        raise BusinessError("报销项不存在", 404)
    require_active_projects((category["project_id"],))
    if request.method == "DELETE":
        if db.execute("SELECT 1 FROM invoice_allocations WHERE category_id=?", (category_id,)).fetchone() or db.execute('SELECT 1 FROM manual_expenses WHERE category_id=? AND deleted_at IS NULL',(category_id,)).fetchone():
            if request.args.get('unassign') != '1':
                raise BusinessError("报销项仍有发票，请先移出", 409)
            db.execute("UPDATE invoice_allocations SET project_id=NULL,category_id=NULL,updated_at=? WHERE category_id=?",
                       (datetime.now(timezone.utc).isoformat(), category_id))
        db.execute('UPDATE manual_expenses SET category=COALESCE((SELECT name FROM project_categories WHERE id=manual_expenses.category_id),category),project_id=NULL,category_id=NULL,updated_at=? WHERE category_id=?',(datetime.now(timezone.utc).isoformat(),category_id))
        db.execute("DELETE FROM project_categories WHERE id=?", (category_id,))
    else:
        try:
            db.execute("UPDATE project_categories SET name=? WHERE id=?", (text_field(payload(), "name", 80), category_id))
        except sqlite3.IntegrityError:
            raise BusinessError("此项目已存在同名报销项")
    db.commit()
    return jsonify(status="success")


@app.route("/api/admin/allocate", methods=["POST"])
@admin_required
def allocate():
    data = payload()
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    rows = invoice_selection(data.get("invoice_ids"))
    pid, cid = data.get("project_id"), data.get("category_id")
    if pid is not None:
        existing_project(pid)
        if cid is not None and (type(cid) is not int or not db.execute("SELECT 1 FROM project_categories WHERE id=? AND project_id=?", (cid, pid)).fetchone()):
            raise BusinessError("报销项不属于所选项目")
    require_active_projects([pid, *(row["project_id"] for row in rows)])
    versions = data.get("versions", {})
    if not isinstance(versions, dict):
        raise BusinessError("版本信息无效")
    for row in rows:
        if pid is not None and row["amount_pending"]:
            raise BusinessError("所选发票有金额尚未填写，请先核对金额", 409)
        if pid is not None and not row["assignable"]:
            raise BusinessError("所选发票包含尚未正式提交的草稿，请等待成员提交或结项后整理", 409)
        if row["id"] in versions and versions[row["id"]] != row["version"]:
            raise BusinessError("发票已被其他操作更新，请刷新后重试", 409)
        destination = cid
        if pid is not None and destination is None:
            db.execute("INSERT OR IGNORE INTO project_categories(project_id,name) VALUES(?,?)", (pid, row["category"]))
            destination = db.execute("SELECT id FROM project_categories WHERE project_id=? AND name=?", (pid, row["category"])).fetchone()[0]
        save_allocation(row, pid, destination if pid is not None else None)
    db.commit()
    return jsonify(status="success", count=len(rows))


@app.route("/api/admin/invoices/amount", methods=["POST"])
@app.route('/api/admin/invoices/edit',methods=['POST'])
@admin_required
def invoice_amount():
    data = payload()
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    row = invoice_selection([data.get("invoice_id")])[0]
    require_active_projects((row["project_id"],))
    if "version" in data and data["version"] != row["version"]:
        raise BusinessError("金额已被其他操作更新，请刷新后重试", 409)
    amount=cents(data.get('amount'))
    cid=row['category_id']
    category=text_field(data,'category',80) if 'category' in data else None
    if category and row['project_id'] is not None:
        db.execute('INSERT OR IGNORE INTO project_categories(project_id,name) VALUES(?,?)',(row['project_id'],category))
        cid=db.execute('SELECT id FROM project_categories WHERE project_id=? AND name=?',(row['project_id'],category)).fetchone()[0]
    save_allocation(row, row["project_id"], cid, amount, keep_amount=False)
    if category:
        db.execute('''INSERT INTO invoice_category_overrides(window_id,owner,item_id,filename,category,updated_at) VALUES(?,?,?,?,?,?)
            ON CONFLICT(window_id,owner,item_id,filename) DO UPDATE SET category=excluded.category,updated_at=excluded.updated_at''',
            (row['task_id'],row['owner'],row['item_id'],row['filename'],category,datetime.now(timezone.utc).isoformat()))
    db.commit()
    return jsonify(status="success")


@app.route("/api/admin/guide", methods=["GET", "POST"])
@admin_required
def guide_settings():
    from guide import guide_document, guide_mode
    db = get_db()
    if request.method == "POST":
        data = payload()
        content = text_field(data, "content", 40000)
        mode = guide_mode(data.get('format', guide_document()[1]))
        with db:
            for key, value in [('guide', content), ('guide_format', mode)]:
                db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    content, mode = guide_document()
    return jsonify(status="success", content=content, format=mode)


@app.route('/api/admin/guide/preview', methods=['POST'])
@admin_required
def guide_preview():
    from guide import guide_mode, render_guide
    data = payload()
    content = text_field(data, 'content', 40000, required=False)
    return jsonify(html=str(render_guide(content, guide_mode(data.get('format', 'text')))))


@app.route('/api/admin/guide/images', methods=['POST'])
@admin_required
def guide_upload_image():
    from PIL import Image, ImageOps
    file = request.files.get('image')
    if not file or extension(file.filename) not in INVOICE_EXTENSIONS - {'pdf'}:
        raise BusinessError('请选择 PNG、JPG、WebP 等常见图片')
    content = file.read(8 * 1024 * 1024 + 1)
    if len(content) > 8 * 1024 * 1024:
        raise BusinessError('说明图片不得超过 8 MB')
    import io
    try:
        with Image.open(io.BytesIO(content)) as original:
            if original.width * original.height > 20_000_000:
                raise ValueError('image too large')
            image = ImageOps.exif_transpose(original).convert('RGBA')
            image.load()
    except Exception:
        raise BusinessError('图片无法读取或像素过大，请换一张图片')
    folder = Path(app.config['UPLOAD_FOLDER']) / '.guide_media'
    folder.mkdir(parents=True, exist_ok=True)
    name = uuid.uuid4().hex + '.png'
    image.save(folder / name, format='PNG')
    return jsonify(status='success', url='/guide-media/' + name)


@app.route('/guide-media/<filename>')
def guide_media(filename):
    if not re.fullmatch(r'[a-f0-9]{32}\.png', filename):
        abort(404)
    path = Path(app.config['UPLOAD_FOLDER']) / '.guide_media' / filename
    if not path.is_file():
        abort(404)
    response = send_file(path, mimetype='image/png', max_age=86400)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@app.route("/api/admin/window/create", methods=["POST"])
@admin_required
def create_window():
    data = payload()
    title, task_type, ids = text_field(data, "title"), data.get("task_type", "日常业务报销"), data.get("user_ids", [])
    if task_type not in {"日常业务报销", "国内差旅费"} or not isinstance(ids, list) or not ids or any(type(uid) is not int for uid in ids):
        raise BusinessError("请选择任务类型和至少一位成员")
    db = get_db()
    try:
        with db:
            cursor = db.execute("INSERT INTO windows(title,task_type) VALUES(?,?)", (title, task_type))
            tid = cursor.lastrowid
            for uid in set(ids):
                if not db.execute("SELECT 1 FROM users WHERE id=? AND archived_username IS NULL", (uid,)).fetchone():
                    raise BusinessError("部分成员已删除，请刷新后重试")
                db.execute("INSERT INTO assignments(window_id,user_id,status) VALUES(?,?,0)", (tid, uid))
    except sqlite3.IntegrityError:
        raise BusinessError("部分成员不存在，请刷新后重试")
    return jsonify(status="success", id=tid)


@app.route("/api/admin/window/<int:task_id>/<action>", methods=["POST"])
@admin_required
def task_action(task_id, action):
    if action not in {'close', 'reopen', 'complete', 'uncomplete'}:
        abort(404)
    db = get_db()
    db.execute('BEGIN IMMEDIATE')
    task = db.execute('SELECT * FROM windows WHERE id=?', (task_id,)).fetchone()
    if task is None:
        raise BusinessError('收集任务不存在', 404)
    if action == 'reopen' and task['is_completed']:
        raise BusinessError('请先撤销结项，再重新开放收集', 409)
    if action == 'complete':
        db.execute('UPDATE windows SET is_completed=1,is_collection_closed=1 WHERE id=?', (task_id,))
    elif action == 'uncomplete':
        db.execute('UPDATE windows SET is_completed=0,is_collection_closed=1 WHERE id=?', (task_id,))
    else:
        db.execute('UPDATE windows SET is_collection_closed=? WHERE id=?', (int(action == 'close'), task_id))
    db.commit()
    return jsonify(status="success")


@app.route('/api/admin/window/<int:task_id>', methods=['PATCH'])
@admin_required
def rename_window(task_id):
    title = text_field(payload(), 'title')
    db = get_db()
    cursor = db.execute('UPDATE windows SET title=? WHERE id=?', (title,task_id))
    if not cursor.rowcount:
        raise BusinessError('收集任务不存在', 404)
    db.commit()
    return jsonify(status='success')


@app.route("/api/admin/window/<int:task_id>/revert/<int:user_id>", methods=["POST"])
@admin_required
def revert_user_status(task_id, user_id):
    db = get_db()
    with db:
        changed = db.execute("""UPDATE assignments SET status=1 WHERE window_id=? AND user_id=?
            AND user_id IN (SELECT id FROM users WHERE archived_username IS NULL)""", (task_id, user_id))
        if not changed.rowcount:
            raise BusinessError("成员已删除或未分配此任务", 404)
    return jsonify(status="success")


@app.route("/api/admin/window/delete/<int:task_id>", methods=["POST"])
@admin_required
def delete_window(task_id):
    db = get_db()
    db.execute('BEGIN IMMEDIATE')
    task = db.execute('SELECT * FROM windows WHERE id=?', (task_id,)).fetchone()
    if task is None:
        raise BusinessError('收集任务不存在或已删除', 404)
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or data.get('confirmed') is not True:
        raise BusinessError('请确认删除任务及其提交记录后重试', 409)
    data_root = Path(app.config['DATA_DIR']).resolve()
    upload_root = Path(app.config['UPLOAD_FOLDER']).resolve()
    folder = upload_root / f'task_{task_id}'
    archive = (data_root / '.trash' / ('task-delete-' + uuid.uuid4().hex)).resolve()
    if folder.is_symlink() or not folder.resolve().is_relative_to(upload_root) or not archive.is_relative_to(data_root):
        raise BusinessError('任务归档路径无效，未执行删除')
    snapshot = {
        'format':'task-delete-v1', 'archived_at':datetime.now(timezone.utc).isoformat(), 'task':dict(task),
        'assignments':[dict(r) for r in db.execute('SELECT * FROM assignments WHERE window_id=? ORDER BY id', (task_id,))],
        'allocations':[dict(r) for r in db.execute('SELECT * FROM invoice_allocations WHERE window_id=? ORDER BY owner,item_id,filename', (task_id,))],
        'manual_expenses':[dict(r) for r in db.execute('SELECT * FROM manual_expenses WHERE task_id=?',(task_id,))],
        'category_overrides':[dict(r) for r in db.execute('SELECT * FROM invoice_category_overrides WHERE window_id=?',(task_id,))],
        'users':[dict(r) for r in db.execute('SELECT id,COALESCE(archived_username,username) AS username,real_name,storage_owner FROM users WHERE id IN (SELECT user_id FROM assignments WHERE window_id=? UNION SELECT user_id FROM manual_expenses WHERE task_id=?)', (task_id,task_id))],
        'projects':[dict(r) for r in db.execute('SELECT * FROM projects WHERE id IN (SELECT project_id FROM invoice_allocations WHERE window_id=? UNION SELECT project_id FROM manual_expenses WHERE task_id=?)', (task_id,task_id))],
        'categories':[dict(r) for r in db.execute('SELECT * FROM project_categories WHERE id IN (SELECT category_id FROM invoice_allocations WHERE window_id=? UNION SELECT category_id FROM manual_expenses WHERE task_id=?)', (task_id,task_id))],
    }
    destination = archive / folder.name
    moved = False
    try:
        archive.mkdir(parents=True, exist_ok=False)
        atomic_json(archive / 'task-records.json', snapshot)
        if folder.exists():
            shutil.move(str(folder), str(destination))
            moved = True
        db.execute('DELETE FROM invoice_allocations WHERE window_id=?', (task_id,))
        db.execute('DELETE FROM invoice_category_overrides WHERE window_id=?',(task_id,))
        db.execute('DELETE FROM manual_expenses WHERE task_id=?',(task_id,))
        db.execute('DELETE FROM assignments WHERE window_id=?', (task_id,))
        db.execute('DELETE FROM windows WHERE id=?', (task_id,))
        db.commit()
    except Exception:
        db.rollback()
        if moved:
            shutil.move(str(destination), str(folder))
        raise
    return jsonify(status='success', archive=archive.relative_to(data_root).as_posix())


@app.route("/api/admin/window/<int:task_id>/status")
@admin_required
def get_window_status(task_id):
    rows, _ = collect_invoices(task_id)
    manual=collect_manual_expenses(task_id=task_id)
    users, totals = [], defaultdict(int)
    for a in get_db().execute("SELECT a.*,u.real_name,u.storage_owner FROM assignments a JOIN users u ON u.id=a.user_id WHERE a.window_id=?", (task_id,)):
        owned = [r for r in rows if r["user_id"] == a["user_id"]]
        expenses=[e for e in manual if e['user_id']==a['user_id']]
        invoice_amount=sum(r['amount_cents'] for r in owned)/100;manual_amount=sum(e['amount_cents'] for e in expenses)/100
        users.append({"user_id": a["user_id"], "real_name": a["real_name"], "owner": member_owner(a), "status": a["status"], "item_count": len({r["item_id"] for r in owned}), "amount": invoice_amount+manual_amount,'invoice_amount':invoice_amount,'manual_amount':manual_amount})
    for uid in {e['user_id'] for e in manual}-{u['user_id'] for u in users}:
        expenses=[e for e in manual if e['user_id']==uid]
        amount=sum(e['amount_cents'] for e in expenses)/100
        users.append({'user_id':uid,'real_name':expenses[0]['real_name'],'status':None,'item_count':0,'amount':amount,'invoice_amount':0,'manual_amount':amount})
    for row in rows:
        totals[row["category"]] += row["amount_cents"]
    for row in manual:totals[row['category']]+=row['amount_cents']
    window = get_db().execute("SELECT task_type FROM windows WHERE id=?", (task_id,)).fetchone()
    return jsonify(users=users, grand_total=sum(r["amount_cents"] for r in rows+manual) / 100, category_totals={k: v / 100 for k, v in totals.items()}, task_type=window[0] if window else "日常业务报销")


def task_owner(task_id, name):
    # These legacy admin operations address a member by display name within a task.
    member = get_db().execute("""SELECT u.storage_owner,u.real_name FROM assignments a
        JOIN users u ON u.id=a.user_id WHERE a.window_id=? AND u.real_name=?
        ORDER BY u.archived_username IS NULL DESC,u.id DESC""", (task_id, name)).fetchone()
    return member_owner(member) if member else secure_zh_filename(name)


@app.route("/api/admin/window/<int:task_id>/user_details/<real_name>")
@admin_required
def get_user_details(task_id, real_name):
    rows, _ = collect_invoices(task_id, task_owner(task_id, real_name))
    items = []
    for item_id in dict.fromkeys(r["item_id"] for r in rows):
        owned = [r for r in rows if r["item_id"] == item_id]
        meta = read_meta(owned[0]["_item_path"])
        meta["auto_amount"] = sum(r["amount_cents"] for r in owned) / 100
        items.append(meta)
    return jsonify(items=items, user_total=sum(r["amount_cents"] for r in rows) / 100)


@app.route("/admin/view_file/<int:task_id>/<real_name>/<item_id>/<filename>")
@admin_required
def view_file(task_id, real_name, item_id, filename):
    # File URLs carry the immutable directory name, including links issued before an account edit.
    return serve_record_file(task_id, real_name, item_id, filename)


@app.route("/api/admin/item/<int:task_id>/<real_name>/<item_id>/amount", methods=["POST"])
@admin_required
def update_item_amount_admin(task_id, real_name, item_id):
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    rows, _ = collect_invoices(task_id, task_owner(task_id, real_name))
    rows = [r for r in rows if r["item_id"] == item_id]
    if not rows:
        raise BusinessError("记录不存在", 404)
    require_active_projects(row["project_id"] for row in rows)
    for row, amount in zip(rows, distribute(cents(payload().get("amount")), [r["amount_cents"] for r in rows])):
        save_allocation(row, row["project_id"], row["category_id"], amount, keep_amount=False)
    db.commit()
    return jsonify(status="success")


def validate_member_identity(db, username, real_name, user_id=None):
    if real_name != secure_zh_filename(real_name):
        raise BusinessError("姓名不能包含路径符号或无效字符")
    if db.execute("SELECT 1 FROM users WHERE username=? AND id IS NOT ?", (username, user_id)).fetchone():
        raise BusinessError("账号已存在")
    if db.execute("SELECT 1 FROM users WHERE real_name=? AND id IS NOT ? AND archived_username IS NULL", (real_name, user_id)).fetchone():
        raise BusinessError("该姓名已使用，同名成员请添加区分后缀")


def create_member(username, real_name, password):
    db = get_db()
    password_hash = generate_password_hash(password)
    with db:
        db.execute('BEGIN IMMEDIATE')
        validate_member_identity(db, username, real_name)
        # New accounts always get a fresh directory, even when a former member's name is reused.
        db.execute("INSERT INTO users(username,password,real_name,storage_owner) VALUES(?,?,?,?)",
                   (username, password_hash, real_name, 'member-' + uuid.uuid4().hex))


@app.route("/api/admin/users/add", methods=["POST"])
@admin_required
def admin_add_user():
    data = payload()
    create_member(text_field(data, 'username'), text_field(data, 'real_name'), text_field(data, 'password'))
    return jsonify(status="success")


@app.route("/api/admin/users/<int:user_id>", methods=["PATCH"])
@admin_required
def admin_edit_user(user_id):
    data = payload()
    username, real_name = text_field(data, 'username'), text_field(data, 'real_name')
    db = get_db()
    with db:
        db.execute('BEGIN IMMEDIATE')
        user = db.execute("SELECT real_name,storage_owner FROM users WHERE id=? AND archived_username IS NULL", (user_id,)).fetchone()
        if user is None:
            raise BusinessError("成员不存在或已删除", 404)
        validate_member_identity(db, username, real_name, user_id)
        db.execute("UPDATE users SET username=?,real_name=?,storage_owner=? WHERE id=?",
                   (username, real_name, member_owner(user), user_id))
    return jsonify(status="success")


@app.route("/api/admin/users/delete/<int:user_id>", methods=["POST"])
@admin_required
def admin_delete_user(user_id):
    db = get_db()
    with db:
        # Release the unique login name while keeping the member ID referenced by the ledger.
        changed = db.execute("""UPDATE users SET archived_username=username,username=?,password=''
            WHERE id=? AND archived_username IS NULL""", ('deleted-' + uuid.uuid4().hex, user_id))
        if not changed.rowcount:
            raise BusinessError("成员不存在或已删除", 404)
    return jsonify(status="success")


from exports import register_exports
register_exports(app, admin_required)
from expenses import register_expenses
register_expenses(app,admin_required,login_required,payload,text_field)

if __name__ == "__main__":
    app.run(host=os.environ.get("LABINVOICE_HOST", "127.0.0.1"), port=int(os.environ.get("LABINVOICE_PORT", "5000")), debug=False)
