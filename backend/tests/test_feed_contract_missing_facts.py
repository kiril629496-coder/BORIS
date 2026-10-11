"""Offline category-contract regressions; no DB, provider, or publication calls."""

import copy
import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "tested_feed_contract", ROOT / "app/api/feed_contract.py"
)
contract_api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(contract_api)


def contract(fields):
    return {
        "ok": True,
        "template_id": "synthetic-category",
        "contract_version": "test-version",
        "fields": fields,
    }


def validate(fields, params=None, defaults=None):
    return contract_api.validate_feed_item(
        {"id": "synthetic-item", "params": params or {}}, contract(fields), defaults
    )


def codes(result):
    return [(error["field"], error["code"]) for error in result["errors"]]


class MissingInputContractTests(unittest.TestCase):
    def test_all_empty_required_values_need_input(self):
        for value in (None, "", " \t\n", [], (), set(), {}):
            with self.subTest(value=value):
                result = validate({"Width": {"required": True}}, {"Width": value})
                self.assertFalse(result["valid"])
                self.assertEqual(codes(result), [("Width", "required_field_needs_input")])
                self.assertNotIn("Width", result["resolved_params"])

    def test_false_and_zero_are_explicit_valid_enum_values(self):
        result = validate(
            {
                "Mirror": {"required": True, "allowed": [False, True]},
                "Count": {"required": True, "allowed": [0, 1]},
            },
            {"Mirror": False, "Count": 0},
        )
        self.assertTrue(result["valid"])
        self.assertIs(result["resolved_params"]["Mirror"], False)
        self.assertEqual(result["resolved_params"]["Count"], 0)
        self.assertEqual(len(result["filled"]), 2)

    def test_false_and_zero_constants_are_filled(self):
        result = validate(
            {
                "Mirror": {"required": True, "allowed": [False]},
                "Count": {"required": True, "allowed": [0]},
            },
            {"Mirror": " ", "Count": None},
        )
        self.assertTrue(result["valid"])
        self.assertEqual(result["resolved_params"], {"Mirror": False, "Count": 0})
        self.assertTrue(all(row["source"] == "template_constant" for row in result["filled"]))

    def test_unknown_empty_key_still_fails_strict_parameter_guard(self):
        result = validate({}, {"InventedField": ""})
        self.assertEqual(codes(result), [("InventedField", "unknown_category_parameter")])

    def test_optional_empty_input_is_omitted(self):
        result = validate({"OptionalWidth": {"required": False}}, {"OptionalWidth": " "})
        self.assertTrue(result["valid"])
        self.assertEqual(result["resolved_params"], {})
        self.assertEqual(result["filled"], [])

    def test_multivalue_enum_still_rejects_invalid_member(self):
        result = validate(
            {"Options": {"required": True, "allowed": ["one", "two"], "multiple": True}},
            {"Options": ["one", "invented"]},
        )
        self.assertEqual(codes(result), [("Options", "enum_value_not_allowed")])

    def test_conditional_required_field_retains_dependency(self):
        fields = {
            "Type": {"required": True, "allowed": ["custom", "standard"]},
            "Width": {
                "required": True,
                "depends_on": "Обязательно, если в поле Type указано значение 'custom'",
            },
        }
        active = validate(fields, {"Type": "custom", "Width": " "})
        inactive = validate(fields, {"Type": "standard", "Width": " "})
        self.assertEqual(codes(active), [("Width", "required_field_needs_input")])
        self.assertTrue(inactive["valid"])

    def test_explicit_non_applicability_does_not_create_missing_fact(self):
        fields = {
            "Type": {"required": True, "allowed": ["custom", "standard"]},
            "Width": {
                "required": True,
                "depends_on": "Не применимо, если в поле Type указано значение 'standard'",
            },
        }
        self.assertTrue(validate(fields, {"Type": "standard", "Width": None})["valid"])
        self.assertEqual(
            codes(validate(fields, {"Type": "custom"})),
            [("Width", "required_field_needs_input")],
        )

    def test_template_constants_are_seeded_before_dependency_checks(self):
        fields = {
            "Width": {
                "required": True,
                "depends_on": "Обязательно, если в поле Type указано значение 'custom'",
            },
            "Type": {"required": True, "allowed": ["custom"]},
        }
        self.assertEqual(codes(validate(fields)), [("Width", "required_field_needs_input")])

    def test_false_and_zero_parent_values_count_as_filled_for_dependency(self):
        fields = {
            "Parent": {"required": True, "allowed": [False, 0, 1, True]},
            "Detail": {"required": True, "depends_on": "Обязательно, если поле Parent заполнено"},
        }
        for value in (False, 0):
            with self.subTest(value=value):
                self.assertEqual(codes(validate(fields, {"Parent": value})), [("Detail", "required_field_needs_input")])

    def test_zero_parent_value_matches_explicit_dependency_value(self):
        fields = {
            "Count": {"required": True, "allowed": [0, 1]},
            "Reason": {"required": True, "depends_on": "Обязательно, если в поле Count указано значение '0'"},
        }
        self.assertEqual(codes(validate(fields, {"Count": 0})), [("Reason", "required_field_needs_input")])
        self.assertTrue(validate(fields, {"Count": 1})["valid"])

    def test_validation_does_not_mutate_original_payload_or_contract(self):
        fields = {"Width": {"required": True}, "Type": {"allowed": ["custom"]}}
        params = {"Width": " "}
        before = copy.deepcopy((fields, params))
        validate(fields, params)
        self.assertEqual((fields, params), before)


