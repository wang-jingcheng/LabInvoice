"""Non-destructive adapters for the original SQLite + upload-directory format."""
import hashlib
import json
import os
import re
import sqlite3
import uuid
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from flask import current_app, g, has_app_context
from pypdf import PdfReader

IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "jfif", "webp", "bmp", "gif", "tif", "tiff"}
INVOICE_EXTENSIONS = {"pdf"} | IMAGE_EXTENSIONS
ATTACHMENT_EXTENSIONS = INVOICE_EXTENSIONS | {"doc", "docx", "xls", "xlsx", "zip", "rar", "7z"}
PRINT_EXTENSIONS = INVOICE_EXTENSIONS
DAILY_CATEGORIES = ["市内交通费", "专用材料费", "科研招待费", "邮电费", "印刷费", "其他"]
TRAVEL_CATEGORIES = ["交通费", "住宿费", "其他费用"]
DEFAULT_GUIDE = """LabInvoice 使用说明

一、登录与收集任务
使用自己的账号登录；首次使用可注册，并填写真实姓名。登录后选择管理员派发的收集任务。若没有任务，请联系管理员。

二、按报销项上传
选择市内交通费、专用材料费等报销项。主发票支持 PDF、JPG/JPEG、PNG、WebP、BMP、GIF、TIFF 等常见图片，行程单、支付记录、材料明细等放在附件区。两类文件均可多次追加，也可逐个移除。各报销项分别暂存文件和备注，切换报销项不会改变文件归属；多个报销项准备好后可一起保存至草稿箱。市内交通费请附对应行程单，具体材料要求以管理员和本单位财务规定为准。

三、保存并核对金额
点击“保存至草稿箱”直接保存。同一任务下的同一报销项会合并显示，多次保存可继续追加；每张发票均可单独删除，也可删除整个草稿条目。系统读取 PDF 中的文本；图片与无法识别的 PDF 金额显示“—”，需在文件右侧逐张手动填写并保存后再正式提交。合计金额由各张发票自动累加。当前不进行图片 OCR，也不会从订单号等数字中猜测金额。自动识别金额仅供参考，请预览原票并核对。尚未保存的文件不会自动上传。

四、正式提交
所有发票、附件、金额和备注核对无误后，再点击“正式提交”。提交后将锁定修改；需要修改时请联系管理员退回。已归入经费项目的记录须先由管理员移回待归类。

五、管理员整理与报销
在“票据整理台”创建经费项目，输入项目名称或项目号。筛选已提交发票，勾选后批量归类，或拖放到项目/报销项。未指定报销项时按原类别自动创建；归类有误可重新移动，也可创建新的报销项。历史多张发票只有批次合计时，逐张金额为合计分摊，请逐张核对。

项目卡片的“管理项目”可修改名称、项目号或撤销项目；报销项旁的编辑入口可改名或删除。删除前会确认，原发票和已核对金额保留，原归类移回待归类。已结项任务的票据仍按结项规则隐藏，重新开放后可重新归类。名称、项目号支持从历史记录成对填入；“管理历史记录”可编辑或删除候选内容，不影响现有项目。

“结束收集”只停止成员上传、修改和提交，票据仍可在整理台核对与归类。“结项”是后续独立操作，结项后该任务的票据移出整理列表；经费项目关联的任务全部结项后，项目移入“已归档项目”，可继续查看汇总、打包下载和打印。撤销结项后票据及相关项目回到整理台，但任务仍处于收集已结束状态；需要成员补充时再点击“重新开放收集”。每个任务均可在管理窗口中重命名，原票据和归类不变。

整理台顶部可筛选某个未结项收集任务，列表与两个金额模块联动。左侧列出本次收集发票总金额，以及各任务、各报销项的小计，零金额项也显示；右侧是本次项目报销总金额。两边只统计正式提交的票据，草稿不计入。各报销项支持单独折叠、全部折叠或全部展开，折叠不影响勾选、归类与金额。

六、汇总、下载与打印
项目详情展示各报销项小计和每位成员的报销总额。打包下载保留原始文件及汇总清单。合并打印默认只选主发票，附件按需勾选；Word、Excel 和压缩包请下载后单独处理。合并 PDF 只表示整理结果，不表示已经通过财务审核。

七、管理员修改与无电子发票报销
“无电子发票报销项”表示尚未上传电子文件的手工登记项，可以持有纸质发票，并不表示没有发票。原有手工登记记录继续保留。
管理员在发票详情中可修改报销项及金额，点击“保存修改”后更新汇总；原上传分类和原票据保留。点击“＋ 无电子发票报销项”，可为任意成员登记劳务费等支出，选择任务、填写金额和备注，并可归入经费项目。登记后直接进入报销汇总，无需成员为此重复提交；成员仅能查看自己的登记记录，修改由管理员操作。已结项任务需先撤销结项再增删改无电子发票项。

发票金额与无电子发票金额分列：发票总额仍只统计正式提交的真实发票，项目报销总额可同时包含发票及无电子发票项。无电子发票项随 CSV 汇总下载，不增加发票张数，不进入合并打印。项目区域可独立滚动；查看某项目后，可用“返回全部票据”或“全部票据 / 待归类 / 当前项目”切换视图，任务筛选保留。
每条无电子发票报销项旁的“归入项目 / 调整归类”可选择经费项目和报销项，也可直接拖入右侧项目卡片或其中的报销项。归类时金额、备注、成员及任务归属保留。

本说明由管理员维护。遇到账号、任务或票据问题，请联系本实验室/工作间管理员。"""


