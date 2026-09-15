#!/usr/bin/env python3
"""Show every stage of a business-model-driven query against real MySQL."""

import argparse
import json
import os
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from data_intelligence.engine import DemoError, ROOT, build_plan, compile_plan, load_model, parse_question, run_query

DEFAULT_QUESTION = "2026年9月14日，信用债占净资产比例超过10%的固收组合有哪些？"
STATUS_LABELS = {
    "OK": "数据可计算",
    "MISSING_VALUATION": "缺少当日估值快照",
    "INCOMPLETE_HOLDINGS": "当日持仓不完整",
    "MISSING_NAV": "净资产缺失",
    "NON_POSITIVE_NAV": "净资产为零或负数",
}


def json_default(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"Cannot serialize {type(value)}")


def dump(value):
    return json.dumps(value, ensure_ascii=False, indent=2, default=json_default, allow_nan=False)


def percentage(value):
    return "—" if value is None else f"{Decimal(value) * 100:.2f}%"


def table(rows, audit=False):
    if not rows:
        return "没有符合条件的组合。"
    lines = ["| 组合代码 | 组合名称 | 分子（元） | 分母（元） | 占比 | " + ("说明 |" if audit else ""),
             "|---|---|---:|---:|---:|" + ("---|" if audit else "")]
    for row in rows:
        cells = [str(row.get("portfolio_id", "—")), row.get("name", "—"),
                 "—" if row.get("numerator") is None else str(row["numerator"]),
                 "—" if row.get("denominator") is None else str(row["denominator"]), percentage(row.get("ratio"))]
        if audit:
            status = row["quality_status"]
            cells.append(("符合条件" if row["is_match"] else "未达到筛选条件") if status == "OK" else STATUS_LABELS[status])
        # Names are data: avoid creating unintended Markdown cells or lines.
        lines.append("| " + " | ".join(cell.replace("|", "\\|").replace("\n", " ") for cell in cells) + " |")
    return "\n".join(lines)


def save_artifacts(output, question, intent, plan, compiled, results):
    output.mkdir(parents=True, exist_ok=True)
    artifacts = {"01-intent.json": dump(intent), "02-plan.json": dump(plan),
                 "03-query.sql": compiled["sql"] + ";\n",
                 "03-parameters.json": dump(compiled["parameters"]),
                 "04-audit-query.sql": compiled["audit_sql"] + ";\n"}
    if results is not None:
        artifacts["05-results.json"] = dump(results)
    summary = ["# 固收智能问数：最小 Demo", "", f"> {question}", "",
               "这是真实 MySQL 查询的演示；自然语言解析使用受限的规则模拟器，没有调用大语言模型。", "",
               f"估值日：{plan['as_of_date']}。当前模拟用户组：FI_TEAM。金额单位：人民币元，数据均为虚构。", "",
               f"指标：{plan['metric']['label']}（{plan['metric']['version']}）。模型版本：{plan['model']['version']}。", ""]
    if intent.get("assumptions"):
        summary += ["采用的默认值：", ""] + [f"- {item}" for item in intent["assumptions"]] + [""]
    if results is not None:
        summary += ["## 查询结果", "", table(results["rows"]), "", "## 范围内所有组合的计算解释", "",
                    table(results["audit_rows"], audit=True), "",
                    "异常组合不会当作零占比；异常行的分子仅反映已读取的数据。其他业务及无权限组合不进入结果和解释表。", ""]
    else:
        summary += ["本次仅生成查询计划和 SQL，尚未连接数据库执行。", ""]
    summary += ["## 1. 结构化查询意图", "", "```json", dump(intent), "```", "",
                "## 2. 模型展开后的查询计划", "", "```json", dump(plan), "```", "",
                "## 3. 确定生成的 SQL", "", "```sql", compiled["sql"] + ";", "```", "",
                "SQL 中的占位符由数据库驱动绑定，参数如下：", "", "```json", dump(compiled["parameters"]), "```", "",
                "## 实现范围", "", "本示例支持组合单日的“汇总分子 / 快照分母”指标。分类固定、币种统一；未实现母子组合穿透、历史分类版本、收益率和真实登录。", "",
                "模型定义与代码见项目 model/ontology.json 和 data_intelligence/engine.py。", ""]
    artifacts["report.md"] = "\n".join(summary)
    for name, contents in artifacts.items():
        (output / name).write_text(contents + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="固收智能问数：模型 → 查询计划 → SQL → MySQL 结果")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--question", help="使用模拟解析器解析的中文问题")
    source.add_argument("--intent", type=Path, help="直接加载结构化查询意图 JSON，绕过模拟解析器")
    parser.add_argument("--model", type=Path, default=ROOT / "model/ontology.json", help="可编辑的业务模型 JSON")
    parser.add_argument("--output", type=Path, default=None, help="结果输出目录，默认 output/latest 或 output/plan-only")
    parser.add_argument("--plan-only", action="store_true", help="只生成计划与 SQL，不连接数据库")
    args = parser.parse_args()
    try:
        model = load_model(args.model)
        if args.intent:
            intent = json.loads(args.intent.read_text(encoding="utf-8"))
            question = f"直接加载结构化查询意图：{args.intent.name}"
        else:
            question = args.question or DEFAULT_QUESTION
            intent = parse_question(question, model)
        # This is execution context, deliberately outside user-supplied intent.
        plan = build_plan(intent, model, principal_groups=("FI_TEAM",))
        compiled = compile_plan(plan)
        print(f"问题：{question}\n")
        print("[1/4] 结构化意图（模拟解析器，不调用大模型）")
        print(dump(intent))
        print("\n[2/4] 从模型展开查询计划")
        print(dump(plan))
        print("\n[3/4] 确定生成 SQL 和绑定参数")
        print(compiled["sql"])
        print(dump(compiled["parameters"]))
        results = None
        if not args.plan_only:
            try:
                import pymysql
            except ImportError as exc:
                raise DemoError("尚未安装依赖，请先运行 ./scripts/run_demo.sh") from exc
            try:
                connection = pymysql.connect(
                    host=os.getenv("MYSQL_HOST", "127.0.0.1"),
                    port=int(os.getenv("MYSQL_PORT", os.getenv("DEMO_MYSQL_PORT", "13316"))),
                    user=os.getenv("MYSQL_USER", "demo"), password=os.getenv("MYSQL_PASSWORD", "demo-local-only"),
                    database=os.getenv("MYSQL_DATABASE", "ontology_demo"),
                    charset="utf8mb4", autocommit=True, connect_timeout=10, read_timeout=30, write_timeout=30)
                try:
                    results = run_query(compiled, connection)
                finally:
                    connection.close()
            except pymysql.MySQLError as exc:
                raise DemoError(f"MySQL 查询失败：{exc}。请确认 ./scripts/start_demo_db.sh 已成功运行。") from exc
            print("\n[4/4] MySQL 实际查询结果")
            print(table(results["rows"]))
            print("\n范围内组合的计算解释：")
            print(table(results["audit_rows"], audit=True))
        else:
            print("\n[4/4] 仅生成计划，本次不执行查询。")
        output = (args.output or ROOT / "output" / ("plan-only" if args.plan_only else "latest")).resolve()
        save_artifacts(output, question, intent, plan, compiled, results)
        print(f"\n完整演示报告：{output / 'report.md'}")
        return 0
    except (DemoError, OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        print(f"无法完成查询：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
