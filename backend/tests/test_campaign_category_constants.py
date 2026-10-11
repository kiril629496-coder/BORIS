"""Official category constants: no fabricated values or hidden required questions."""
import ast
from pathlib import Path


def contract_functions():
    source = Path(__file__).resolve().parents[1] / 'app/api/feed_contract.py'
    tree = ast.parse(source.read_text())
    names = {'resolve_applicable_params', 'validate_feed_item', '_applicable_now', '_required_now', '_fmt_ok', '_norm_dep', '_dep_clause_matches'}
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    scope = {'SYSTEM_FIELDS': {'AvitoId', 'AuctionPrice', 'AuctionPriceLastDate'}, 'SRC_CONST': 'constant', 'SRC_DRAFT': 'draft', 'SRC_SELLER': 'seller', 'CODE_ENUM_NOT_ALLOWED': 'enum', 'CODE_UNKNOWN_PARAM': 'unknown', 'CODE_FORMAT': 'format', 'CODE_NEEDS_INPUT': 'needs_input'}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), scope)
    return scope


def test_required_and_optional_singletons_are_materialized_without_questions():
    f = contract_functions()
    c = {'ok': True, 'fields': {'GoodsType': {'allowed': ['Мебель'], 'required': True}, 'Availability': {'allowed': ['В наличии'], 'required': False}}}
    assert f['resolve_applicable_params']({}, c)['params'] == {'GoodsType': 'Мебель', 'Availability': 'В наличии'}
    result = f['validate_feed_item']({'params': {}}, c)
    assert result['valid']
    assert len(result['filled']) == 2
    assert all(x['source'] == 'constant' for x in result['filled'])


def test_multiple_values_still_require_explicit_choice():
    f = contract_functions()
    c = {'ok': True, 'fields': {'ColorName': {'allowed': ['Белый', 'Чёрный'], 'required': True}}}
    result = f['validate_feed_item']({'params': {}}, c)
    assert not result['valid']
    assert result['errors'][0]['code'] == 'needs_input'
    assert result['resolved_params'] == {}


def test_invalid_explicit_value_is_not_silently_replaced_by_constant():
    f = contract_functions()
    c = {'ok': True, 'fields': {'GoodsType': {'allowed': ['Мебель'], 'required': True}}}
    result = f['validate_feed_item']({'params': {'GoodsType': 'Игрушки'}}, c)
    assert not result['valid']
    assert result['errors'][0]['code'] == 'enum'


def test_service_fields_are_not_materialized():
    f = contract_functions()
    c = {'ok': True, 'fields': {k: {'allowed': ['1'], 'required': True} for k in f['SYSTEM_FIELDS']}}
    assert f['resolve_applicable_params']({}, c)['params'] == {}
    assert f['validate_feed_item']({'params': {}}, c)['resolved_params'] == {}


def test_inapplicable_constant_is_not_materialized():
    f = contract_functions()
    # Exercise the existing applicability boundary without changing its parser.
    f['_applicable_now'] = lambda name, spec, fields, params: name != 'Delivery'
    c = {'ok': True, 'fields': {'Delivery': {'allowed': ['Да'], 'required': True}, 'GoodsType': {'allowed': ['Мебель']}}}
    result = f['resolve_applicable_params']({}, c)
    assert result['params'] == {'GoodsType': 'Мебель'}
    assert result['removed_non_applicable'] == ['Delivery']