class BusinessError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


def get_db():
    if "db" not in g:
        db = g.db = sqlite3.connect(current_app.config["DATABASE"], timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=30000")
    return g.db


def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def migrate():
    db = get_db()
    # Read and back up before any schema change. Never rewrite uploaded JSON.
    existing = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "users" in existing and "schema_migrations" not in existing:
        directory = Path(current_app.config["DATA_DIR"]) / ".backups"
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / ("database-pre-v2-" + datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8] + ".db")
        with closing(sqlite3.connect(destination)) as target:
            db.backup(target)
    elif "windows" in existing and "is_collection_closed" not in {r[1] for r in db.execute('PRAGMA table_info(windows)')}:
        directory = Path(current_app.config['DATA_DIR']) / '.backups'
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / ('database-pre-v2.6-' + uuid.uuid4().hex + '.db')
        with closing(sqlite3.connect(destination)) as target:
            db.backup(target)
    elif 'users' in existing and ('manual_expenses' not in existing or 'invoice_category_overrides' not in existing):
        directory = Path(current_app.config['DATA_DIR']) / '.backups'
        directory.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(directory / ('database-pre-v2.7-' + uuid.uuid4().hex + '.db'))) as target:
            db.backup(target)
    elif 'users' in existing and 'storage_owner' not in {r[1] for r in db.execute('PRAGMA table_info(users)')}:
        directory = Path(current_app.config['DATA_DIR']) / '.backups'
        directory.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(directory / ('database-pre-v3.1.1-' + uuid.uuid4().hex + '.db'))) as target:
            db.backup(target)
    db.execute("BEGIN IMMEDIATE")
    try:
        seed_project_history = not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='project_input_history'").fetchone()
        statements = [
            "CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,username TEXT UNIQUE NOT NULL,password TEXT NOT NULL,real_name TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS windows(id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT NOT NULL,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,is_completed INTEGER DEFAULT 0,task_type TEXT DEFAULT '日常业务报销')",
            "CREATE TABLE IF NOT EXISTS assignments(id INTEGER PRIMARY KEY AUTOINCREMENT,window_id INTEGER,user_id INTEGER,status INTEGER DEFAULT 0,FOREIGN KEY(window_id) REFERENCES windows(id),FOREIGN KEY(user_id) REFERENCES users(id))",
            "CREATE TABLE IF NOT EXISTS projects(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,code TEXT NOT NULL DEFAULT '',created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
            "CREATE TABLE IF NOT EXISTS project_input_history(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,code TEXT NOT NULL DEFAULT '',updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(name,code))",
            "CREATE TABLE IF NOT EXISTS project_categories(id INTEGER PRIMARY KEY AUTOINCREMENT,project_id INTEGER NOT NULL REFERENCES projects(id),name TEXT NOT NULL,sort_order INTEGER DEFAULT 0,UNIQUE(project_id,name))",
            "CREATE TABLE IF NOT EXISTS invoice_allocations(window_id INTEGER NOT NULL,owner TEXT NOT NULL,item_id TEXT NOT NULL,filename TEXT NOT NULL,project_id INTEGER REFERENCES projects(id),category_id INTEGER REFERENCES project_categories(id),amount_cents INTEGER,updated_at TEXT NOT NULL,PRIMARY KEY(window_id,owner,item_id,filename))",
            "CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY,applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
            "CREATE INDEX IF NOT EXISTS allocations_project ON invoice_allocations(project_id,category_id)",
            "CREATE TABLE IF NOT EXISTS invoice_category_overrides(window_id INTEGER NOT NULL,owner TEXT NOT NULL,item_id TEXT NOT NULL,filename TEXT NOT NULL,category TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(window_id,owner,item_id,filename))",
            "CREATE TABLE IF NOT EXISTS manual_expenses(id TEXT PRIMARY KEY,task_id INTEGER NOT NULL REFERENCES windows(id),user_id INTEGER NOT NULL REFERENCES users(id),category TEXT NOT NULL,amount_cents INTEGER NOT NULL CHECK(amount_cents>=0),remark TEXT NOT NULL DEFAULT '',project_id INTEGER REFERENCES projects(id),category_id INTEGER REFERENCES project_categories(id),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,deleted_at TEXT)",
            "CREATE INDEX IF NOT EXISTS manual_expenses_task ON manual_expenses(task_id,user_id)",
            "CREATE INDEX IF NOT EXISTS manual_expenses_project ON manual_expenses(project_id,category_id)",
        ]
        for sql in statements:
            db.execute(sql)
        user_columns = {r[1] for r in db.execute('PRAGMA table_info(users)')}
        if 'storage_owner' not in user_columns:
            db.execute('ALTER TABLE users ADD COLUMN storage_owner TEXT')
            for user in db.execute('SELECT id,real_name FROM users').fetchall():
                db.execute('UPDATE users SET storage_owner=? WHERE id=?', (secure_zh_filename(user['real_name']), user['id']))
        if 'archived_username' not in user_columns:
            db.execute('ALTER TABLE users ADD COLUMN archived_username TEXT')
        if seed_project_history:
            db.execute("INSERT OR IGNORE INTO project_input_history(name,code) SELECT name,code FROM projects ORDER BY id")
        columns = {r[1] for r in db.execute("PRAGMA table_info(windows)")}
        if "is_completed" not in columns:
            db.execute("ALTER TABLE windows ADD COLUMN is_completed INTEGER DEFAULT 0")
        if 'is_collection_closed' not in columns:
            db.execute('ALTER TABLE windows ADD COLUMN is_collection_closed INTEGER NOT NULL DEFAULT 0')
            db.execute('UPDATE windows SET is_collection_closed=1 WHERE is_completed=1')
        if "task_type" not in columns:
            db.execute("ALTER TABLE windows ADD COLUMN task_type TEXT DEFAULT '日常业务报销'")
        db.execute("INSERT OR IGNORE INTO schema_migrations(version) VALUES(2)")
        db.commit()
    except Exception:
        db.rollback()
        raise


