"""Project-scoped original-file archives and explicit, loss-aware PDF printing."""
import csv
import hashlib
import io
import json
import zipfile
from collections import defaultdict
from pathlib import Path

from flask import abort, jsonify, request, send_file
from PIL import Image, ImageOps, ImageSequence
from pypdf import PdfReader, PdfWriter

from storage import BusinessError, collect_invoices, collect_manual_expenses, extension, get_db, public, secure_zh_filename

try:
    from pypinyin import lazy_pinyin
except ImportError:
    def lazy_pinyin(value):
        return list(value)


def ordered(rows):
    return sorted(rows, key=lambda r: (r["category_id"] or 0, r["category"], "".join(lazy_pinyin(r["real_name"])), r["task_id"], r["item_id"], r["filename"]))


def print_manifest(rows):
    files, seen = [], set()
    for row in ordered(rows):
        if row["id"] not in seen:
            files.append({"id": row["id"], "name": row["filename"], "owner": row["real_name"], "category": row["category"],
                          "kind": "invoice", "printable": True, "missing": row["missing"], "selected": True, "url": row["url"], "_path": row["_path"]})
            seen.add(row["id"])
        for attachment in row["attachments"]:
            if attachment["id"] not in seen:
                files.append({**attachment, "owner": row["real_name"], "category": row["category"], "kind": "attachment", "selected": False})
                seen.add(attachment["id"])
    return files


def merged_pdf(files, selected_ids):
    if not isinstance(selected_ids, list) or not selected_ids or any(not isinstance(v, str) for v in selected_ids):
        raise BusinessError("请至少勾选一个打印文件")
    selected = set(selected_ids)
    by_id = {f["id"]: f for f in files}
    if not selected <= by_id.keys():
        raise BusinessError("部分文件已不属于此项目，请刷新打印清单")
    writer = PdfWriter()
    for file in files:
        if file["id"] not in selected:
            continue
        if not file["printable"]:
            raise BusinessError("该附件暂不支持合并打印，请下载后处理：" + file["name"])
        try:
            path = file["_path"]
            if extension(file["name"]) == "pdf":
                reader = PdfReader(path)
                if not reader.pages:
                    raise ValueError("PDF 没有页面")
                writer.append(reader)
            else:
                with Image.open(path) as image:
                    for frame in ImageSequence.Iterator(image):
                        frame = ImageOps.exif_transpose(frame).convert("RGBA")
                        page = Image.new("RGB", frame.size, "white")
                        page.paste(frame, mask=frame.getchannel("A"))
                        stream = io.BytesIO()
                        page.save(stream, format="PDF")
                        stream.seek(0)
                        writer.append(PdfReader(stream))
        except Exception:
            raise BusinessError("无法合并文件，请检查是否缺失、损坏或加密：" + file["name"])
    stream = io.BytesIO()
    writer.write(stream)
    writer.close()
    stream.seek(0)
    return stream


