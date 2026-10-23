"""FIX-3: sync CLI _report prints 0 as 0, None as unknown; overview offense fields."""

from __future__ import annotations

from app.cli.sync import _report, main


def test_report_prints_error_and_skipped_reason(capsys) -> None:
    """_report: error message and skipped_reason are printed (not dropped)."""
    result = {
        "status": "ok",
        "results": [
            {
                "instance": "i",
                "status": "ok",
                "processed": 5,
                "error": "connection refused",
                "skipped_reason": "locked by other worker",
            }
        ],
    }
    _report("x", result)
    out = capsys.readouterr().out
    assert "error=connection refused" in out
    assert "skipped_reason=locked by other worker" in out


def test_report_prints_zero_and_unknown(capsys) -> None:
    """_report: processed=0 stays 0; a None field prints as x=unknown."""
    result = {
        "status": "ok",
        "results": [
            {
                "instance": "i",
                "status": "ok",
                "processed": 0,
                "unknown_count": None,
            }
        ],
    }
    _report("sync", result)
    out = capsys.readouterr().out
    assert "processed=0" in out
    assert "unknown_count=unknown" in out


def test_sync_cli_main_with_stubbed_command(capsys, monkeypatch) -> None:
    """Operator path: main() dispatches to stub, prints line, exits 0."""
    import app.cli.sync
    original_commands = app.cli.sync.COMMANDS
    async def stub_cmd(*_a, **_kw):
        return {
            "status": "ok",
            "results": [
                {
                    "instance": "t",
                    "status": "ok",
                    "processed": 0,
                    "missing": None,
                    "error": "fail",
                }
            ],
        }

    monkeypatch.setattr(
        app.cli.sync,
        "COMMANDS",
        {**original_commands, "stub": stub_cmd},
    )
    exit_code = main(["stub"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "processed=0" in out
    assert "missing=unknown" in out
    assert "error=fail" in out



