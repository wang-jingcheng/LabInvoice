import os
import io
import zipfile
import re
import json
import time
import sqlite3
import shutil
from collections import defaultdict
from functools import wraps
from datetime import timedelta
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_file, g, abort
from werkzeug.security import generate_password_hash, check_password_hash
from pypdf import PdfWriter, PdfReader

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("WARNING: 请先执行 pip install Pillow 以支持图片处理！")

try:
    from pypinyin import lazy_pinyin
except ImportError:
    print("WARNING: 请先执行 pip install pypinyin 以支持按拼音排序！")
    def lazy_pinyin(word):
        return word 

app = Flask(__name__)
app.secret_key = 'super_secret_lab_key' 
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['MAX_CONTENT_LENGTH'] = 128 * 1024 * 1024 

BASE_DIR = os.path.dirname(os.path.abspath(__name__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
DATABASE = os.path.join(BASE_DIR, 'labinvoice.db')
ADMIN_PASSWORD = 'jcwang'

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ==================== 安全防护配置 ====================
ALLOWED_INVOICE_EXTS = {'pdf'}
ALLOWED_ATTACHMENT_EXTS = {'pdf', 'png', 'jpg', 'jpeg', 'doc', 'docx', 'xls', 'xlsx', 'zip', 'rar', '7z'}

def allowed_file(filename, allowed_set):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed_set

# ==================== 数据库与工具函数 ====================
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        db.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, real_name TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS windows (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, is_completed INTEGER DEFAULT 0)')
        db.execute('CREATE TABLE IF NOT EXISTS assignments (id INTEGER PRIMARY KEY AUTOINCREMENT, window_id INTEGER, user_id INTEGER, status INTEGER DEFAULT 0, FOREIGN KEY(window_id) REFERENCES windows(id), FOREIGN KEY(user_id) REFERENCES users(id))')
        
        try:
            db.execute('ALTER TABLE windows ADD COLUMN is_completed INTEGER DEFAULT 0')
        except sqlite3.OperationalError:
            pass 
            
        try:
            db.execute('ALTER TABLE windows ADD COLUMN task_type TEXT DEFAULT "日常业务报销"')
        except sqlite3.OperationalError:
            pass 

        db.commit()

init_db()

def secure_zh_filename(filename):
    filename = os.path.basename(filename)
    return re.sub(r'[\<\>\:\"\/\\\|\?\*]', '_', filename)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session: return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

def extract_amount_from_pdf(filepath):
    try:
        reader = PdfReader(filepath)
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""
            
        text = re.sub(r'[\s　,，"\'\r\n]', '', text)
        
        match = re.search(r'小写[^\d]{0,10}([0-9]+\.[0-9]{2})', text)
        if match: 
            return float(match.group(1))
        
        match = re.search(r'价税合计[^\d]{0,20}([0-9]+\.[0-9]{2})', text)
        if match: 
            return float(match.group(1))
            
        match = re.search(r'(?:合计金额|总金额|金额)[^\d]{0,10}([0-9]+\.[0-9]{2})', text)
        if match: 
            return float(match.group(1))

        amounts = re.findall(r'[¥￥]([0-9]+\.[0-9]{2})', text)
        if amounts:
            return float(max([float(a) for a in amounts]))
    except Exception as e:
        print(f"提取金额失败 {filepath}: {e}")
    return 0.0

def convert_to_pdf(file_path):
    try:
        img = Image.open(file_path)
        if img.mode in ("RGBA", "P"): img = img.convert("RGB")
        pdf_bytes = io.BytesIO()
        img.save(pdf_bytes, format='PDF')
        pdf_bytes.seek(0)
        return pdf_bytes
    except Exception as e:
        print(f"转换图片失败 {file_path}: {e}")
        return None

def get_pinyin_sort_key(real_name):
    try:
        return ''.join([p[0] for p in lazy_pinyin(real_name)]).lower()
    except:
        return real_name.lower()

# ==================== 登录/注册模块 ====================
@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session: return redirect(url_for('index'))
    if request.method == 'POST':
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE username = ?', (request.form.get('username', '').strip(),)).fetchone()
        if user and check_password_hash(user['password'], request.form.get('password', '').strip()):
            session.permanent = bool(request.form.get('remember'))
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['real_name'] = user['real_name']
            return redirect(url_for('index'))
        return render_template('login.html', error='用户名或密码错误！')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        db = get_db()
        username = request.form.get('username').strip()
        password = request.form.get('password').strip()
        real_name = request.form.get('real_name').strip()
        
        existing_realname = db.execute('SELECT id FROM users WHERE real_name = ?', (real_name,)).fetchone()
        if existing_realname:
            return render_template('register.html', error='该真实姓名已被使用，如有同名请添加后缀（如：张三2）！')
        
        try:
            db.execute('INSERT INTO users (username, password, real_name) VALUES (?, ?, ?)',
                       (username, generate_password_hash(password), real_name))
            db.commit()
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            return render_template('register.html', error='账号已被注册！')
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ==================== 普通用户 端 API ====================
@app.route('/')
@login_required
def index():
    return render_template('index.html', real_name=session.get('real_name'))

@app.route('/api/user/tasks')
@login_required
def get_user_tasks():
    user_id = session.get('user_id')
    db = get_db()
    tasks = db.execute('SELECT w.id, w.title, w.task_type, w.is_completed, a.status FROM assignments a JOIN windows w ON a.window_id = w.id WHERE a.user_id = ? ORDER BY w.id DESC', (user_id,)).fetchall()
    
    result = []
    real_name = session.get('real_name')
    for t in tasks:
        task_data = {
            'id': t['id'], 
            'title': t['title'], 
            'task_type': t['task_type'] or '日常业务报销',
            'status': t['status'], 
            'is_completed': t['is_completed'], 
            'items': []
        }
        target_dir = os.path.join(UPLOAD_FOLDER, f"task_{t['id']}", secure_zh_filename(real_name))
        
        auto_total = 0.0

        if os.path.exists(target_dir):
            for item_folder in os.listdir(target_dir):
                item_path = os.path.join(target_dir, item_folder)
                if os.path.isdir(item_path):
                    json_file = os.path.join(item_path, 'meta.json')
                    if os.path.exists(json_file):
                        with open(json_file, 'r', encoding='utf-8') as f:
                            meta = json.load(f)
                            task_data['items'].append(meta)
                            auto_total += meta.get('auto_amount', 0.0)
        
        task_data['final_amount'] = round(auto_total, 2)
        result.append(task_data)
    return jsonify(result)

@app.route('/api/user/view_file/<int:task_id>/<item_id>/<filename>')
@login_required
def user_view_file(task_id, item_id, filename):
    real_name = session.get('real_name')
    user_id = session.get('user_id')
    
    db = get_db()
    assignment = db.execute('SELECT 1 FROM assignments WHERE window_id = ? AND user_id = ?', (task_id, user_id)).fetchone()
    if not assignment:
        abort(403) 
        
    file_path = os.path.join(
        UPLOAD_FOLDER, 
        f"task_{task_id}", 
        secure_zh_filename(real_name), 
        f"item_{secure_zh_filename(item_id)}", 
        secure_zh_filename(filename)
    )
    if os.path.exists(file_path):
        return send_file(file_path)
    abort(404)

@app.route('/api/user/upload_item/<int:task_id>', methods=['POST'])
@login_required
def user_upload_item(task_id):
    user_id = session.get('user_id')
    real_name = session.get('real_name')
    db = get_db()
    
    window = db.execute('SELECT is_completed FROM windows WHERE id = ?', (task_id,)).fetchone()
    if window and window['is_completed'] == 1:
        return jsonify({'status': 'error', 'message': '该任务已结项，不可再提交新发票'}), 400

    assignment = db.execute('SELECT status FROM assignments WHERE window_id = ? AND user_id = ?', (task_id, user_id)).fetchone()
    if not assignment or assignment['status'] == 2:
        return jsonify({'status': 'error', 'message': '任务状态已锁定'}), 400

    item_type = request.form.get('type')
    remark = request.form.get('remark', '').strip()
    invoice_files = request.files.getlist('invoice_files')
    attachments = request.files.getlist('attachments')
    invoice_files = [f for f in invoice_files if f and f.filename]
    attachments = [f for f in attachments if f and f.filename]

    if not item_type or not invoice_files:
        return jsonify({'status': 'error', 'message': '请至少选择一张主发票文件'}), 400

    for inv in invoice_files:
        if not allowed_file(inv.filename, ALLOWED_INVOICE_EXTS):
            return jsonify({'status': 'error', 'message': f'发票存在安全风险，仅支持 PDF 格式：{inv.filename}'}), 400

    for att in attachments:
        if not allowed_file(att.filename, ALLOWED_ATTACHMENT_EXTS):
            return jsonify({'status': 'error', 'message': f'附件包含不支持的高风险文件类型：{att.filename}'}), 400
    
    item_id = str(int(time.time() * 1000))
    item_dir = os.path.join(UPLOAD_FOLDER, f"task_{task_id}", secure_zh_filename(real_name), f"item_{item_id}")
    os.makedirs(item_dir, exist_ok=True)

    saved_invoices = []
    total_auto_amount = 0.0 
    for inv in invoice_files:
        safe_inv_name = f"主发票_{secure_zh_filename(inv.filename)}"
        save_path = os.path.join(item_dir, safe_inv_name)
        inv.save(save_path)
        saved_invoices.append(safe_inv_name)
        if save_path.lower().endswith('.pdf'):
            total_auto_amount += extract_amount_from_pdf(save_path)

    saved_attachments = []
    for att in attachments:
        safe_att_name = f"附件_{secure_zh_filename(att.filename)}"
        att.save(os.path.join(item_dir, safe_att_name))
        saved_attachments.append(safe_att_name)

    meta_data = {
        'id': item_id,
        'type': item_type,
        'remark': remark,
        'invoice_files': saved_invoices,
        'attachments': saved_attachments,
        'auto_amount': round(total_auto_amount, 2) 
    }
    with open(os.path.join(item_dir, 'meta.json'), 'w', encoding='utf-8') as f:
        json.dump(meta_data, f, ensure_ascii=False)

    if assignment['status'] == 0:
        db.execute('UPDATE assignments SET status = 1 WHERE window_id = ? AND user_id = ?', (task_id, user_id))
        db.commit()

    return jsonify({'status': 'success'})

@app.route('/api/user/item/<int:task_id>/<item_id>/amount', methods=['POST'])
@login_required
def update_item_amount_user(task_id, item_id):
    user_id = session.get('user_id')
    real_name = session.get('real_name')
    db = get_db()
    
    window = db.execute('SELECT is_completed FROM windows WHERE id = ?', (task_id,)).fetchone()
    if window and window['is_completed'] == 1:
        return jsonify({'status': 'error', 'message': '该任务已结项，不可修改金额'}), 400

    assignment = db.execute('SELECT status FROM assignments WHERE window_id = ? AND user_id = ?', (task_id, user_id)).fetchone()
    if not assignment or assignment['status'] == 2:
        return jsonify({'status': 'error', 'message': '无法修改，已提交锁定'}), 400

    item_dir = os.path.join(UPLOAD_FOLDER, f"task_{task_id}", secure_zh_filename(real_name), f"item_{secure_zh_filename(item_id)}")
    json_file = os.path.join(item_dir, 'meta.json')
    if os.path.exists(json_file):
        with open(json_file, 'r', encoding='utf-8') as f:
            meta = json.load(f)
        meta['auto_amount'] = float(request.json.get('amount', 0))
        meta.pop('is_admin_calibrated', None) 
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False)
        return jsonify({'status': 'success'})
    return jsonify({'status': 'error', 'message': '条目不存在'}), 404

@app.route('/api/user/item/<int:task_id>/<item_id>', methods=['DELETE'])
@login_required
def delete_item(task_id, item_id):
    user_id = session.get('user_id')
    db = get_db()
    
    window = db.execute('SELECT is_completed FROM windows WHERE id = ?', (task_id,)).fetchone()
    if window and window['is_completed'] == 1:
        return jsonify({'status': 'error', 'message': '该任务已结项，不可删除文件'}), 400

    assignment = db.execute('SELECT status FROM assignments WHERE window_id = ? AND user_id = ?', (task_id, user_id)).fetchone()
    if not assignment or assignment['status'] == 2:
        return jsonify({'status': 'error', 'message': '无法修改'}), 400
        
    item_dir = os.path.join(UPLOAD_FOLDER, f"task_{task_id}", secure_zh_filename(session.get('real_name')), f"item_{secure_zh_filename(item_id)}")
    if os.path.exists(item_dir):
        shutil.rmtree(item_dir)
        return jsonify({'status': 'success'})
    return jsonify({'status': 'error', 'message': '记录不存在'}), 404

@app.route('/api/user/submit/<int:task_id>', methods=['POST'])
@login_required
def submit_task(task_id):
    db = get_db()
    window = db.execute('SELECT is_completed FROM windows WHERE id = ?', (task_id,)).fetchone()
    if window and window['is_completed'] == 1:
        return jsonify({'status': 'error', 'message': '该任务已结项，操作无效'}), 400

    db.execute('UPDATE assignments SET status = 2 WHERE window_id = ? AND user_id = ?', (task_id, session.get('user_id')))
    db.commit()
    return jsonify({'status': 'success'})

# ==================== 管理员 端 API ====================
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            return redirect(url_for('admin_dashboard'))
        return render_template('admin_login.html', error='密码错误！')
    return render_template('admin_login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('admin_login'))

@app.route('/admin')
@admin_required
def admin_dashboard():
    db = get_db()
    users = db.execute('SELECT id, username, real_name FROM users ORDER BY id DESC').fetchall()
    windows = db.execute('SELECT * FROM windows ORDER BY id DESC').fetchall()
    return render_template('admin_dashboard.html', users=users, windows=windows)

@app.route('/api/admin/window/create', methods=['POST'])
@admin_required
def create_window():
    data = request.json
    task_type = data.get('task_type', '日常业务报销')
    db = get_db()
    db.execute('INSERT INTO windows (title, task_type) VALUES (?, ?)', (data.get('title'), task_type))
    window_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
    for uid in data.get('user_ids', []):
        db.execute('INSERT INTO assignments (window_id, user_id, status) VALUES (?, ?, 0)', (window_id, uid))
    db.commit()
    return jsonify({'status': 'success'})

@app.route('/api/admin/window/<int:task_id>/complete', methods=['POST'])
@admin_required
def complete_window(task_id):
    db = get_db()
    db.execute('UPDATE windows SET is_completed = 1 WHERE id = ?', (task_id,))
    db.commit()
    return jsonify({'status': 'success'})

@app.route('/api/admin/window/<int:task_id>/uncomplete', methods=['POST'])
@admin_required
def uncomplete_window(task_id):
    db = get_db()
    db.execute('UPDATE windows SET is_completed = 0 WHERE id = ?', (task_id,))
    db.commit()
    return jsonify({'status': 'success'})

@app.route('/api/admin/window/<int:task_id>/status')
@admin_required
def get_window_status(task_id):
    db = get_db()
    
    window = db.execute('SELECT task_type FROM windows WHERE id = ?', (task_id,)).fetchone()
    task_type = window['task_type'] if window and window['task_type'] else '日常业务报销'
    
    assignments = db.execute('SELECT u.id as user_id, u.real_name, a.status FROM assignments a JOIN users u ON a.user_id = u.id WHERE a.window_id = ?', (task_id,)).fetchall()
    
    users_data = []
    grand_total = 0.0
    category_totals = defaultdict(float)
    
    for a in assignments:
        target_dir = os.path.join(UPLOAD_FOLDER, f"task_{task_id}", secure_zh_filename(a['real_name']))
        item_count = 0
        user_total = 0.0

        if os.path.exists(target_dir):
            for item_folder in os.listdir(target_dir):
                item_path = os.path.join(target_dir, item_folder)
                if os.path.isdir(item_path):
                    item_count += 1
                    json_file = os.path.join(item_path, 'meta.json')
                    if os.path.exists(json_file):
                        with open(json_file, 'r', encoding='utf-8') as f:
                            meta = json.load(f)
                            amt = meta.get('auto_amount', 0.0)
                            user_total += amt
                            category_totals[meta.get('type', '其他')] += amt
                            
        grand_total += user_total
        users_data.append({
            'user_id': a['user_id'], 
            'real_name': a['real_name'], 
            'status': a['status'], 
            'item_count': item_count,
            'amount': round(user_total, 2)
        })
        
    return jsonify({
        'task_type': task_type,
        'users': users_data,
        'grand_total': round(grand_total, 2),
        'category_totals': {k: round(v, 2) for k, v in category_totals.items()}
    })

@app.route('/api/admin/window/<int:task_id>/user_details/<real_name>')
@admin_required
def get_user_details(task_id, real_name):
    target_dir = os.path.join(UPLOAD_FOLDER, f"task_{task_id}", secure_zh_filename(real_name))
    items = []
    user_total = 0.0
    
    if os.path.exists(target_dir):
        for item_folder in os.listdir(target_dir):
            item_path = os.path.join(target_dir, item_folder)
            if os.path.isdir(item_path):
                json_file = os.path.join(item_path, 'meta.json')
                if os.path.exists(json_file):
                    with open(json_file, 'r', encoding='utf-8') as f:
                        meta = json.load(f)
                        items.append(meta)
                        user_total += meta.get('auto_amount', 0.0)
                        
    return jsonify({
        'items': items,
        'user_total': round(user_total, 2)
    })

@app.route('/admin/view_file/<int:task_id>/<real_name>/<item_id>/<filename>')
@admin_required
def view_file(task_id, real_name, item_id, filename):
    file_path = os.path.join(
        UPLOAD_FOLDER, 
        f"task_{task_id}", 
        secure_zh_filename(real_name), 
        f"item_{secure_zh_filename(item_id)}", 
        secure_zh_filename(filename)
    )
    if os.path.exists(file_path):
        return send_file(file_path)
    abort(404)

@app.route('/api/admin/item/<int:task_id>/<real_name>/<item_id>/amount', methods=['POST'])
@admin_required
def update_item_amount_admin(task_id, real_name, item_id):
    amount = request.json.get('amount', 0)
    target_dir = os.path.join(UPLOAD_FOLDER, f"task_{task_id}", secure_zh_filename(real_name))
    item_dir = os.path.join(target_dir, f"item_{secure_zh_filename(item_id)}")
    json_file = os.path.join(item_dir, 'meta.json')
    
    if os.path.exists(json_file):
        with open(json_file, 'r', encoding='utf-8') as f:
            meta = json.load(f)
        
        meta['auto_amount'] = float(amount)
        meta['is_admin_calibrated'] = True
        
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False)
        return jsonify({'status': 'success'})
        
    return jsonify({'status': 'error', 'message': '条目不存在'}), 404

@app.route('/api/admin/window/<int:task_id>/revert/<int:user_id>', methods=['POST'])
@admin_required
def revert_user_status(task_id, user_id):
    db = get_db()
    db.execute('UPDATE assignments SET status = 1 WHERE window_id = ? AND user_id = ?', (task_id, user_id))
    db.commit()
    return jsonify({'status': 'success'})

@app.route('/api/admin/window/delete/<int:task_id>', methods=['POST'])
@admin_required
def delete_window(task_id):
    db = get_db()
    db.execute('DELETE FROM assignments WHERE window_id = ?', (task_id,))
    db.execute('DELETE FROM windows WHERE id = ?', (task_id,))
    db.commit()
    task_dir = os.path.join(UPLOAD_FOLDER, f"task_{task_id}")
    if os.path.exists(task_dir): shutil.rmtree(task_dir)
    return jsonify({'status': 'success'})

@app.route('/admin/download/<int:task_id>')
@admin_required
def download_task_zip(task_id):
    task_dir = os.path.join(UPLOAD_FOLDER, f"task_{task_id}")
    if not os.path.exists(task_dir): return "暂无数据", 404
    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(task_dir):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, os.path.join(task_dir, '..'))
                zf.write(file_path, arcname)
    memory_file.seek(0)
    return send_file(memory_file, mimetype='application/zip', as_attachment=True, download_name=f'发票原始文件打包.zip')

