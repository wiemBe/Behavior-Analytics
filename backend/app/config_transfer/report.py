"""Import decisions and canonical JSON (CFG-1, D-78)."""

from dataclasses import dataclass, field
from typing import Any

from app.config_transfer.document import SECTION_ORDER, dumps

OUTCOMES = ("created", "unchanged", "conflict", "unresolved")


@dataclass
class ImportReport:
    mode: str
    instance: str = ""
    committed: bool = False
    sections: dict[str, Any] = field(default_factory=lambda: {
        name: {outcome: [] for outcome in OUTCOMES} for name in SECTION_ORDER
    })

    def add(self, section: str, outcome: str, key: str, reason: str,
            local_id: str | None = None) -> None:
        line = {"key": key, "reason": reason}
        if local_id is not None:
            line["local_id"] = local_id
        self.sections[section][outcome].append(line)

    def counts(self) -> dict[str, int]:
        return {outcome: sum(len(rows[outcome]) for rows in self.sections.values())
                for outcome in OUTCOMES}

    def render(self) -> str:
        return dumps({"schema_version": 1, "mode": self.mode, "committed": self.committed,
                      "instance": self.instance, "sections": self.sections})
