"""Run local browser checks with an isolated demo server in the same process."""
import argparse
import os
import runpy
import sys
import threading
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUITES = {
    "full": "workflows",
    "member-accounts": "member_accounts",
    "project-archive": "project_archive",
    "responsive-dialogs": "responsive_dialogs",
    "account-and-guide": "account_and_guide",
    "upload-drafts": "upload_drafts",
    "project-editing": "project_editing",
    "task-deletion": "task_deletion",
    "task-overview": "task_overview",
    "manual-expenses": "manual_expenses",
}
parser = argparse.ArgumentParser(description=__doc__)
selection = parser.add_mutually_exclusive_group()
selection.add_argument("--suite", choices=SUITES)
legacy_flags = {
    "full": "full",
    "member-accounts": "member-accounts",
    "project-archive": "project-archive",
    "v22": "account-and-guide",
    "v23": "upload-drafts",
    "v24": "project-editing",
    "v25": "task-deletion",
    "v26": "task-overview",
    "v27": "manual-expenses",
}
for flag, name in legacy_flags.items():
    selection.add_argument(f"--{flag}", dest="suite", action="store_const", const=name, help=argparse.SUPPRESS)
parser.add_argument("--dialog-only", action="store_true", help=argparse.SUPPRESS)
parser.add_argument("--old-dialog", action="store_true", help=argparse.SUPPRESS)
args = parser.parse_args()
suite = args.suite or "responsive-dialogs"
if suite != "responsive-dialogs" and (args.dialog_only or args.old_dialog):
    parser.error("dialog debug flags require the responsive-dialogs suite")

os.environ["LABINVOICE_PREVIEW_NAME"] = "browser-" + uuid.uuid4().hex[:10]
from preview_server import app, seed
from werkzeug.serving import make_server

seed()
server = make_server("127.0.0.1", 0, app, threaded=True)
worker = threading.Thread(target=server.serve_forever, daemon=True)
worker.start()
base = f"http://127.0.0.1:{server.server_port}"
print("Isolated browser server:", base, flush=True)
try:
    script = ROOT / "tests" / "browser" / f"{SUITES[suite]}.py"
    sys.argv = [str(script), "--base-url", base]
    if args.dialog_only:
        sys.argv.append("--dialog-only")
    if args.old_dialog:
        sys.argv.append("--old-dialog")
    runpy.run_path(str(script), run_name="__main__")
finally:
    server.shutdown()
    server.server_close()
