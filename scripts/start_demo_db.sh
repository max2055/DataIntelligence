#!/usr/bin/env bash
set -euo pipefail

task_script_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
task_project_root="$(CDPATH= cd -- "${task_script_dir}/.." && pwd)"
export MYSQL_IMAGE="${MYSQL_IMAGE:-mysql:8.4}"
export DEMO_MYSQL_PORT="${DEMO_MYSQL_PORT:-13316}"

if ! [[ "${DEMO_MYSQL_PORT}" =~ ^[0-9]+$ ]] || (( 10#${DEMO_MYSQL_PORT} < 1 || 10#${DEMO_MYSQL_PORT} > 65535 )); then
  printf 'DEMO_MYSQL_PORT 必须是 1 至 65535 的端口号。\n' >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  printf '未找到 Docker，请先安装或启动 Docker Desktop。\n' >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  printf 'Docker 尚未就绪，请启动 Docker Desktop 后重试。\n' >&2
  exit 1
fi
if ! docker image inspect "${MYSQL_IMAGE}" >/dev/null 2>&1; then
  printf '本机没有镜像 %s。请用 MYSQL_IMAGE 指定已安装的 MySQL 8 镜像。\n' "${MYSQL_IMAGE}" >&2
  exit 1
fi

docker compose \
  --project-directory "${task_project_root}" \
  --project-name data-intelligence-demo \
  --file "${task_project_root}/compose.yaml" \
  up --detach --wait --wait-timeout 180 mysql

printf '\n演示数据库已就绪：127.0.0.1:%s / ontology_demo，用户 demo。\n' "${DEMO_MYSQL_PORT}"
printf '数据保存在专用 Docker 卷 data-intelligence-demo-mysql-data 中。\n'
