"""Verify a full backup and migrate only an isolated copy of that backup.

The live data directory is opened read-only. No app server is started.
"""
import argparse
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def table_data(path, columns=None):
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        if columns is None:
            columns={name:[r[1] for r in db.execute('PRAGMA table_info('+name+')')] for name in ['users','windows','assignments']}
        return {name:db.execute('SELECT '+','.join('"'+c.replace('"','""')+'"' for c in names)+' FROM '+name+' ORDER BY id').fetchall() for name,names in columns.items()}


def check(backup, data_dir):
    data_dir=Path(data_dir).resolve()
    with closing(sqlite3.connect((data_dir/'labinvoice.db').as_uri()+'?mode=ro',uri=True)) as db:
        columns={name:[r[1] for r in db.execute('PRAGMA table_info('+name+')')] for name in ['users','windows','assignments']}
    stage_root=ROOT/'.test-data'/'upgrade-verification'
    stage_root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='copy-',dir=stage_root) as name:
        stage=Path(name).resolve()
        assert stage.is_relative_to(stage_root.resolve())
        with zipfile.ZipFile(backup) as archive:
            manifest=json.loads(archive.read('backup-manifest.json'))
            for info in archive.infolist():
                if not (stage/info.filename).resolve().is_relative_to(stage):
                    raise ValueError('备份包含无效路径')
            archive.extractall(stage)
        for relative,meta in manifest.items():
            if hashlib.sha256((stage/relative).read_bytes()).hexdigest()!=meta['sha256']:
                raise AssertionError('备份校验失败：'+relative)
            if relative.startswith('uploads/') and hashlib.sha256((data_dir/relative).read_bytes()).hexdigest()!=meta['sha256']:
                raise AssertionError('原始上传文件与备份不一致：'+relative)
        original=table_data(data_dir/'labinvoice.db',columns)
        assert original==table_data(stage/'labinvoice.db',columns),'旧表内容与备份不一致，请先停服重新备份'
        os.environ['LABINVOICE_DATA_DIR']=str(stage)
        sys.path.insert(0,str(ROOT))
        import app
        from storage import cents,collect_invoices,read_meta
        with app.app.app_context():
            rows,warnings=collect_invoices()
            expected=sum(cents(read_meta(p.parent).get('auto_amount',0),allow_negative=True) for p in (stage/'uploads').rglob('meta.json'))
            assert not warnings,warnings
            assert expected==sum(r['amount_cents'] for r in rows),'金额不守恒'
            assert original==table_data(stage/'labinvoice.db',columns),'升级改变了旧表内容'
            for relative,meta in manifest.items():
                if relative.startswith('uploads/'):
                    assert hashlib.sha256((stage/relative).read_bytes()).hexdigest()==meta['sha256']
            result={'passed':True,'old_table_counts':{key:len(value) for key,value in original.items()},
                    'upload_files_verified':sum(name.startswith('uploads/') for name in manifest),
                    'upload_records':len(list((stage/'uploads').rglob('meta.json'))),'invoice_count':len(rows),
                    'original_total_cents':expected,'adapted_total_cents':sum(r['amount_cents'] for r in rows),
                    'raw_data_unchanged':True,'automatic_database_backups':len(list((stage/'.backups').glob('*.db'))),
                    'warnings':warnings}
        return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup',required=True)
    parser.add_argument('--data-dir',default=str(ROOT))
    parser.add_argument('--report',default='test-results/upgrade-check.json')
    args=parser.parse_args()
    result=check(args.backup,args.data_dir)
    report=Path(args.report)
    report.parent.mkdir(parents=True,exist_ok=True)
    report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
