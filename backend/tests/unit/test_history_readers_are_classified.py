"""Guard every reader of a table that can contain learning-only history (§B.4.7)."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class ReaderKind(StrEnum):
    LIVE_ONLY = "LIVE-only"
    EVIDENCE = "EVIDENCE"
    DISPLAY = "DISPLAY"
    DELETE = "DELETE"


@dataclass(frozen=True)
class Reader:
    reference: str
    kind: ReaderKind
    live_clause_calls: int = 0


@dataclass(frozen=True)
class HistoryTable:
    model: str
    sql_name: str
    readers: dict[str, Reader]


# Registering a table here is an adapter's phase 1: its reader inventory and guards land
# before its history writer. HB-3 through HB-7 extend this tuple without changing the scanner.
HISTORY_TABLES = (
    HistoryTable(
        model="PwBucket",
        sql_name="pw_bucket",
        readers={
            "app.collectors.pw_collector.PwCollector._unparsed_streaks": Reader(
                "R1 (T-138 streak)", ReaderKind.LIVE_ONLY, 1
            ),
            "app.custom_rules.loaders.load_pw_values": Reader(
                "R5", ReaderKind.LIVE_ONLY, 2
            ),
            "app.repositories.property_watch.PropertyWatchRepository.scope_history": Reader(
                "R4", ReaderKind.EVIDENCE
            ),
            "app.repositories.property_watch.PropertyWatchRepository.pending_buckets": Reader(
                "R1", ReaderKind.LIVE_ONLY, 1
            ),
            "app.repositories.property_watch.PropertyWatchRepository.buckets_between": Reader(
                "R2", ReaderKind.EVIDENCE
            ),
            "app.repositories.property_watch.PropertyWatchRepository.component_occurrences": Reader(
                "R3", ReaderKind.EVIDENCE
            ),
            "app.services.property_watches._latest_coverage": Reader(
                "R6", ReaderKind.DISPLAY
            ),
            "app.services.property_watches._scope_changes": Reader("R6", ReaderKind.DISPLAY),
            "app.services.property_watches.list_values": Reader("R6", ReaderKind.DISPLAY),
            "app.services.property_watches.value_series": Reader("R6", ReaderKind.DISPLAY),
            "app.services.property_watches.watch_buckets": Reader("R6", ReaderKind.DISPLAY),
            "app.services.property_watches.watch_aql": Reader("R6", ReaderKind.DISPLAY),
            "app.services.pw_retention.<module>": Reader("R7", ReaderKind.DELETE),
        },
    ),
)


@dataclass(frozen=True)
class FoundReader:
    name: str
    live_clause_calls: int


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _contains_select(node: ast.AST) -> bool:
    return any(
        isinstance(child, ast.Call) and _call_name(child) == "select"
        for child in ast.walk(node)
    )


def _names(node: ast.AST) -> set[str]:
    return {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}


def _live_clause_calls(node: ast.AST, model: str) -> int:
    count = 0
    for child in ast.walk(node):
        if not isinstance(child, ast.Call) or _call_name(child) != "is_live_clause":
            continue
        if len(child.args) == 1 and isinstance(child.args[0], ast.Name):
            count += child.args[0].id == model
    return count


def _raw_sql_mentions(node: ast.AST, table: str) -> bool:
    pattern = re.compile(rf"\b(?:SELECT|DELETE)\b.*\b{re.escape(table)}\b", re.I | re.S)
    return any(
        isinstance(child, ast.Constant)
        and isinstance(child.value, str)
        and pattern.search(child.value) is not None
        for child in ast.walk(node)
    )


class _FunctionCollector(ast.NodeVisitor):
    def __init__(self, module: str, table: HistoryTable) -> None:
        self.module = module
        self.table = table
        self.scope: list[str] = []
        self.readers: list[FoundReader] = []

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
        orm_read = self.table.model in _names(node) and _contains_select(node)
        if orm_read or _raw_sql_mentions(node, self.table.sql_name):
            self.readers.append(
                FoundReader(name, _live_clause_calls(node, self.table.model))
            )
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()


def _module_node_without_functions(tree: ast.Module) -> ast.Module:
    return ast.Module(
        body=[
            node
            for node in tree.body
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ],
        type_ignores=[],
    )


def _find_readers(app: Path, table: HistoryTable) -> dict[str, FoundReader]:
    found: list[FoundReader] = []
    for path in sorted(app.rglob("*.py")):
        module = ".".join(path.relative_to(app.parent).with_suffix("").parts)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        visitor = _FunctionCollector(module, table)
        visitor.visit(tree)
        found.extend(visitor.readers)
        module_body = _module_node_without_functions(tree)
        if _raw_sql_mentions(module_body, table.sql_name):
            found.append(FoundReader(f"{module}.<module>", 0))
    return {reader.name: reader for reader in found}


def test_history_readers_are_classified_and_live_readers_use_the_helper() -> None:
    app = Path(__file__).parents[2] / "app"
    for table in HISTORY_TABLES:
        found = _find_readers(app, table)
        classified = table.readers
        assert found.keys() == classified.keys(), (
            f"{table.sql_name} readers changed; registering a table or reader is the "
            f"adapter's phase 1. unclassified={sorted(found.keys() - classified.keys())}, "
            f"missing={sorted(classified.keys() - found.keys())}"
        )
        for name, reader in classified.items():
            if reader.kind is not ReaderKind.LIVE_ONLY:
                continue
            assert found[name].live_clause_calls >= reader.live_clause_calls, (
                f"{name} ({reader.reference}) is {reader.kind.value} and must use "
                f"is_live_clause({table.model}) {reader.live_clause_calls} time(s)"
            )
