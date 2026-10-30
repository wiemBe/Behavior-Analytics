"""MSG-7b: the engines' alert titles render exactly the f-string each engine built before."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.i18n.messages import REGISTRY, MessageRef, render
from app.models.enums import AnomalyType, IpsAnomalyType, PwAnomalyType, WafAnomalyType

NAMES = [
    "edge-waf",
    "Edge firewall 01",
    "Güvenlik duvar\u0131 \u2013 \u0130stanbul",
    "host: primary",
    "a  b",
    "{braces} %s",
]
ENUMS = Path(__file__).resolve().parents[3] / "frontend" / "messages" / "en" / "enums.json"


@pytest.mark.parametrize("anomaly_type", list(AnomalyType))
@pytest.mark.parametrize("name", NAMES)
def test_phase_a_title(anomaly_type: AnomalyType, name: str) -> None:
    ref = MessageRef(
        "alert.anomaly.title", {"anomaly_type": anomaly_type.value, "log_source": name}
    )
    assert render(ref) == f"{anomaly_type.value} on {name}"


@pytest.mark.parametrize("detector", list(WafAnomalyType))
@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize(
    ("method", "endpoint"),
    [("GET", "/"), ("POST", "/login/{id}"), ("PUT", "/ü/ş: x"), ("GET", "/a b")],
)
def test_waf_title(detector: WafAnomalyType, name: str, method: str, endpoint: str) -> None:
    ref = MessageRef(
        "alert.waf.title",
        {"detector": detector.value, "log_source": name, "method": method, "endpoint": endpoint},
    )
    assert render(ref) == f"{detector.value} on {name}: {method} {endpoint}"


@pytest.mark.parametrize("detector", list(IpsAnomalyType))
@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("entity", ["3389/tcp", "10.0.0.1", "ET SCAN: x ü", "a  b", "0"])
def test_ips_titles(detector: IpsAnomalyType, name: str, entity: str) -> None:
    base = {"detector": detector.value, "log_source": name}
    assert render(MessageRef("alert.ips.title.source", base)) == f"{detector.value} on {name}"
    for code, words in (
        ("destination_port", "destination port"),
        ("destination_ip", "destination IP"),
        ("signature", "signature"),
    ):
        ref = MessageRef(f"alert.ips.title.{code}", {**base, "entity": entity})
        assert render(ref) == f"{detector.value} on {name}: {words} {entity}"


@pytest.mark.parametrize("detector", list(PwAnomalyType))
@pytest.mark.parametrize("watch", NAMES)
@pytest.mark.parametrize("shown", ["alice", "alice | login", "", "ü | 10.0.0.1: x", "a  b"])
def test_pw_title(detector: PwAnomalyType, watch: str, shown: str) -> None:
    ref = MessageRef("alert.pw.title", {"detector": detector.value, "watch": watch, "value": shown})
    assert render(ref) == f"{detector.value} on {watch}: {shown}"


def test_title_wire_carries_values_verbatim() -> None:
    ref = MessageRef(
        "alert.waf.title",
        {"detector": "WAF_BLOCK_RATIO_SPIKE", "log_source": "ü", "method": "GET", "endpoint": "/x"},
    )
    assert ref.to_wire()["params"] == ref.params


def test_every_engine_detector_type_has_a_label_in_the_group_the_title_names() -> None:
    """The frontend labels the enum param from these groups; a missing value falls back raw."""
    groups = json.loads(ENUMS.read_text(encoding="utf-8"))
    for enum_type in (WafAnomalyType, IpsAnomalyType):
        assert {m.value for m in enum_type} <= set(groups["anomalyType"])
    assert {m.value for m in PwAnomalyType} <= set(groups["propertyWatchAnomalyType"])
    by_code = {spec.code: spec for spec in REGISTRY}
    assert by_code["alert.waf.title"].params["detector"].enum_group == "anomalyType"
    assert by_code["alert.pw.title"].params["detector"].enum_group == "propertyWatchAnomalyType"
