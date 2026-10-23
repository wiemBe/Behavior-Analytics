"""Keep every Ariel create inside a lifecycle owner protected by a permit."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

APP_ROOT = Path(__file__).parents[2] / "app"
DESIGN = "docs/ARIEL-PERMIT.md §P.3"

ALLOWED_CALLERS = frozenset(
    {
        ("providers/qradar_rest.py", "QRadarRestProvider.get_log_source_metrics"),
        ("providers/qradar_rest.py", "QRadarRestProvider.get_log_source_metrics_bucketed"),
        ("providers/qradar_rest.py", "QRadarRestProvider.observe_property_rejection"),
        ("providers/qradar_rest.py", "QRadarRestProvider._run_bounded_aggregate"),
        ("services/search_executor.py", "SearchExecutor._run_once"),
    }
)
PERMITTED_PROVIDER_METHODS = frozenset(
    {
        "get_log_source_metrics",
        "get_log_source_metrics_bucketed",
        "observe_property_rejection",
        "_run_bounded_aggregate",
    }
)


@dataclass(frozen=True)
class CallSite:
    file: str
    function: str
    line: int


class _CreateCallVisitor(ast.NodeVisitor):
    def __init__(self, relative_file: str) -> None:
        self.relative_file = relative_file
        self.classes: list[str] = []
        self.functions: list[str] = []
        self.calls: list[CallSite] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.classes.append(node.name)
        self.generic_visit(node)
        self.classes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.functions.append(node.name)
        self.generic_visit(node)
        self.functions.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.functions.append(node.name)
        self.generic_visit(node)
        self.functions.pop()

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute) and node.func.attr == "create_ariel_search":
            names = [*self.classes, *self.functions]
            self.calls.append(
                CallSite(self.relative_file, ".".join(names) or "<module>", node.lineno)
            )
        self.generic_visit(node)


def find_create_call_sites(app_root: Path) -> list[CallSite]:
    calls: list[CallSite] = []
    for path in sorted(app_root.rglob("*.py")):
        relative = path.relative_to(app_root).as_posix()
        visitor = _CreateCallVisitor(relative)
        visitor.visit(ast.parse(path.read_text(), filename=str(path)))
        calls.extend(visitor.calls)
    return calls


def _has_permit_decorator(method: ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(decorator, ast.Name) and decorator.id == "_holds_ariel_permit"
        for decorator in method.decorator_list
    )


def assert_create_call_sites_are_permitted(app_root: Path) -> None:
    calls = find_create_call_sites(app_root)
    violations = [
        call for call in calls if (call.file, call.function) not in ALLOWED_CALLERS
    ]

    assert not violations, "\n".join(
        f"{call.file}:{call.line} in {call.function} bypasses the lifecycle permit; "
        f"see {DESIGN}"
        for call in violations
    )
    assert {(call.file, call.function) for call in calls} == ALLOWED_CALLERS


def test_every_ariel_create_call_is_inside_an_approved_lifecycle_owner() -> None:
    assert_create_call_sites_are_permitted(APP_ROOT)


def test_each_provider_lifecycle_owner_is_decorated_with_the_permit() -> None:
    path = APP_ROOT / "providers" / "qradar_rest.py"
    module = ast.parse(path.read_text(), filename=str(path))
    provider = next(
        node
        for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "QRadarRestProvider"
    )
    methods = {
        node.name: node
        for node in provider.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name in PERMITTED_PROVIDER_METHODS
    }

    assert methods.keys() == PERMITTED_PROVIDER_METHODS
    missing = sorted(name for name, method in methods.items() if not _has_permit_decorator(method))
    assert not missing, (
        f"provider lifecycle methods lack the Ariel permit: {', '.join(missing)}; see {DESIGN}"
    )


def test_permit_decorator_holds_the_budget_context_manager() -> None:
    path = APP_ROOT / "providers" / "qradar_rest.py"
    module = ast.parse(path.read_text(), filename=str(path))
    decorator = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "_holds_ariel_permit"
    )
    opens_permit = any(
        isinstance(node, ast.AsyncWith)
        and any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Attribute)
            and item.context_expr.func.attr == "permit"
            for item in node.items
        )
        for node in ast.walk(decorator)
    )

    assert opens_permit, f"_holds_ariel_permit no longer opens permit(); see {DESIGN}"


def test_permitted_provider_methods_never_call_each_other() -> None:
    """A nested lifecycle would take a second permit and count one search twice."""
    path = APP_ROOT / "providers" / "qradar_rest.py"
    module = ast.parse(path.read_text(), filename=str(path))
    provider = next(
        node
        for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "QRadarRestProvider"
    )
    nested = []
    for node in provider.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name in PERMITTED_PROVIDER_METHODS:
            for call in ast.walk(node):
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr in PERMITTED_PROVIDER_METHODS
                ):
                    nested.append(f"{node.name} -> {call.func.attr} (line {call.lineno})")
    assert not nested, f"lifecycle owners call each other: {nested}; see {DESIGN}"
