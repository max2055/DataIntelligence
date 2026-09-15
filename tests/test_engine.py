"""Behavior checks for the semantic model and optional seeded MySQL demo.

Run unit checks with ``python -m unittest discover -s tests -v``.
Add ``RUN_MYSQL_TESTS=1`` to check the actual seeded database as well.
"""

import copy
import os
import unittest
from decimal import Decimal

from data_intelligence.engine import (
    DemoError,
    build_plan,
    compile_plan,
    load_model,
    parse_question,
    run_query,
)


QUESTION = "2026年9月14日，信用债占净资产比例超过10%的固收组合有哪些？"


class SemanticCompilerTests(unittest.TestCase):
    def setUp(self):
        self.model = load_model()
        self.intent = parse_question(QUESTION, self.model)

    def compile(self, intent=None, model=None, groups=("FI_TEAM",)):
        return compile_plan(build_plan(
            self.intent if intent is None else intent,
            self.model if model is None else model,
            principal_groups=groups,
        ))

    def test_question_binds_date_metric_and_strict_threshold(self):
        self.assertEqual(self.intent["as_of_date"], "2026-09-14")
        self.assertEqual(self.intent["metric"], "credit_bond_nav_ratio")
        self.assertEqual(self.intent["scope"], "fixed_income")
        self.assertEqual(self.intent["operator"], "gt")
        self.assertEqual(Decimal(str(self.intent["threshold"])), Decimal("0.10"))

    def test_question_parser_rejects_unimplemented_business_questions(self):
        for question in (
            "2026年9月14日，收益率超过10%的固收组合有哪些？",
            "2026年9月14日，信用债占净资产比例超过10%的子组合有哪些？",
        ):
            with self.subTest(question=question), self.assertRaises(DemoError):
                parse_question(question, self.model)

    def test_zero_and_above_one_thresholds_are_supported(self):
        # Leverage can make a market-value / NAV ratio exceed 100%.
        for threshold in (0, 1.25):
            with self.subTest(threshold=threshold):
                intent = dict(self.intent, threshold=threshold)
                self.compile(intent)

    def test_non_finite_boolean_and_negative_thresholds_are_rejected(self):
        for threshold in (True, False, float("nan"), float("inf"), -0.01):
            with self.subTest(threshold=threshold), self.assertRaises(DemoError):
                self.compile(dict(self.intent, threshold=threshold))

    def test_unregistered_metric_scope_object_and_dimension_are_rejected(self):
        variants = (
            dict(self.intent, metric="portfolio_yield"),
            dict(self.intent, scope="all_portfolios"),
            dict(self.intent, object="subportfolio"),
            dict(self.intent, select=["portfolio_id", "issuer_rating"]),
        )
        for intent in variants:
            with self.subTest(intent=intent), self.assertRaises(DemoError):
                self.compile(intent)

    def test_query_intent_cannot_override_execution_permissions(self):
        for key, value in (
            ("principal_groups", ["RESTRICTED"]),
            ("access_group", "RESTRICTED"),
            ("permissions", "all"),
            ("where", "1=1"),
        ):
            with self.subTest(key=key), self.assertRaises(DemoError):
                self.compile(dict(self.intent, **{key: value}))

    def test_compilation_is_deterministic(self):
        first = self.compile()
        second = self.compile(copy.deepcopy(self.intent), copy.deepcopy(self.model))
        self.assertEqual(first, second)
        self.assertIn("sql", first)
        self.assertIn("audit_sql", first)
        self.assertIsInstance(first["parameters"], dict)

    def test_values_are_bound_as_parameters(self):
        # A permission value is data even when it resembles SQL syntax.
        group = "FI_TEAM' OR 1=1 --"
        compiled = self.compile(groups=(group,))
        self.assertNotIn(group, compiled["sql"])
        self.assertNotIn(group, compiled["audit_sql"])
        self.assertIn(group, compiled["parameters"].values())
        self.assertNotIn("2026-09-14", compiled["sql"])
        self.assertIn("2026-09-14", [str(v) for v in compiled["parameters"].values()])

    def test_untrusted_source_identifiers_are_rejected(self):
        for identifier in ("holdings; DROP TABLE portfolios", "holdings --", "a`b"):
            model = copy.deepcopy(self.model)
            model["sources"]["holding"]["table"] = identifier
            with self.subTest(identifier=identifier), self.assertRaises(DemoError):
                self.compile(model=model)

    def test_changing_model_classification_changes_compiled_filter(self):
        original = self.compile()
        model = copy.deepcopy(self.model)
        model["measures"]["credit_bond_market_value"]["filters"][0]["value"] = "RATE_BOND"
        changed = self.compile(model=model)
        self.assertIn("CREDIT_BOND", original["parameters"].values())
        self.assertIn("RATE_BOND", changed["parameters"].values())
        self.assertNotIn("CREDIT_BOND", changed["parameters"].values())


