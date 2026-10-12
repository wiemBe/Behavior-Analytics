"""Pure Phase C fleet-classification contract."""

from __future__ import annotations

import re

import pytest

from app.fleet.classification import (
    GroupFacts,
    RuleFacts,
    SourceFacts,
    TypeFacts,
    classify,
)
from app.fleet.seed_rules import NAME_RULES, TYPE_NAME_RULES
from app.models.enums import ClassificationRuleKind, SourceClass


def rule(
    ident: str,
    kind: ClassificationRuleKind,
    pattern: str,
    target: SourceClass,
    priority: int = 100,
) -> RuleFacts:
    return RuleFacts(ident, kind, pattern, target, priority)


def test_override_beats_internal_group_type_and_name() -> None:
    result = classify(
        SourceFacts(
            name="dmz-waf-01",
            type_id=2,
            identifier="127.0.0.1",
            group_ids=(20,),
            override_class=SourceClass.OTHER,
        ),
        [
            rule("g", ClassificationRuleKind.GROUP, "20", SourceClass.FIREWALL),
            rule("t", ClassificationRuleKind.TYPE, "2", SourceClass.LINUX),
            rule("n", ClassificationRuleKind.NAME_REGEX, r"\bwaf\b", SourceClass.WAF),
        ],
        [TypeFacts(2, "Linux OS", internal=True)],
        [GroupFacts(20, 10)],
    )

    assert result.source_class == SourceClass.OTHER
    assert result.basis == "OVERRIDE"
    assert {item["class"] for item in result.conflicts} == {"FIREWALL", "LINUX", "WAF"}


def test_internal_beats_every_rule_level() -> None:
    result = classify(
        SourceFacts(name="waf", type_id=368, identifier="host", group_ids=(20,)),
        [rule("g", ClassificationRuleKind.GROUP, "20", SourceClass.WAF)],
        [TypeFacts(368, "Health Metrics", internal=True)],
        [GroupFacts(20)],
    )
    assert (result.source_class, result.basis) == (
        SourceClass.QRADAR_INTERNAL,
        "INTERNAL",
    )


def test_loopback_identifier_is_internal_without_type_metadata() -> None:
    result = classify(SourceFacts(name="unknown", identifier="127.0.0.1"), [], [], [])
    assert result.source_class == SourceClass.QRADAR_INTERNAL


def test_group_beats_type_and_name_and_walks_ancestors() -> None:
    result = classify(
        SourceFacts(name="linux-node", type_id=2, group_ids=(20,)),
        [
            rule("ancestor", ClassificationRuleKind.GROUP, "10", SourceClass.WAF),
            rule("type", ClassificationRuleKind.TYPE, "2", SourceClass.LINUX),
            rule("name", ClassificationRuleKind.NAME_REGEX, "linux", SourceClass.LINUX),
        ],
        [TypeFacts(2, "Linux OS")],
        [GroupFacts(20, 10), GroupFacts(10, 0), GroupFacts(0, 10)],
    )
    assert result.source_class == SourceClass.WAF
    assert result.basis == "GROUP"


def test_root_zero_is_matched_only_when_explicitly_assigned() -> None:
    root_rule = rule("root", ClassificationRuleKind.GROUP, "0", SourceClass.NETWORK)
    groups = [GroupFacts(20, 10), GroupFacts(10, 0), GroupFacts(0, 10)]

    nested = classify(SourceFacts(name="nested", group_ids=(20,)), [root_rule], [], groups)
    explicit = classify(SourceFacts(name="other", group_ids=(0,)), [root_rule], [], groups)

    assert nested.source_class == SourceClass.UNCLASSIFIED
    assert explicit.source_class == SourceClass.NETWORK


def test_type_beats_name_and_records_lower_disagreement() -> None:
    result = classify(
        SourceFacts(name="linux-auth-01", type_id=25),
        [
            rule("type", ClassificationRuleKind.TYPE, "25", SourceClass.IPS),
            rule("name", ClassificationRuleKind.NAME_REGEX, r"\blinux\b", SourceClass.LINUX),
        ],
        [TypeFacts(25, "McAfee Network Security Platform")],
        [],
    )
    assert (result.source_class, result.basis) == (SourceClass.IPS, "TYPE")
    assert result.conflicts == (
        {"basis": "NAME_REGEX", "rule_id": "name", "class": "LINUX"},
    )


