"""Create a verified, portable snapshot without importing or changing the app.

Stop application workers while backing up: SQLite's backup is consistent, but
the database and uploaded files cannot be snapshotted together while uploading.
"""
import argparse
import hashlib
import json
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime
from pathlib import Path


def create_backup(data_dir, output_dir):
    root = Path(data_dir).resolve()
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    archive = output / ("labinvoice-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".zip")
    entries = {}
    # Keep the staging directory next to the output (also works in sandboxes
    # where the host's default temporary directory is not writable).
    with tempfile.TemporaryDirectory(dir=output, prefix="snapshot-") as stage:
        assert Path(stage).resolve().is_relative_to(output)
        db_path = root / "labinvoice.db"
        sources = []
        if db_path.exists():
            snapshot = Path(stage) / "labinvoice.db"
            with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)) as source:
                with closing(sqlite3.connect(snapshot)) as target:
                    source.backup(target)
                    if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise RuntimeError("数据库完整性检查失败")
            sources.append((snapshot, "labinvoice.db"))
        sources.extend((p, p.relative_to(root).as_posix())
                       for p in sorted((root / "uploads").rglob("*")) if p.is_file())
        with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED) as bundle:
            for path, name in sources:
                content = path.read_bytes()
                entries[name] = {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
                bundle.writestr(name, content)
            bundle.writestr("backup-manifest.json", json.dumps(entries, ensure_ascii=False, indent=2))
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip():
            raise RuntimeError("备份压缩包校验失败")
        for name, meta in entries.items():
            if hashlib.sha256(bundle.read(name)).hexdigest() != meta["sha256"]:
                raise RuntimeError("备份文件校验失败：" + name)
    return archive, entries


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--output-dir", default=".backups")
    args = parser.parse_args()
    result, files = create_backup(args.data_dir, args.output_dir)
    print(json.dumps({"backup": str(result), "files": len(files), "verified": True}, ensure_ascii=False))
