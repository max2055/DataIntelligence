#!/usr/bin/env bash
set -euo pipefail
task_script_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
task_project_root="$(CDPATH= cd -- "${task_script_dir}/.." && pwd)"
cd "${task_project_root}"
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
if ! .venv/bin/python -c 'import pymysql' >/dev/null 2>&1; then
  .venv/bin/python -m pip install -r requirements.txt
fi
"${task_script_dir}/start_demo_db.sh"
exec .venv/bin/python demo.py "$@"