def csv_bytes(headers, rows):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([("'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v) for v in row])
    return output.getvalue().encode("utf-8-sig")


def manual_csv_bytes(rows):
    return csv_bytes(['记录ID','任务','成员','报销项','金额（元）','备注'],[[r['id'],r['task_title'],r['real_name'],r['category'],f"{r['amount_cents']/100:.2f}",r['remark']] for r in rows])


def project_zip(project, rows, manual_expenses=None):
    manual_expenses=manual_expenses or []
    if not rows and not manual_expenses:
        raise BusinessError("此项目还没有报销记录")
    stream, entries, seen = io.BytesIO(), [], set()
    names = {row['owner']: row['real_name'] for row in rows + manual_expenses}
    grouped, by_category, by_user = defaultdict(lambda: [0, 0, 0]), defaultdict(lambda: [0, 0, 0]), defaultdict(lambda: [0, 0, 0])
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for row in ordered(rows):
            group = grouped[(row["category"], row["owner"])]
            group[0] += 1
            group[1] += row["amount_cents"]
            for index, key in [(by_category, row["category"]), (by_user, row["owner"])]:
                index[key][0] += 1
                index[key][1] += row["amount_cents"]
            files = [(row["id"], row["filename"], row["_path"])] + [(a["id"], a["name"], a["_path"]) for a in row["attachments"]]
            for identifier, filename, path in files:
                if identifier in seen:
                    continue
                seen.add(identifier)
                try:
                    content = path.read_bytes()
                except OSError:
                    raise BusinessError("原始文件缺失，未生成不完整压缩包：" + filename)
                source_base = f"票据/{secure_zh_filename(row['category'])}/{secure_zh_filename(row['owner'])}/task_{row['task_id']}/{path.parent.name}"
                arcname = source_base + "/" + filename
                archive.writestr(arcname, content)
                entries.append({"id": identifier, "path": arcname, "sha256": hashlib.sha256(content).hexdigest()})
        for row in manual_expenses:
            for group in [grouped[(row['category'],row['owner'])],by_category[row['category']],by_user[row['owner']]]:
                group[1]+=row['amount_cents'];group[2]+=1
        archive.writestr("报销汇总.csv", csv_bytes(["项目名称", "项目号", "报销项", "成员", "发票数",'无电子发票项数', "金额（元）"],
            [[project["name"], project["code"], cat, names[owner], count,manual_count, f"{amount / 100:.2f}"] for (cat, owner), (count, amount,manual_count) in grouped.items()]))
        archive.writestr("报销项小计.csv", csv_bytes(["报销项", "发票数",'无电子发票项数', "金额（元）"], [[name, count,manual_count, f"{amount / 100:.2f}"] for name, (count, amount,manual_count) in by_category.items()]))
        archive.writestr("人员合计.csv", csv_bytes(["成员", "发票数",'无电子发票项数', "金额（元）"], [[names[owner], count,manual_count, f"{amount / 100:.2f}"] for owner, (count, amount,manual_count) in by_user.items()]))
        if manual_expenses:archive.writestr('无电子发票报销项.csv',manual_csv_bytes(manual_expenses))
        archive.writestr("归档清单.json", json.dumps({"project": project, "total_cents": sum(r["amount_cents"] for r in rows+manual_expenses), "invoices": public(rows), 'manual_expenses':manual_expenses,"files": entries}, ensure_ascii=False, indent=2))
    stream.seek(0)
    return stream


def register_exports(app, admin_required):
    def project_rows(project_id):
        project = get_db().execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if project is None:
            abort(404)
        rows, warnings = collect_invoices()
        if warnings:
            raise BusinessError("有历史记录无法读取，请先处理整理台中的提示再导出")
        return dict(project), [r for r in rows if r["project_id"] == project_id], [r for r in collect_manual_expenses() if r['project_id']==project_id]

    @app.route("/api/admin/projects/<int:project_id>/print-manifest")
    @admin_required
    def project_print_manifest(project_id):
        project, rows, manual = project_rows(project_id)
        return jsonify(project=project, files=public(print_manifest(rows)), invoice_count=len(rows),manual_count=len(manual))

    @app.route("/admin/projects/<int:project_id>/print", methods=["POST"])
    @admin_required
    def print_project(project_id):
        project, rows, _ = project_rows(project_id)
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            raise BusinessError("请求内容格式错误")
        stream = merged_pdf(print_manifest(rows), data.get("file_ids"))
        return send_file(stream, mimetype="application/pdf", as_attachment=False, download_name=secure_zh_filename(project["name"]) + "-合并打印.pdf")

    @app.route("/admin/projects/<int:project_id>/download")
    @admin_required
    def download_project(project_id):
        project, rows, manual = project_rows(project_id)
        return send_file(project_zip(project, rows,manual), mimetype="application/zip", as_attachment=True, download_name=secure_zh_filename(project["name"]) + "-报销归档.zip")

    @app.route("/admin/download/<int:task_id>")
    @admin_required
    def download_task_zip(task_id):
        rows, warnings = collect_invoices(task_id)
        manual=collect_manual_expenses(task_id=task_id)
        if warnings:
            raise BusinessError("任务有元数据读取错误，请先处理后下载")
        if not rows and not manual:
            raise BusinessError("此任务暂无电子发票", 404)
        stream, seen = io.BytesIO(), set()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            for row in rows:
                paths = [row["_path"], row["_item_path"] / "meta.json"] + [a["_path"] for a in row["attachments"]]
                paths += [a['_path'].parent / 'meta.json' for a in row['attachments']]
                for path in paths:
                    if path in seen:
                        continue
                    seen.add(path)
                    if not path.is_file():
                        raise BusinessError("原始文件缺失：" + path.name)
                    source_base = f"task_{task_id}/{row['owner']}/{path.parent.name}"
                    archive.write(path, source_base + "/" + path.name)
            archive.writestr("整理清单.json", json.dumps(public(rows), ensure_ascii=False, indent=2))
            if manual:
                archive.writestr('无电子发票报销项.csv',manual_csv_bytes(manual))
                archive.writestr('无电子发票报销项.json',json.dumps(manual,ensure_ascii=False,indent=2))
        stream.seek(0)
        return send_file(stream, mimetype="application/zip", as_attachment=True, download_name=f"收集任务-{task_id}-原始资料.zip")

    @app.route("/admin/merge_print/<int:task_id>")
    @admin_required
    def merge_and_print(task_id):
        rows, warnings = collect_invoices(task_id)
        if warnings:
            raise BusinessError("任务有元数据读取错误，请先处理后打印")
        files = print_manifest(rows)
        return send_file(merged_pdf(files, [f["id"] for f in files if f["selected"]]), mimetype="application/pdf", download_name=f"收集任务-{task_id}-发票汇总.pdf")
