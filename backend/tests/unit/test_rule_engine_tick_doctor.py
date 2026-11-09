"""SOC-6i: the doctor reads the rule engine's marker row like any other watermark."""

from app.cli import doctor
from app.models.enums import SearchFamily
from app.rules.engine_tick import EVALUATION_COLLECTOR


def test_the_engine_marker_is_a_known_watermark_governed_by_the_rule_match_switch() -> None:
    assert EVALUATION_COLLECTOR in doctor._WATERMARK_NAMES
    assert doctor._watermark_family(EVALUATION_COLLECTOR) is SearchFamily.RULE_MATCH
    assert doctor._watermark_family("rule_match") is SearchFamily.RULE_MATCH
    assert doctor._watermark_family("rule_metric") is None
