"""Built-in portable log-source classification rules.

Catch-all DSMs are intentionally absent. Their type does not identify the
product sending through them, so the source name or identifier must decide.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.models.enums import ClassificationRuleKind, SourceClass


@dataclass(frozen=True)
class SeedRule:
    id: uuid.UUID
    kind: ClassificationRuleKind
    pattern: str
    target_class: SourceClass
    priority: int
    description: str


def _rule(
    suffix: int,
    kind: ClassificationRuleKind,
    pattern: str,
    target_class: SourceClass,
    description: str,
) -> SeedRule:
    return SeedRule(
        id=uuid.UUID(f"c1000000-0000-0000-0000-{suffix:012d}"),
        kind=kind,
        pattern=pattern,
        target_class=target_class,
        priority=100,
        description=description,
    )


TYPE_NAME_RULES: tuple[SeedRule, ...] = (
    _rule(1, ClassificationRuleKind.TYPE_NAME_REGEX,
          r"\b(?:F5 Networks BIG-IP ASM|Imperva SecureSphere|Fortinet FortiWeb|"
          r"Barracuda Web Application Firewall|ModSecurity)\b",
          SourceClass.WAF, "Well-known web application firewall DSMs"),
    _rule(2, ClassificationRuleKind.TYPE_NAME_REGEX,
          r"\b(?:Snort Open Source IDS|Suricata|McAfee Network Security Platform|"
          r"TippingPoint|Firepower IPS)\b",
          SourceClass.IPS, "Well-known intrusion prevention DSMs"),
    _rule(3, ClassificationRuleKind.TYPE_NAME_REGEX,
          r"\b(?:Linux iptables Firewall|Netgate pfSense|Fortinet FortiGate Security Gateway|"
          r"Palo Alto PA Series|Check Point|Cisco ASA|Cisco Firepower Threat Defense|"
          r"Juniper SRX)\b",
          SourceClass.FIREWALL, "Well-known firewall DSMs"),
    _rule(4, ClassificationRuleKind.TYPE_NAME_REGEX,
          r"\b(?:Squid Web Proxy|Blue Coat|Zscaler)\b",
          SourceClass.PROXY, "Well-known proxy DSMs"),
    _rule(5, ClassificationRuleKind.TYPE_NAME_REGEX,
          r"\b(?:F5 Networks FirePass|Pulse Secure|GlobalProtect)\b",
          SourceClass.VPN, "Well-known VPN DSMs"),
    _rule(6, ClassificationRuleKind.TYPE_NAME_REGEX,
          r"\b(?:Linux OS|Red Hat Enterprise Linux|Ubuntu Linux)\b",
          SourceClass.LINUX, "Well-known Linux DSMs"),
    _rule(7, ClassificationRuleKind.TYPE_NAME_REGEX,
          r"\bMicrosoft Windows Security Event Log\b",
          SourceClass.WINDOWS, "Microsoft Windows event DSM"),
    _rule(8, ClassificationRuleKind.TYPE_NAME_REGEX, r"\b(?:DNS|Infoblox NIOS)\b",
          SourceClass.DNS, "DNS DSMs"),
    _rule(9, ClassificationRuleKind.TYPE_NAME_REGEX,
          r"\b(?:Oracle Database|Microsoft SQL Server|MySQL|PostgreSQL|IBM Db2)\b",
          SourceClass.DATABASE, "Common database DSMs"),
    _rule(10, ClassificationRuleKind.TYPE_NAME_REGEX,
          r"\b(?:Microsoft Exchange|Proofpoint|Postfix|Sendmail)\b",
          SourceClass.EMAIL, "Common email DSMs"),
    _rule(11, ClassificationRuleKind.TYPE_NAME_REGEX,
          r"\b(?:Okta|Microsoft Azure Active Directory|CyberArk|IBM Security Verify)\b",
          SourceClass.IDENTITY, "Common identity-provider DSMs"),
    _rule(12, ClassificationRuleKind.TYPE_NAME_REGEX,
          r"\b(?:AWS CloudTrail|Microsoft Azure Platform|Google Cloud Platform)\b",
          SourceClass.CLOUD, "Common cloud-platform DSMs"),
    _rule(13, ClassificationRuleKind.TYPE_NAME_REGEX,
          r"\b(?:CrowdStrike Falcon|Microsoft Defender for Endpoint|"
          r"Symantec Endpoint Protection|McAfee ePolicy Orchestrator)\b",
          SourceClass.ENDPOINT, "Common endpoint-security DSMs"),
)

NAME_RULES: tuple[SeedRule, ...] = tuple(
    _rule(100 + index, ClassificationRuleKind.NAME_REGEX, pattern, target, description)
    for index, (pattern, target, description) in enumerate(
        (
            (r"\bwaf\b", SourceClass.WAF, "Site name token: waf"),
            (r"\b(?:ips|ids)\b", SourceClass.IPS, "Site name token: ips or ids"),
            (r"\b(?:fw|firewall)\b", SourceClass.FIREWALL,
             "Site name token: fw or firewall"),
            (r"\blinux\b", SourceClass.LINUX, "Site name token: linux"),
            (r"\bwindows\b", SourceClass.WINDOWS, "Site name token: windows"),
            (r"\bproxy\b", SourceClass.PROXY, "Site name token: proxy"),
            (r"\bvpn\b", SourceClass.VPN, "Site name token: vpn"),
            (r"\bdns\b", SourceClass.DNS, "Site name token: dns"),
        )
    )
)

SEED_RULES: tuple[SeedRule, ...] = TYPE_NAME_RULES + NAME_RULES
