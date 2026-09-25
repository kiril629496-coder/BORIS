import ast
from pathlib import Path

ROOT = Path('/root/BORIS/backend/app')
TARGETS = {
    'services/telephony_core.py': ['phone_owner_economics'],
    'services/service_pricing.py': ['list_items'],
    'services/service_marketplace.py': ['list_platforms', 'generate_drafts', 'save_drafts', 'mark_posted', 'registration_plan', 'platform_policy'],
    'services/platform_rules.py': ['inspect_platform'],
    'factory/client_launch_entry.py': ['classify'],
}


def _definitions(tree, name):
    return [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]


class _ImmediateLoadVisitor(ast.NodeVisitor):
    def __init__(self, name):
        self.name = name
        self.uses = []

    def visit_Name(self, node):
        if node.id == self.name and isinstance(node.ctx, ast.Load):
            self.uses.append(getattr(node, 'lineno', None))

    # Function/class bodies execute later and resolve globals at call time; they
    # do not capture the shadowed function merely by being defined between two
    # generations. Defaults/decorators are handled explicitly below.
    def visit_FunctionDef(self, node):
        for deco in node.decorator_list:
            self.visit(deco)
        for default in list(node.args.defaults) + [x for x in node.args.kw_defaults if x is not None]:
            self.visit(default)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        for deco in node.decorator_list:
            self.visit(deco)
        for base in node.bases:
            self.visit(base)
        for kw in node.keywords:
            self.visit(kw.value)


def _uses_name(nodes, name):
    visitor = _ImmediateLoadVisitor(name)
    for node in nodes:
        visitor.visit(node)
    return [x for x in visitor.uses if x]


def test_shadowed_defs_have_no_top_level_binding_between_generations():
    problems = {}
    for rel, names in TARGETS.items():
        path = ROOT / rel
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for name in names:
            defs = _definitions(tree, name)
            for left, right in zip(defs, defs[1:]):
                between = [n for n in tree.body if getattr(n, 'lineno', 0) > left.end_lineno and getattr(n, 'lineno', 0) < right.lineno]
                uses = _uses_name(between, name)
                if uses:
                    problems[f'{rel}:{name}:{left.lineno}->{right.lineno}'] = uses
    assert not problems, f'Top-level code binds/uses a shadowed definition before the next generation: {problems}'