def test_equal_priority_conflict_falls_through_to_next_level() -> None:
    result = classify(
        SourceFacts(name="proxy-01", group_ids=(20,)),
        [
            rule("a", ClassificationRuleKind.GROUP, "20", SourceClass.WAF),
            rule("b", ClassificationRuleKind.GROUP, "20", SourceClass.IPS),
            rule("name", ClassificationRuleKind.NAME_REGEX, r"\bproxy\b", SourceClass.PROXY),
        ],
        [],
        [GroupFacts(20)],
    )
    assert (result.source_class, result.basis) == (SourceClass.PROXY, "NAME_REGEX")
    assert {item["class"] for item in result.conflicts} == {"WAF", "IPS"}


def test_higher_priority_wins_within_one_level() -> None:
    result = classify(
        SourceFacts(name="vpn-proxy"),
        [
            rule("low", ClassificationRuleKind.NAME_REGEX, "proxy", SourceClass.PROXY, 10),
            rule("high", ClassificationRuleKind.NAME_REGEX, "vpn", SourceClass.VPN, 20),
        ],
        [],
        [],
    )
    assert result.source_class == SourceClass.VPN
    assert result.rule_id == "high"


def test_catch_all_type_falls_through_to_name() -> None:
    rules = [
        RuleFacts(item.id, item.kind, item.pattern, item.target_class, item.priority)
        for item in (*TYPE_NAME_RULES, *NAME_RULES)
    ]
    result = classify(
        SourceFacts(name="dmz-waf-01", type_id=212),
        rules,
        [TypeFacts(212, "Universal LEEF")],
        [],
    )
    assert (result.source_class, result.basis) == (SourceClass.WAF, "NAME_REGEX")


def test_name_regex_has_case_insensitive_word_boundaries() -> None:
    ips_rule = next(item for item in NAME_RULES if item.target_class == SourceClass.IPS)
    rules = [RuleFacts(ips_rule.id, ips_rule.kind, ips_rule.pattern, ips_rule.target_class)]

    assert classify(SourceFacts(name="dmz-IPS-01"), rules, [], []).source_class == SourceClass.IPS
    assert classify(SourceFacts(name="lipstick"), rules, [], []).source_class == (
        SourceClass.UNCLASSIFIED
    )


def test_same_inputs_are_deterministic() -> None:
    source = SourceFacts(name="dmz-ips-01", type_id=25)
    rules = [
        rule("name", ClassificationRuleKind.NAME_REGEX, r"\bips\b", SourceClass.IPS),
        rule("type", ClassificationRuleKind.TYPE, "25", SourceClass.IPS),
    ]
    types = [TypeFacts(25, "McAfee Network Security Platform")]
    assert classify(source, rules, types, []) == classify(source, rules, types, [])


REALISTIC_DSM_TYPES = (
    # Every type observed in T-015 section 7.
    ("Snort Open Source IDS", SourceClass.IPS),
    ("Linux OS", SourceClass.LINUX),
    ("Microsoft Windows Security Event Log", SourceClass.WINDOWS),
    ("Linux iptables Firewall", SourceClass.FIREWALL),
    ("Custom Rule Engine", None),
    ("SIM Audit", None),
    ("System Notification", None),
    ("Anomaly Detection Engine", None),
    ("Asset Profiler", None),
    ("Search Results", None),
    ("Health Metrics", None),
    ("SIM Generic Log DSM", None),
    ("McAfee Network Security Platform", SourceClass.IPS),
    ("Squid Web Proxy", SourceClass.PROXY),
    # Multi-role NGFW DSM: the source name decides (see MULTI_ROLE_DSM_CASES).
    ("Fortinet FortiGate Security Gateway", None),
    ("Universal LEEF", None),
    ("F5 Networks BIG-IP ASM", SourceClass.WAF),
    ("F5 Networks FirePass", SourceClass.VPN),
    ("Microsoft DNS Debug", SourceClass.DNS),
    ("Netgate pfSense", SourceClass.FIREWALL),
    # Common QRadar DSMs spanning every built-in class.
    ("Imperva SecureSphere", SourceClass.WAF),
    ("Fortinet FortiWeb", SourceClass.WAF),
    ("Barracuda Web Application Firewall", SourceClass.WAF),
    ("ModSecurity", SourceClass.WAF),
    ("Suricata", SourceClass.IPS),
    ("TippingPoint", SourceClass.IPS),
    ("Firepower IPS", SourceClass.IPS),
    # Multi-role NGFW DSMs: the source name decides (see MULTI_ROLE_DSM_CASES).
    ("Cisco Firepower Threat Defense", None),
    ("Palo Alto PA Series", None),
    ("Check Point Firewall-1", None),
    ("Cisco ASA", SourceClass.FIREWALL),
    ("Juniper SRX Series Services Gateway", None),
    ("Blue Coat ProxySG", SourceClass.PROXY),
    ("Zscaler NSS", SourceClass.PROXY),
    ("Pulse Secure", SourceClass.VPN),
    ("Palo Alto GlobalProtect", SourceClass.VPN),
    ("Red Hat Enterprise Linux", SourceClass.LINUX),
    ("Ubuntu Linux", SourceClass.LINUX),
    ("Infoblox NIOS", SourceClass.DNS),
    ("Oracle Database", SourceClass.DATABASE),
    ("Microsoft SQL Server", SourceClass.DATABASE),
    ("MySQL", SourceClass.DATABASE),
    ("PostgreSQL", SourceClass.DATABASE),
    ("IBM Db2", SourceClass.DATABASE),
    ("Microsoft Exchange Server", SourceClass.EMAIL),
    ("Proofpoint Email Security", SourceClass.EMAIL),
    ("Postfix", SourceClass.EMAIL),
    ("Sendmail", SourceClass.EMAIL),
    ("Okta", SourceClass.IDENTITY),
    ("Microsoft Azure Active Directory", SourceClass.IDENTITY),
    ("CyberArk Privileged Account Security", SourceClass.IDENTITY),
    ("IBM Security Verify", SourceClass.IDENTITY),
    ("AWS CloudTrail", SourceClass.CLOUD),
    ("Microsoft Azure Platform", SourceClass.CLOUD),
    ("Google Cloud Platform", SourceClass.CLOUD),
    ("CrowdStrike Falcon", SourceClass.ENDPOINT),
    ("Microsoft Defender for Endpoint", SourceClass.ENDPOINT),
    ("Symantec Endpoint Protection", SourceClass.ENDPOINT),
    ("McAfee ePolicy Orchestrator", SourceClass.ENDPOINT),
    # The full catch-all set from T-015 section 4 must not have a type rule.
    ("Universal DSM", None),
    ("Universal CEF", None),
    ("IBM Custom DSM", None),
)

