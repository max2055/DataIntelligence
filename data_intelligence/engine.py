"""Model -> relational plan -> parameterized MySQL. No LLM or SQL guessing."""

from __future__ import annotations

import copy
import json
import math
import re
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SELECT_FIELDS = ["portfolio_id", "name", "numerator", "denominator", "ratio"]
COMPARISONS = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<=", "eq": "="}


class DemoError(ValueError):
    """A query is ambiguous, unsupported, or invalid; never silently guess."""


def require(condition, message):
    if not condition:
        raise DemoError(message)


def identifier(value):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value),
            f"非法标识符：{value!r}")
    return f"`{value}`"


def load_model(path=None):
    with Path(path or ROOT / "model/ontology.json").open(encoding="utf-8") as handle:
        model = json.load(handle)
    validate_model(model)
    return model


def validate_model(model):
    for name, source in model["sources"].items():
        identifier(name)
        identifier(source["table"])
        for field, physical in source["fields"].items():
            identifier(field)
            identifier(physical)
        require(set(source["grain"]) <= set(source["fields"]), f"数据源 {name} 的粒度字段未定义")
    for name, relationship in model["relationships"].items():
        require(relationship["from"] in model["sources"] and relationship["to"] in model["sources"],
                f"关系 {name} 引用了不存在的数据源")
        require(bool(relationship["keys"]), f"关系 {name} 缺少关联键")
        for key in relationship["keys"]:
            require(key["from"] in model["sources"][relationship["from"]]["fields"] and
                    key["to"] in model["sources"][relationship["to"]]["fields"], f"关系 {name} 关联键未定义")


def parse_question(question, model):
    """Constrained simulator, deliberately not a general natural-language parser."""
    require(isinstance(question, str), "问题必须为文本")
    compact = re.sub(r"\s+", "", question)
    match = re.fullmatch(
        r"(?:截至)?(?:(?P<date>\d{4}-\d{1,2}-\d{1,2}|\d{4}年\d{1,2}月\d{1,2}日)[，,]?)?"
        r"(?P<metric>信用债(?:市值)?占净资产比例|信用债占比)"
        r"(?P<op>超过|大于|不低于|大于等于|低于|小于|不超过|小于等于|等于)"
        r"(?P<threshold>\d+(?:\.\d+)?)(?:%|％|百分比)的固收组合有哪些[？?。]?", compact)
    require(match is not None,
            "模拟解析器暂不支持该句式。请使用“2026年9月14日，信用债占净资产比例超过10%的固收组合有哪些？”；"
            "也可通过 --intent 提交结构化意图。母子组合、收益率等场景尚未定义。")
    assumptions = []
    if match["date"]:
        parts = re.findall(r"\d+", match["date"])
        try:
            as_of_date = date(*map(int, parts)).isoformat()
        except ValueError as exc:
            raise DemoError("估值日期无效") from exc
    else:
        as_of_date = model["defaults"]["valuation_date"]
        assumptions.append(f"未指定日期，采用模型的示例日期 {as_of_date}，并非实时最新日期。")
    if match["metric"] == "信用债占比":
        metric_id = model["defaults"]["credit_ratio_metric"]
        require(metric_id in model["metrics"], "默认指标不存在")
        assumptions.append(f"“信用债占比”采用模型默认口径：{model['metrics'][metric_id]['label']}。")
    else:
        candidates = [key for key, metric in model["metrics"].items() if match["metric"] in metric["aliases"]]
        require(len(candidates) == 1, "指标无法唯一绑定，请明确口径")
        metric_id = candidates[0]
    ops = {"超过": "gt", "大于": "gt", "不低于": "gte", "大于等于": "gte",
           "低于": "lt", "小于": "lt", "不超过": "lte", "小于等于": "lte", "等于": "eq"}
    return {"object": "portfolio", "scope": "fixed_income", "as_of_date": as_of_date,
            "metric": metric_id, "operator": ops[match["op"]],
            "threshold": float(match["threshold"]) / 100,
            "select": SELECT_FIELDS.copy(), "assumptions": assumptions}