@unittest.skipUnless(os.environ.get("RUN_MYSQL_TESTS") == "1", "set RUN_MYSQL_TESTS=1 for seeded MySQL checks")
class MySQLSemanticIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import pymysql

        cls.connection = pymysql.connect(
            host=os.environ.get("MYSQL_HOST", "127.0.0.1"),
            port=int(os.environ.get("MYSQL_PORT", os.environ.get("DEMO_MYSQL_PORT", "13316"))),
            user=os.environ.get("MYSQL_USER", "demo"),
            password=os.environ.get("MYSQL_PASSWORD", "demo-local-only"),
            database=os.environ.get("MYSQL_DATABASE", "ontology_demo"),
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls.connection.close()

    def query(self, date="2026-09-14", threshold=0.10, model=None, operator="gt", groups=("FI_TEAM",)):
        model = load_model() if model is None else model
        intent = parse_question(QUESTION, model)
        intent.update(as_of_date=date, threshold=threshold, operator=operator)
        compiled = compile_plan(build_plan(intent, model, principal_groups=groups))
        return run_query(compiled, self.connection)

    def test_default_question_returns_only_the_two_expected_portfolios(self):
        rows = self.query()["rows"]
        actual = {r["portfolio_id"]: Decimal(str(r["ratio"])) for r in rows}
        self.assertEqual(actual, {"P001": Decimal("0.12"), "P004": Decimal("0.25")})

    def test_multiple_accounts_aggregate_numerator_without_multiplying_nav(self):
        rows = {r["portfolio_id"]: r for r in self.query()["rows"]}
        self.assertEqual(Decimal(str(rows["P001"]["numerator"])), Decimal("120"))
        self.assertEqual(Decimal(str(rows["P001"]["denominator"])), Decimal("1000"))
        self.assertEqual(Decimal(str(rows["P004"]["numerator"])), Decimal("250"))
        self.assertEqual(Decimal(str(rows["P004"]["denominator"])), Decimal("1000"))

    def test_audit_keeps_boundary_zero_and_invalid_rows_inside_authorized_scope(self):
        audit = {r["portfolio_id"]: r for r in self.query()["audit_rows"]}
        expected_ids = {f"P{i:03}" for i in range(1, 10)} | {"P012"}
        self.assertEqual(set(audit), expected_ids)
        for portfolio_id, expected_ratio in (("P002", "0.08"), ("P003", "0.10"), ("P005", "0")):
            with self.subTest(portfolio_id=portfolio_id):
                self.assertEqual(Decimal(str(audit[portfolio_id]["ratio"])), Decimal(expected_ratio))
        for portfolio_id in ("P006", "P007", "P008", "P009", "P012"):
            with self.subTest(portfolio_id=portfolio_id):
                self.assertIsNone(audit[portfolio_id]["ratio"])

    def test_earlier_date_does_not_use_latest_holdings_or_nav(self):
        result = self.query(date="2026-09-11")
        self.assertEqual(result["rows"], [])
        audit = {r["portfolio_id"]: r for r in result["audit_rows"]}
        self.assertEqual(Decimal(str(audit["P001"]["ratio"])), Decimal("0.09"))
        self.assertEqual(Decimal(str(audit["P004"]["ratio"])), Decimal("0.05"))

    def test_missing_valuation_preserves_observed_numerator_but_never_matches(self):
        audit = {r["portfolio_id"]: r for r in self.query()["audit_rows"]}
        self.assertEqual(audit["P012"]["numerator"], Decimal("500"))
        self.assertEqual(audit["P012"]["quality_status"], "MISSING_VALUATION")
        self.assertIsNone(audit["P012"]["ratio"])
        self.assertEqual(audit["P012"]["is_match"], 0)

    def test_inclusive_threshold_includes_exactly_ten_percent(self):
        self.assertEqual([r["portfolio_id"] for r in self.query(operator="gte")["rows"]],
                         ["P001", "P003", "P004"])

    def test_empty_or_sql_like_groups_cannot_access_results_or_audit(self):
        for groups in ((), ("FI_TEAM' OR 1=1 --",)):
            with self.subTest(groups=groups):
                self.assertEqual(self.query(groups=groups), {"rows": [], "audit_rows": []})

    def test_model_change_is_executed_without_a_python_code_change(self):
        model = load_model()
        model["measures"]["credit_bond_market_value"]["filters"][0]["value"] = "RATE_BOND"
        result = self.query(model=model)
        actual = {r["portfolio_id"]: Decimal(str(r["ratio"])) for r in result["rows"]}
        self.assertEqual(actual, {
            "P001": Decimal("0.88"), "P002": Decimal("0.92"), "P003": Decimal("0.90"),
            "P004": Decimal("0.75"), "P005": Decimal("1.00"),
        })


if __name__ == "__main__":
    unittest.main()
