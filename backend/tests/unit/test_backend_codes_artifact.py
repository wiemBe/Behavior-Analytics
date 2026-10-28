"""The committed backend-message frontend artifacts match the registry."""

from __future__ import annotations

from app.i18n.artifact import generated_files, icu_template


def test_backend_message_artifacts_are_current() -> None:
    for path, expected in generated_files().items():
        assert path.read_text() == expected, f"stale generated artifact: {path}"


def test_python_templates_are_escaped_for_icu() -> None:
    assert icu_template("owner's literal {{brace}} and {count}") == (
        "owner''s literal '{'brace'}' and {count}"
    )


def test_write_then_check_round_trips_and_check_fails_on_drift(
    tmp_path, monkeypatch
) -> None:
    """The command the operator runs: --write makes --check pass; an edit makes it exit 1."""
    from app.i18n import artifact

    monkeypatch.setattr(artifact, "ARTIFACT_PATH", tmp_path / "codes.json")
    monkeypatch.setattr(artifact, "ENGLISH_CATALOG_PATH", tmp_path / "en" / "backend.json")

    monkeypatch.setattr("sys.argv", ["artifact", "--check"])
    assert artifact.main() == 1  # nothing written yet

    monkeypatch.setattr("sys.argv", ["artifact", "--write"])
    assert artifact.main() == 0
    monkeypatch.setattr("sys.argv", ["artifact", "--check"])
    assert artifact.main() == 0

    (tmp_path / "en" / "backend.json").write_text("{}\n")
    assert artifact.main() == 1