def build_plan(intent, model, principal_groups=("FI_TEAM",)):
    """Bind IDs and expand measure dependencies into explicit relational nodes."""
    validate_model(model)
    required = {"object", "scope", "as_of_date", "metric", "operator", "threshold", "select"}
    require(isinstance(intent, dict) and required <= set(intent), "查询意图缺少必填字段")
    require(set(intent) <= required | {"assumptions"}, "查询意图包含未支持的字段，不能指定权限或物理数据源")
    require(isinstance(intent.get("assumptions", []), list) and
            all(isinstance(item, str) for item in intent.get("assumptions", [])), "assumptions 必须是文本列表")
    try:
        parsed_date = date.fromisoformat(intent["as_of_date"])
        require(parsed_date.isoformat() == intent["as_of_date"], "日期必须为 YYYY-MM-DD")
    except (ValueError, TypeError) as exc:
        raise DemoError("日期必须为有效的 YYYY-MM-DD") from exc
    require(intent["scope"] in model["scopes"], "未定义的查询范围")
    require(intent["metric"] in model["metrics"], "未定义的指标")
    require(intent["operator"] in COMPARISONS, "未支持的比较运算符")
    threshold = intent["threshold"]
    require(type(threshold) in (int, float) and math.isfinite(threshold) and threshold >= 0,
            "阈值必须为非负有限数值，以小数表示比例，例如 0.10")
    require(isinstance(intent["select"], list) and bool(intent["select"]) and
            all(isinstance(field, str) and field in SELECT_FIELDS for field in intent["select"]) and
            len(set(intent["select"])) == len(intent["select"]), "未支持或重复的返回字段")
    require(isinstance(principal_groups, (list, tuple)) and
            all(isinstance(group, str) and group for group in principal_groups), "用户组参数无效")
    scope = model["scopes"][intent["scope"]]
    metric = model["metrics"][intent["metric"]]
    require(intent["object"] == scope["object"] == metric["object"] == "portfolio", "对象与范围或指标不兼容")
    require(metric["kind"] == "ratio", "当前编译器只支持比率指标")
    require(metric["missing_numerator"] == "zero_when_complete" and metric["valid_denominator"] == "positive",
            "未支持的比率异常处理规则")
    require(metric["numerator"] in model["measures"] and metric["denominator"] in model["measures"],
            "指标依赖的度量未定义")
    numerator = model["measures"][metric["numerator"]]
    denominator = model["measures"][metric["denominator"]]
    require(numerator["kind"] == "sum" and denominator["kind"] == "snapshot", "当前支持汇总分子 / 快照分母")
    require(numerator["unit"] == denominator["unit"], "分子与分母单位不一致")
    require(numerator["group_by"] == denominator["group_by"] == metric["grain"], "分子与分母计算粒度不一致")
    require(numerator["date_field"] == denominator["date_field"], "分子与分母时间维度不一致")
    key, date_field = metric["join_key"], numerator["date_field"]
    require(metric["grain"] == [key, date_field] and key == "portfolio_id", "当前只支持组合 × 估值日粒度")
    coverage = metric["coverage"]
    require(coverage["source"] == denominator["source"] and coverage["required_value"] is True,
            "完整性标志必须来自分母同一估值快照，且要求为 true")
    portfolio_source = model["objects"][intent["object"]]["source"]
    require(model["sources"][portfolio_source]["grain"] == [key], "组合数据源必须按组合唯一")
    require(model["sources"][denominator["source"]]["grain"] == metric["grain"], "分母数据源不满足唯一快照粒度")
    require(model["access_policy"]["source"] == portfolio_source, "权限策略未绑定组合数据源")

    def column(source, field):
        require(source in model["sources"] and field in model["sources"][source]["fields"],
                f"未定义的属性 {source}.{field}")
        return model["sources"][source]["fields"][field]

    def physical_filter(source, condition, alias="b"):
        require(condition["op"] == "eq", "模型筛选当前只支持 eq")
        return {"alias": alias, "column": column(source, condition["field"]),
                "op": "eq", "value": condition["value"]}

    def measure_node(measure, output_name):
        source = measure["source"]
        aliases, joins, filters = {}, [], []
        for condition in measure["filters"]:
            if "relationship" not in condition:
                filters.append(physical_filter(source, condition))
                continue
            relationship_id = condition["relationship"]
            require(relationship_id in model["relationships"], "度量引用了未定义的关系")
            relation = model["relationships"][relationship_id]
            require(relation["from"] == source and relation["cardinality"] in ("many_to_one", "one_to_one"),
                    "不允许可能放大持仓金额的关联方向或基数")
            target = relation["to"]
            require(set(model["sources"][target]["grain"]) <= {pair["to"] for pair in relation["keys"]},
                    "关联目标键不满足唯一粒度，会产生重复汇总")
            if relationship_id not in aliases:
                alias = f"j{len(joins)}"
                aliases[relationship_id] = alias
                joins.append({"type": "inner", "table": model["sources"][target]["table"], "alias": alias,
                              "keys": [{"left": column(source, pair["from"]), "right": column(target, pair["to"])}
                                       for pair in relation["keys"]]})
            filters.append(physical_filter(target, condition, aliases[relationship_id]))
        filters.append({"alias": "b", "column": column(source, date_field), "op": "eq", "value": intent["as_of_date"]})
        columns = [{"column": column(source, field), "as": field} for field in metric["grain"]]
        columns.append({"column": column(source, measure["field"]), "as": "value"})
        if output_name == "denominator":
            columns.append({"column": column(source, coverage["field"]), "as": "complete"})
        return {"id": output_name, "op": "aggregate" if measure["kind"] == "sum" else "scan",
                "table": model["sources"][source]["table"], "columns": columns, "joins": joins,
                "filters": filters, "group_by": list(metric["grain"]) if measure["kind"] == "sum" else []}

    scope_filters = [physical_filter(portfolio_source, condition) for condition in scope["filters"]]
    scope_filters.append({"alias": "b", "column": column(portfolio_source, model["access_policy"]["field"]),
                          "op": "in", "value": list(principal_groups)})
    steps = [
        {"id": "scope", "op": "scan", "table": model["sources"][portfolio_source]["table"],
         "columns": [{"column": column(portfolio_source, field), "as": field} for field in [key, "name"]],
         "joins": [], "filters": scope_filters, "group_by": []},
        measure_node(numerator, "numerator"), measure_node(denominator, "denominator"),
        {"id": "calculated", "op": "ratio", "scope": "scope", "numerator": "numerator", "denominator": "denominator",
         "join_key": key, "date_field": date_field, "as_of_date": intent["as_of_date"],
         "missing_numerator": metric["missing_numerator"],
         "valid_denominator": metric["valid_denominator"], "require_complete": True},
        {"id": "evaluated", "op": "compare", "source": "calculated", "operator": intent["operator"],
         "threshold": threshold},
        {"id": "result", "op": "project_matches", "source": "evaluated", "select": intent["select"], "order_by": key}
    ]
    return copy.deepcopy({"plan_version": 1, "model": {"id": model["id"], "version": model["version"]},
                          "metric": {"id": intent["metric"], "label": metric["label"], "version": metric["version"],
                                     "unit": metric["unit"], "classification": metric["classification"]},
                          "as_of_date": intent["as_of_date"], "assumptions": intent.get("assumptions", []), "steps": steps})