class NumericBoundsContractTests(unittest.TestCase):
    def fields(self, rule=None):
        return {"Width": {"required": True, "format_rule": rule or '{"min":500,"max":3000}'}}

    def test_inclusive_template_bounds(self):
        for value in (500, 3000, "500", "3000", "1 000", "1\u00a0000", "1000,5"):
            with self.subTest(value=value):
                self.assertTrue(validate(self.fields(), {"Width": value})["valid"])

    def test_out_of_bounds_or_non_numeric_input_fails(self):
        for value in (499, 3001, "not-a-number", float("nan"), float("inf"), "-Infinity", True, False, [1000]):
            with self.subTest(value=value):
                result = validate(self.fields(), {"Width": value})
                self.assertEqual(codes(result), [("Width", "format_mismatch")])

    def test_dict_and_json_rules_have_same_behavior(self):
        rule = {"min": 0, "max": 2}
        for representation in (rule, json.dumps(rule)):
            for value, expected in ((0, True), (2, True), (-1, False), (3, False)):
                with self.subTest(rule=representation, value=value):
                    self.assertEqual(validate(self.fields(representation), {"Width": value})["valid"], expected)

    def test_empty_numeric_value_is_missing_input_not_format_error(self):
        self.assertEqual(
            codes(validate(self.fields(), {"Width": " "})),
            [("Width", "required_field_needs_input")],
        )

    def test_partial_numeric_bounds_are_supported(self):
        self.assertFalse(validate(self.fields({"min": 500}), {"Width": 499})["valid"])
        self.assertTrue(validate(self.fields({"min": 500}), {"Width": 99999})["valid"])
        self.assertFalse(validate(self.fields({"max": 3000}), {"Width": 3001})["valid"])

    def test_non_finite_or_invalid_template_bound_fails_closed(self):
        for bound in ("NaN", "Infinity", "not-a-number", True):
            with self.subTest(bound=bound):
                self.assertFalse(validate(self.fields({"min": bound}), {"Width": 1000})["valid"])

    def test_legacy_text_number_and_integer_rules_remain_supported(self):
        self.assertTrue(validate(self.fields("целое число"), {"Width": "1000"})["valid"])
        self.assertFalse(validate(self.fields("целое число"), {"Width": "1000.5"})["valid"])
        self.assertTrue(validate(self.fields("number"), {"Width": "1000.5"})["valid"])
        self.assertFalse(validate(self.fields("number"), {"Width": "unknown"})["valid"])

    def test_declared_json_integer_type_is_not_weakened_by_bounds(self):
        fields = self.fields('{"type":"integer","min":500,"max":3000}')
        self.assertTrue(validate(fields, {"Width": "1000"})["valid"])
        self.assertFalse(validate(fields, {"Width": "1000.5"})["valid"])

    def test_no_format_rule_does_not_invent_one(self):
        self.assertTrue(validate({"Custom": {"required": True}}, {"Custom": "по заказу"})["valid"])

    def test_invalid_numeric_seller_default_cannot_bypass_validation(self):
        for value in (99999, "not-a-number", True):
            with self.subTest(value=value):
                self.assertEqual(codes(validate(self.fields(), {}, {"Width": value})), [("Width", "format_mismatch")])

    def test_empty_seller_default_does_not_satisfy_required_field(self):
        self.assertEqual(
            codes(validate(self.fields(), {}, {"Width": " "})),
            [("Width", "required_field_needs_input")],
        )

    def test_valid_numeric_seller_default_stays_usable(self):
        result = validate(self.fields({"min": 0, "max": 3}), {}, {"Width": 0})
        self.assertTrue(result["valid"])
        self.assertEqual(result["resolved_params"]["Width"], 0)
        self.assertEqual(result["filled"][0]["source"], "seller_settings")

    def test_false_enum_seller_default_stays_usable(self):
        result = validate({"Mirror": {"required": True, "allowed": [False, True]}}, {}, {"Mirror": False})
        self.assertTrue(result["valid"])
        self.assertIs(result["resolved_params"]["Mirror"], False)

    def test_blank_dimensions_do_not_hide_behind_valid_other_facts(self):
        fields = {
            "Purpose": {"required": True, "allowed": ["Дом", "Квартира"]},
            "Mirror": {"required": True, "allowed": ["Есть", "Нет"]},
            "ThermalRupture": {"required": True, "allowed": ["Есть", "Нет"]},
            "Shape": {"required": True, "allowed": ["Левая", "Правая"]},
            "Width": {"required": True, "format_rule": '{"min":500,"max":3000}'},
            "Height": {"required": True, "format_rule": '{"min":500,"max":3000}'},
        }
        params = {"Purpose": "Дом", "Mirror": "Нет", "ThermalRupture": "Есть", "Shape": "Левая", "Width": "", "Height": " "}
        self.assertEqual(codes(validate(fields, params)), [("Width", "required_field_needs_input"), ("Height", "required_field_needs_input")])
        self.assertEqual(len(validate(fields)["errors"]), 6)


if __name__ == "__main__":
    unittest.main()