@app.route('/admin/merge_print/<int:task_id>')
@admin_required
def merge_and_print(task_id):
    task_dir = os.path.join(UPLOAD_FOLDER, f"task_{task_id}")
    if not os.path.exists(task_dir): return "暂无文件可合并", 404

    merger = PdfWriter()
    has_files = False
    
    type_order = ['市内交通费', '专用材料费', '科研招待费', '邮电费', '印刷费', '交通费', '住宿费', '其他费用', '其他']
    data_by_type = defaultdict(list)

    for user_name in os.listdir(task_dir):
        user_dir = os.path.join(task_dir, user_name)
        if os.path.isdir(user_dir):
            pinyin_key = get_pinyin_sort_key(user_name)
            for item_folder in os.listdir(user_dir):
                item_path = os.path.join(user_dir, item_folder)
                if os.path.isdir(item_path):
                    json_file = os.path.join(item_path, 'meta.json')
                    if os.path.exists(json_file):
                        with open(json_file, 'r', encoding='utf-8') as f:
                            meta = json.load(f)
                            cat_type = meta.get('type', '其他')
                            data_by_type[cat_type].append({
                                'user_name': user_name,
                                'pinyin': pinyin_key,
                                'meta': meta,
                                'item_path': item_path
                            })

    def add_to_merger(file_path):
        nonlocal has_files
        try:
            if file_path.lower().endswith('.pdf'):
                reader = PdfReader(file_path)
                for page in reader.pages: merger.add_page(page)
                has_files = True
            elif file_path.lower().endswith(('.png', '.jpg', '.jpeg')):
                pdf_stream = convert_to_pdf(file_path)
                if pdf_stream:
                    reader = PdfReader(pdf_stream)
                    for page in reader.pages: merger.add_page(page)
                    has_files = True
        except Exception as e:
            print(f"合并失败: {file_path}, Error: {e}")

    all_exist_types = list(data_by_type.keys())
    ordered_types = [t for t in type_order if t in all_exist_types]
    ordered_types += [t for t in all_exist_types if t not in type_order]

    is_first_category = True
    for current_type in ordered_types:
        records = data_by_type[current_type]
        if not records: continue
        
        if not is_first_category:
            merger.add_blank_page(width=595.28, height=411.02)
        else:
            is_first_category = False
            
        records.sort(key=lambda x: x['pinyin'])
        
        for record in records:
            meta = record['meta']
            item_path = record['item_path']
            
            if 'invoice_files' in meta:
                for inv in meta['invoice_files']:
                    add_to_merger(os.path.join(item_path, inv))
            elif 'invoice_file' in meta:
                add_to_merger(os.path.join(item_path, meta['invoice_file']))

    if not has_files: return "处理完成，但暂无支持的有效文档格式", 404
    memory_file = io.BytesIO()
    merger.write(memory_file)
    memory_file.seek(0)
    return send_file(memory_file, mimetype='application/pdf', as_attachment=False, download_name='发票智能分类汇总版.pdf')

@app.route('/api/admin/users/add', methods=['POST'])
@admin_required
def admin_add_user():
    data = request.json
    try:
        get_db().execute('INSERT INTO users (username, password, real_name) VALUES (?, ?, ?)', (data.get('username'), generate_password_hash(data.get('password')), data.get('real_name')))
        get_db().commit()
        return jsonify({'status': 'success'})
    except:
        return jsonify({'status': 'error', 'message': '账号已存在！'}), 400

@app.route('/api/admin/users/delete/<int:user_id>', methods=['POST'])
@admin_required
def admin_delete_user(user_id):
    get_db().execute('DELETE FROM users WHERE id = ?', (user_id,))
    get_db().commit()
    return jsonify({'status': 'success'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)