def cents(value, allow_negative=False):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or abs(amount) > Decimal("9999999999.99") or (amount < 0 and not allow_negative):
            raise ValueError()
        return int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        raise BusinessError("请输入有效的非负金额（最多两位小数，且不超过 9,999,999,999.99 元）")


def secure_zh_filename(filename):
    name = re.split(r"[/\\]", str(filename))[-1]
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip().rstrip(".")
    return name if name and name not in {".", ".."} else "_"


def member_owner(user):
    return user['storage_owner'] or secure_zh_filename(user['real_name'])


def extension(name):
    return str(name).rsplit(".", 1)[-1].lower() if "." in str(name) else ""


def identity(task_id, owner, item_id, filename):
    payload = json.dumps([int(task_id), owner, str(item_id), filename], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def atomic_json(path, data):
    path = Path(path)
    temp = path.with_name(".meta-" + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("x", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


@lru_cache(maxsize=4096)
def _pdf_amount(path, mtime, size):
    cache = None
    if has_app_context():
        key = hashlib.sha256(json.dumps(['amount-v1', path, mtime, size]).encode()).hexdigest()
        cache = Path(current_app.config['DATA_DIR']) / '.cache' / 'pdf-amounts' / (key + '.json')
        try:
            value = json.loads(cache.read_text(encoding='utf-8'))
            if type(value) is int and value >= 0:
                return value
        except (OSError, ValueError):
            pass
    value = _extract_pdf_amount(path)
    if cache is not None:
        temp = cache.with_name(uuid.uuid4().hex + '.tmp')
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            temp.write_text(json.dumps(value), encoding='utf-8')
            os.replace(temp, cache)
        except OSError:
            pass  # Disposable cache; a read-only cache directory cannot block work.
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
    return value


def _extract_pdf_amount(path):
    try:
        text = "".join(page.extract_text() or "" for page in PdfReader(path).pages)
        text = re.sub(r"[\s　,，\"'\r\n]", "", text)
        for pattern in [r"小写[^\d]{0,10}([0-9]+\.[0-9]{2})", r"价税合计[^\d]{0,20}([0-9]+\.[0-9]{2})", r"(?:合计金额|总金额|金额)[^\d]{0,10}([0-9]+\.[0-9]{2})"]:
            match = re.search(pattern, text)
            if match:
                return cents(match.group(1))
        matches = re.findall(r"[¥￥]([0-9]+\.[0-9]{2})", text)
        return max((cents(v) for v in matches), default=0)
    except Exception:
        return 0


def pdf_amount(path):
    path = Path(path)
    try:
        stat = path.stat()
        return _pdf_amount(str(path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return 0


def distribute(total, weights):
    """Largest remainders, in integer cents: never introduce or lose a cent."""
    if not weights:
        return []
    weights = [max(0, w) for w in weights]
    if not sum(weights):
        weights = [1] * len(weights)
    sign = -1 if total < 0 else 1
    total = abs(total)
    denominator = sum(weights)
    result = [total * w // denominator for w in weights]
    order = sorted(range(len(weights)), key=lambda i: (-(total * weights[i] % denominator), i))
    for i in order[:total - sum(result)]:
        result[i] += 1
    return [v * sign for v in result]


def read_meta(directory):
    meta = json.loads((Path(directory) / "meta.json").read_text(encoding="utf-8-sig"))
    if not isinstance(meta, dict):
        raise ValueError("meta.json 必须为对象")
    raw_names = meta.get("invoice_files")
    if not isinstance(raw_names, list):
        raw_names = [meta["invoice_file"]] if meta.get("invoice_file") else []
    def names(values):
        return list(dict.fromkeys(v for v in values if isinstance(v, str) and v == secure_zh_filename(v)))
    meta["invoice_files"] = names(raw_names)
    meta["attachments"] = names(meta.get("attachments", []) or [])
    meta["id"] = Path(directory).name.removeprefix("item_")
    return meta


def base_amounts(meta, directory):
    names = meta["invoice_files"]
    total = cents(meta.get("auto_amount") or 0, allow_negative=True)
    known = meta.get("invoice_amounts", {})
    complete = isinstance(known, dict) and all(name in known for name in names)
    if len(names) == 1 and not complete:
        return [total], False
    weights = [cents(known[name] or 0, allow_negative=True) for name in names] if complete else [pdf_amount(Path(directory) / name) for name in names]
    # Explicit null means unknown, rather than an invoice worth zero. Partial
    # recognition must retain known values without redistributing them.
    amounts = weights if complete and (meta.get("auto_amount") is None or sum(weights) == total) else distribute(total, weights)
    return amounts, len(names) > 1 and not complete


def warm_invoice_cache():
    """Prepare only legacy multi-invoice weights before serving login requests."""
    root = Path(current_app.config['UPLOAD_FOLDER']).resolve()
    for path in root.glob('task_*/*/item_*/meta.json'):
        if not path.resolve().is_relative_to(root):
            continue
        try:
            meta = read_meta(path.parent)
            if len(meta['invoice_files']) > 1:
                base_amounts(meta, path.parent)
        except (OSError, ValueError, TypeError, BusinessError):
            continue  # Normal collection reads retain and report these warnings.


def collect_invoices(task_id=None, owner=None):
    db = get_db()
    windows = {r["id"]: dict(r) for r in db.execute("SELECT * FROM windows")}
    assignments = {(r["window_id"], member_owner(r)): dict(r) for r in db.execute("SELECT a.*,u.real_name,u.storage_owner FROM assignments a JOIN users u ON u.id=a.user_id")}
    allocations = {(r["window_id"], r["owner"], r["item_id"], r["filename"]): dict(r) for r in db.execute("SELECT a.*,c.name AS category_name FROM invoice_allocations a LEFT JOIN project_categories c ON c.id=a.category_id")}
    overrides = {(r['window_id'],r['owner'],r['item_id'],r['filename']):dict(r) for r in db.execute('SELECT * FROM invoice_category_overrides')}
    results, warnings, loose_attachments = [], [], defaultdict(list)
    root = Path(current_app.config["UPLOAD_FOLDER"])
    for task in sorted(root.glob("task_*")):
        if not task.is_dir() or not re.fullmatch(r"task_\d+", task.name) or task.is_symlink():
            continue
        tid = int(task.name[5:])
        if task_id is not None and tid != task_id:
            continue
        window = windows.get(tid, {})
        for person in sorted(task.iterdir()):
            if not person.is_dir() or person.is_symlink() or (owner is not None and person.name != owner):
                continue
            assignment = assignments.get((tid, person.name), {})
            for directory in sorted(person.glob("item_*")):
                if not directory.is_dir() or directory.is_symlink():
                    continue
                try:
                    meta = read_meta(directory)
                    amounts, legacy = base_amounts(meta, directory)
                except (OSError, ValueError, TypeError, BusinessError) as error:
                    warnings.append(f"任务 {tid} / {person.name} / {directory.name}：元数据无法读取，请检查备份（{error}）")
                    continue
                item_id = meta["id"]
                attachments = []
                for filename in meta["attachments"]:
                    url = "/admin/view_file/" + "/".join(quote(str(v), safe="") for v in [tid, person.name, item_id, filename])
                    attachments.append({"id": identity(tid, person.name, item_id, filename), "name": filename, "url": url,
                                        "printable": extension(filename) in PRINT_EXTENSIONS, "missing": not (directory / filename).is_file(), "_path": directory / filename})
                if not meta['invoice_files']:
                    loose_attachments[(tid, person.name, meta.get('type') or '其他')].extend(attachments)
                for filename, amount in zip(meta["invoice_files"], amounts):
                    allocation = allocations.get((tid, person.name, item_id, filename), {})
                    override = overrides.get((tid,person.name,item_id,filename), {})
                    effective = allocation.get("amount_cents")
                    effective = amount if effective is None else effective
                    known = meta.get("invoice_amounts", {})
                    pending = isinstance(known, dict) and filename in known and known[filename] is None and allocation.get("amount_cents") is None
                    url = "/admin/view_file/" + "/".join(quote(str(v), safe="") for v in [tid, person.name, item_id, filename])
                    results.append({"id": identity(tid, person.name, item_id, filename), "task_id": tid, "task_title": window.get("title", f"历史任务 #{tid}"),
                                    "owner": person.name, "real_name": assignment.get("real_name", person.name), "user_id": assignment.get("user_id"), "item_id": item_id,
                                    "filename": filename, "url": url, "original_category": meta.get("type") or "其他", "category": allocation.get("category_name") or override.get('category') or meta.get("type") or "其他",
                                    "project_id": allocation.get("project_id"), "category_id": allocation.get("category_id"), "amount_cents": effective, "amount": None if pending else effective / 100,
                                    "amount_pending": pending, "task_completed": bool(window.get("is_completed")),
                                    "collection_closed": bool(window.get('is_collection_closed') or window.get('is_completed')),
                                    "calibrated": allocation.get("amount_cents") is not None or bool(meta.get("is_admin_calibrated")),
                                    "legacy_allocated": legacy and allocation.get("amount_cents") is None, "remark": meta.get("remark", ""),
                                    "submission_status": assignment.get("status"), "assignable": assignment.get("status") == 2 or bool(window.get("is_completed")),
                                    "orphaned": not bool(assignment), "missing": not (directory / filename).is_file(), "attachments": attachments,
                                    "version": allocation.get("updated_at", "") + ('|' + override['updated_at'] if override else ''), "_path": directory / filename, "_item_path": directory})
    for row in results:
        extra = loose_attachments.get((row['task_id'], row['owner'], row['original_category']), [])
        if extra:
            row['attachments'] = row['attachments'] + extra
    return results, warnings


def public(value):
    if isinstance(value, dict):
        return {key: public(v) for key, v in value.items() if not key.startswith("_")}
    if isinstance(value, list):
        return [public(v) for v in value]
    return value


def task_summaries(invoices, windows, manual_expenses=None):
    """Submitted invoices only; default expense categories remain visible at zero."""
    result = []
    for window in windows:
        all_rows = [r for r in invoices if r['task_id'] == window['id']]
        rows = [r for r in all_rows if r['submission_status'] == 2]
        names = list(TRAVEL_CATEGORIES if window['task_type'] == '国内差旅费' else DAILY_CATEGORIES)
        names += sorted({r['category'] for r in all_rows} - set(names))
        def aggregate(entries):
            pending = sum(r['amount_pending'] for r in entries)
            known = sum(r['amount_cents'] for r in entries if not r['amount_pending'])
            return {'amount_cents':None if pending else known, 'known_amount_cents':known,
                    'pending_amount_count':pending, 'invoice_count':len(entries)}
        summary = dict(window, **aggregate(rows))
        summary['categories'] = [dict(name=name, **aggregate([r for r in rows if r['category'] == name])) for name in names]
        allocated = aggregate([r for r in rows if r['project_id'] is not None])
        summary['project_amount_cents'] = allocated['amount_cents']
        summary['project_invoice_count'] = allocated['invoice_count']
        summary['project_pending_amount_count'] = allocated['pending_amount_count']
        manual = [e for e in (manual_expenses or []) if e['task_id']==window['id']]
        summary['manual_amount_cents'] = sum(e['amount_cents'] for e in manual)
        summary['manual_count'] = len(manual)
        summary['manual_categories'] = [{'name':name,'amount_cents':sum(e['amount_cents'] for e in manual if e['category']==name)} for name in sorted({e['category'] for e in manual})]
        summary['reimbursement_amount_cents'] = None if summary['amount_cents'] is None else summary['amount_cents'] + summary['manual_amount_cents']
        result.append(summary)
    return result


def collect_manual_expenses(task_id=None,user_id=None):
    query='''SELECT e.*,u.real_name,COALESCE(u.archived_username,u.username) AS username,u.storage_owner,w.title AS task_title,w.is_completed AS task_completed,w.is_collection_closed,
             c.name AS project_category FROM manual_expenses e JOIN users u ON u.id=e.user_id JOIN windows w ON w.id=e.task_id
             LEFT JOIN project_categories c ON c.id=e.category_id WHERE e.deleted_at IS NULL'''
    values=[]
    for name,value in [('task_id',task_id),('user_id',user_id)]:
        if value is not None:query+=' AND e.'+name+'=?';values.append(value)
    result=[]
    for record in get_db().execute(query+' ORDER BY e.created_at,e.id',values):
        entry=dict(record)
        entry['category']=entry.pop('project_category') or entry['category']
        entry['owner']=member_owner(record)
        del entry['storage_owner']
        entry['version']=entry['updated_at']
        result.append(entry)
    return result


def project_task_links():
    """Get persisted project/task relationships, even if a source file cannot be read."""
    links = defaultdict(list)
    for row in get_db().execute("""
        WITH project_tasks(project_id, task_id) AS (
            SELECT project_id, window_id FROM invoice_allocations WHERE project_id IS NOT NULL
            UNION
            SELECT project_id, task_id FROM manual_expenses
            WHERE project_id IS NOT NULL AND deleted_at IS NULL
        )
        SELECT link.project_id, task.title, task.is_completed FROM project_tasks AS link
        LEFT JOIN windows AS task ON task.id = link.task_id
        ORDER BY link.task_id DESC
    """):
        links[row["project_id"]].append(row)
    return links


def project_is_archived(tasks):
    return bool(tasks) and all(task["is_completed"] == 1 for task in tasks)


def require_active_projects(project_ids):
    ids = {project_id for project_id in project_ids if project_id is not None}
    if ids:
        links = project_task_links()
        if any(project_is_archived(links[project_id]) for project_id in ids):
            raise BusinessError("经费项目已归档，请先在对应收集任务中撤销结项", 409)


def projects_with_archive_status():
    links = project_task_links()
    result = []
    for project in get_db().execute("SELECT * FROM projects ORDER BY id"):
        tasks = links[project["id"]]
        titles = list(dict.fromkeys(task["title"] for task in tasks if task["title"]))
        result.append(dict(project, is_archived=project_is_archived(tasks), task_titles=titles))
    return result


def project_summaries(invoices,manual_expenses=None):
    db = get_db()
    projects = projects_with_archive_status()
    for project in projects:
        rows = [r for r in invoices if r["project_id"] == project["id"]]
        manual = [r for r in (manual_expenses or []) if r['project_id']==project['id']]
        project['invoice_amount_cents']=sum(r['amount_cents'] for r in rows)
        project['manual_amount_cents']=sum(r['amount_cents'] for r in manual)
        project['manual_count']=len(manual)
        project["total_cents"] = project['invoice_amount_cents']+project['manual_amount_cents']
        project["invoice_count"] = len(rows)
        categories = [dict(c) for c in db.execute("SELECT * FROM project_categories WHERE project_id=? ORDER BY sort_order,id", (project["id"],))]
        for category in categories:
            entries = [r for r in rows if r["category_id"] == category["id"]]
            manual_entries=[r for r in manual if r['category_id']==category['id']]
            category["amount_cents"] = sum(r["amount_cents"] for r in entries+manual_entries)
            category["invoice_count"] = len(entries)
            category['manual_count']=len(manual_entries)
        project["categories"] = categories
        users = defaultdict(lambda: {"amount_cents": 0, "invoice_count": 0, 'manual_count':0})
        for row in rows:
            user = users[row["owner"]]
            user["real_name"] = row["real_name"]
            user["amount_cents"] += row["amount_cents"]
            user["invoice_count"] += 1
        for row in manual:
            user=users[row['owner']]
            user['real_name']=row['real_name'];user['amount_cents']+=row['amount_cents'];user['manual_count']+=1
        project["users"] = sorted(users.values(), key=lambda u: u["real_name"])
    return projects


def invoice_selection(ids):
    if not isinstance(ids, list) or not ids or any(not isinstance(v, str) for v in ids):
        raise BusinessError("请先选择发票")
    rows, _ = collect_invoices()
    by_id = {r["id"]: r for r in rows}
    if any(identifier not in by_id for identifier in ids):
        raise BusinessError("部分发票已变化或不存在，请刷新后重试")
    return [by_id[identifier] for identifier in dict.fromkeys(ids)]


def save_allocation(row, project_id, category_id, amount_cents=None, keep_amount=True):
    get_db().execute("""INSERT INTO invoice_allocations(window_id,owner,item_id,filename,project_id,category_id,amount_cents,updated_at)
        VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(window_id,owner,item_id,filename) DO UPDATE SET
        project_id=excluded.project_id,category_id=excluded.category_id,
        amount_cents=CASE WHEN ? THEN invoice_allocations.amount_cents ELSE excluded.amount_cents END,updated_at=excluded.updated_at""",
        (row["task_id"], row["owner"], row["item_id"], row["filename"], project_id, category_id, amount_cents,
         datetime.now(timezone.utc).isoformat() + "-" + uuid.uuid4().hex[:8], int(keep_amount)))