def compile_plan(plan):
    """Compile the explicit plan, without consulting the ontology or question."""
    parameters, ctes = {}, []

    def bind(value):
        name = f"p{len(parameters) + 1}"
        parameters[name] = value
        return f"%({name})s"

    def ref(alias, column):
        return f"{identifier(alias)}.{identifier(column)}"

    def predicate(condition):
        lhs = ref(condition["alias"], condition["column"])
        if condition["op"] == "in":
            values = condition["value"]
            return f"{lhs} IN ({', '.join(bind(value) for value in values)})" if values else "1 = 0"
        require(condition["op"] == "eq", "计划包含不支持的筛选操作")
        return f"{lhs} = {bind(condition['value'])}"

    require(plan["plan_version"] == 1, "不支持的计划版本")
    require([step["id"] for step in plan["steps"]] ==
            ["scope", "numerator", "denominator", "calculated", "evaluated", "result"], "不支持的计划结构")
    for step in plan["steps"]:
        operation = step["op"]
        if operation in ("scan", "aggregate"):
            expressions = []
            for item in step["columns"]:
                expression = ref("b", item["column"])
                if operation == "aggregate" and item["as"] == "value":
                    expression = f"SUM({expression})"
                expressions.append(f"{expression} AS {identifier(item['as'])}")
            sql = f"SELECT {', '.join(expressions)}\nFROM {identifier(step['table'])} AS `b`"
            for join in step["joins"]:
                require(join["type"] == "inner", "不支持的关联类型")
                terms = [f"{ref('b', pair['left'])} = {ref(join['alias'], pair['right'])}" for pair in join["keys"]]
                sql += f"\nINNER JOIN {identifier(join['table'])} AS {identifier(join['alias'])} ON {' AND '.join(terms)}"
            if step["filters"]:
                sql += "\nWHERE " + " AND ".join(predicate(item) for item in step["filters"])
            if operation == "aggregate":
                columns_by_alias = {item["as"]: item["column"] for item in step["columns"]}
                sql += "\nGROUP BY " + ", ".join(ref("b", columns_by_alias[key]) for key in step["group_by"])
        elif operation == "ratio":
            require(step["missing_numerator"] == "zero_when_complete" and step["valid_denominator"] == "positive"
                    and step["require_complete"] is True, "不支持的比率规则")
            key, time_key = step["join_key"], step["date_field"]
            sql = (
                f"SELECT {ref('s', key)} AS {identifier(key)}, `s`.`name`,\n"
                "  COALESCE(`n`.`value`, 0) AS `numerator`, `d`.`value` AS `denominator`,\n"
                "  CASE WHEN `d`.`complete` = 1 AND `d`.`value` > 0\n"
                "       THEN COALESCE(`n`.`value`, 0) / `d`.`value` ELSE NULL END AS `ratio`,\n"
                f"  CASE WHEN {ref('d', key)} IS NULL THEN 'MISSING_VALUATION'\n"
                "       WHEN `d`.`complete` IS NULL OR `d`.`complete` <> 1 THEN 'INCOMPLETE_HOLDINGS'\n"
                "       WHEN `d`.`value` IS NULL THEN 'MISSING_NAV'\n"
                "       WHEN `d`.`value` <= 0 THEN 'NON_POSITIVE_NAV'\n"
                "       ELSE 'OK' END AS `quality_status`\n"
                f"FROM {identifier(step['scope'])} AS `s`\n"
                f"LEFT JOIN {identifier(step['denominator'])} AS `d` ON {ref('s', key)} = {ref('d', key)}\n"
                f"LEFT JOIN {identifier(step['numerator'])} AS `n` ON {ref('s', key)} = {ref('n', key)}"
                f" AND {ref('n', time_key)} = {bind(step['as_of_date'])}"
            )
        elif operation == "compare":
            require(step["operator"] in COMPARISONS, "不支持的比较操作")
            require(type(step["threshold"]) in (int, float) and math.isfinite(step["threshold"]), "无效阈值")
            sql = (f"SELECT *, CASE WHEN `quality_status` = 'OK' AND `ratio` {COMPARISONS[step['operator']]} "
                   f"{bind(step['threshold'])} THEN 1 ELSE 0 END AS `is_match`\nFROM {identifier(step['source'])}")
        elif operation == "project_matches":
            require(step["source"] == "evaluated" and set(step["select"]) <= set(SELECT_FIELDS), "非法投影")
            prefix = "WITH\n" + ",\n".join(ctes) + "\n"
            select = ", ".join(identifier(field) for field in step["select"])
            return {"sql": prefix + f"SELECT {select} FROM `evaluated` WHERE `is_match` = 1 ORDER BY {identifier(step['order_by'])}",
                    "parameters": parameters,
                    "audit_sql": prefix + f"SELECT * FROM `evaluated` ORDER BY {identifier(step['order_by'])}"}
        else:
            raise DemoError(f"不支持的计划操作：{operation}")
        ctes.append(f"{identifier(step['id'])} AS (\n{sql}\n)")
    raise DemoError("计划缺少结果操作")


def run_query(compiled, connection):
    """Results and audit use one read-only consistent database snapshot."""
    import pymysql.cursors

    with connection.cursor(pymysql.cursors.DictCursor) as cursor:
        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
        try:
            cursor.execute(compiled["sql"], compiled["parameters"])
            rows = list(cursor.fetchall())
            cursor.execute(compiled["audit_sql"], compiled["parameters"])
            audit_rows = list(cursor.fetchall())
        finally:
            connection.rollback()
    return {"rows": rows, "audit_rows": audit_rows}
