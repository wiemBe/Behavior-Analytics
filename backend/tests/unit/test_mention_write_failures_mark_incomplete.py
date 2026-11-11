"""Every mention-write except marks that source incomplete, except log-source inventory."""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).parents[2] / "app"
SCAN_ROOTS = (APP / "collectors", APP / "services")

# The timeline does not read a LOG_SOURCE backfill row. The next inventory sync
# rewrites those mentions, so this except logs and does not mark the row.
EXEMPT = "app.services.inventory_sync.InventorySyncService._record_mentions"

EXPECTED_WRITERS = {
    "app.collectors.explanation_collector.ExplanationCollector._record_mentions",
    "app.collectors.waf_endpoint_collector.WafEndpointCollector._record_client_mentions",
    "app.collectors.waf_endpoint_collector.WafEndpointCollector._record_history_client_mentions",
    "app.collectors.ips_collector.IpsCollector._record_mentions",
    "app.collectors.ips_collector.IpsCollector._record_history_mentions",
    "app.collectors.offense_collector.OffenseCollector._record_mentions",
    "app.collectors.catchall_collector.CatchallCollector._record_mentions",
    "app.collectors.catchall_collector.CatchallCollector._record_history_mentions",
    "app.collectors.pw_collector.PwCollector._record_mentions",
    EXEMPT,
}


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _calls(node: ast.AST, name: str) -> bool:
    return any(
        isinstance(child, ast.Call) and _call_name(child) == name for child in ast.walk(node)
    )


def _iter_tries(node: ast.AST):
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(child, ast.Try):
            yield child
        yield from _iter_tries(child)


def _try_writes_mentions(node: ast.Try) -> bool:
    for statement in node.body:
        if isinstance(statement, (ast.Try, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if _calls(statement, "record_mentions"):
            return True
    return False


class _WriterCollector(ast.NodeVisitor):
    def __init__(self, module: str) -> None:
        self.module = module
        self.scope: list[str] = []
        self.found: dict[str, list[ast.Try]] = {}

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        name = ".".join((self.module, *self.scope, node.name))
        writes = [tried for tried in _iter_tries(node) if _try_writes_mentions(tried)]
        if writes:
            self.found[name] = writes
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()


def _writers() -> dict[str, list[ast.Try]]:
    found: dict[str, list[ast.Try]] = {}
    for root in SCAN_ROOTS:
        for path in sorted(root.rglob("*.py")):
            module = ".".join(path.relative_to(APP.parent).with_suffix("").parts)
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            visitor = _WriterCollector(module)
            visitor.visit(tree)
            found.update(visitor.found)
    return found


def test_every_mention_write_except_marks_the_index_incomplete() -> None:
    found = _writers()
    assert found.keys() == EXPECTED_WRITERS
    exempt = found.pop(EXEMPT)
    assert exempt
    assert all(not _calls(tried, "mark_mentions_incomplete") for tried in exempt)
    for name, tries in found.items():
        for tried in tries:
            assert tried.handlers, name
            assert all(_calls(handler, "mark_mentions_incomplete") for handler in tried.handlers), (
                name
            )