# A source name carrying no class token, so the type rule alone must decide.
NEUTRAL_SOURCE_NAME = "source"

# The multi-role NGFW DSMs listed as `None` above under a name that carries a
# class token: their type carries firewall, IPS and threat logs alike, so no
# built-in type rule may claim them and the site's naming convention decides.
MULTI_ROLE_DSM_CASES = (
    ("Fortinet FortiGate Security Gateway", "fgt-fw-01", SourceClass.FIREWALL),
    ("Palo Alto PA Series", "pa-ips-01", SourceClass.IPS),
    ("Check Point Firewall-1", "cp-fw-01", SourceClass.FIREWALL),
    ("Cisco Firepower Threat Defense", "fp-fw-01", SourceClass.FIREWALL),
    ("Juniper SRX Series Services Gateway", "srx-ips-01", SourceClass.IPS),
)

DSM_CASES: tuple[tuple[str, str, SourceClass | None], ...] = (
    *((type_name, NEUTRAL_SOURCE_NAME, expected) for type_name, expected in REALISTIC_DSM_TYPES),
    *MULTI_ROLE_DSM_CASES,
)


@pytest.mark.parametrize(("type_name", "source_name", "expected_class"), DSM_CASES)
def test_seed_type_rules_classify_realistic_dsm_names_without_priority_ties(
    type_name: str, source_name: str, expected_class: SourceClass | None
) -> None:
    # Name rules join the type rules so the matrix pins both directions: a
    # neutral name is decided by the type rule, a class token by the name.
    rules = [
        RuleFacts(item.id, item.kind, item.pattern, item.target_class, item.priority)
        for item in (*TYPE_NAME_RULES, *NAME_RULES)
    ]
    result = classify(
        SourceFacts(name=source_name, type_id=1),
        rules,
        [TypeFacts(1, type_name)],
        [],
    )
    matches = [
        item
        for item in TYPE_NAME_RULES
        if re.search(item.pattern, type_name, flags=re.IGNORECASE)
    ]
    tied_classes = {
        item.target_class
        for item in matches
        if item.priority == max((match.priority for match in matches), default=0)
    }
    # A neutral name must be claimed by the pinned type rule and no other; a
    # class-token name means no built-in type rule may claim the type at all.
    expected_type_classes = (
        {expected_class} if expected_class and source_name == NEUTRAL_SOURCE_NAME else set()
    )

    assert result.source_class == (expected_class or SourceClass.UNCLASSIFIED)
    assert {item.target_class for item in matches} == expected_type_classes
    assert len(tied_classes) <= 1
