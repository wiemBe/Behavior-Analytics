"""IPS source profiles and their strictly validated field references."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.models.enums import (
    IpsLogicalField,
    IpsPreset,
    IpsProfileValidationStatus,
    IpsReferenceKind,
)
from app.models.ips import IpsSourceProfile
from app.providers.base import IPS_NORMALIZED_FIELD_ALLOWLIST, PROPERTY_NAME_PATTERN

NORMALIZED_FIELD_ALLOWLIST = IPS_NORMALIZED_FIELD_ALLOWLIST
_NORMALIZED_FIELDS = frozenset(NORMALIZED_FIELD_ALLOWLIST)
REQUIRED_FIELDS = frozenset(
    {
        IpsLogicalField.SOURCE_IP,
        IpsLogicalField.DESTINATION_IP,
        IpsLogicalField.DESTINATION_PORT,
        IpsLogicalField.SIGNATURE,
    }
)
ACTION_OUTCOMES = ("BLOCKED", "ALLOWED")
ACTION_VALUE_MAX_LENGTH = 64
ACTION_VALUE_MAX_COUNT = 20


class IpsProfileError(ValueError):
    """A profile operation was refused. Its message is safe for an operator."""


@dataclass(frozen=True)
class IpsMappingField:
    key: IpsLogicalField
    label: str
    description: str
    required: bool
    used_by_collection: bool


IPS_MAPPING_FIELDS = (
    IpsMappingField(
        IpsLogicalField.SOURCE_IP,
        "Source IP",
        "The initiating address. Feeds concentration, fan-out and contributor evidence.",
        True,
        True,
    ),
    IpsMappingField(
        IpsLogicalField.DESTINATION_IP,
        "Destination IP",
        "The protected destination address used for destination behavior.",
        True,
        True,
    ),
    IpsMappingField(
        IpsLogicalField.DESTINATION_PORT,
        "Destination port",
        "The protected service port used for port rate, novelty and fan-out behavior.",
        True,
        True,
    ),
    IpsMappingField(
        IpsLogicalField.PROTOCOL,
        "Protocol",
        "Separates services that share a port number, such as TCP and UDP.",
        False,
        True,
    ),
    IpsMappingField(
        IpsLogicalField.SIGNATURE,
        "Signature",
        "The IPS signature or QRadar QID used for signature behavior.",
        True,
        True,
    ),
    IpsMappingField(
        IpsLogicalField.ACTION,
        "Action",
        "What the IPS did. Feeds the block ratio through the action value map.",
        False,
        False,
    ),
    IpsMappingField(
        IpsLogicalField.SEVERITY,
        "Severity",
        "The vendor or QRadar severity retained for bounded evidence only.",
        False,
        False,
    ),
)


@dataclass(frozen=True)
class IpsFieldReference:
    kind: IpsReferenceKind
    name: str

    def __post_init__(self) -> None:
        if self.kind is IpsReferenceKind.NORMALIZED:
            if self.name not in _NORMALIZED_FIELDS:
                raise IpsProfileError("normalized field is not in the IPS allowlist")
            return
        if self.kind is IpsReferenceKind.CUSTOM_PROPERTY:
            if not PROPERTY_NAME_PATTERN.fullmatch(self.name):
                raise IpsProfileError(
                    "invalid QRadar property name: letters, digits, underscore, space and "
                    "hyphen only, at most 64 characters"
                )
            return
        raise IpsProfileError("unknown IPS field reference kind")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> IpsFieldReference:
        raw_kind = value.get("kind")
        if not isinstance(raw_kind, str):
            raise IpsProfileError("unknown IPS field reference kind")
        try:
            kind = IpsReferenceKind(raw_kind)
            name = value.get("name")
        except ValueError as exc:
            raise IpsProfileError("unknown IPS field reference kind") from exc
        if not isinstance(name, str):
            raise IpsProfileError("IPS field reference name must be text")
        return cls(kind=kind, name=name)

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind.value, "name": self.name}


def _action_key(value: str) -> str:
    return value.strip().casefold()


def _action_values(values: Iterable[str], outcome: str) -> tuple[str, ...]:
    raw_values = list(values)
    if len(raw_values) > ACTION_VALUE_MAX_COUNT:
        raise IpsProfileError(
            f"{outcome} action values exceed the {ACTION_VALUE_MAX_COUNT}-value limit"
        )
    out: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        value = raw.strip()
        if not (
            0 < len(value) <= ACTION_VALUE_MAX_LENGTH
            and value.isprintable()
        ):
            raise IpsProfileError(
                f"invalid {outcome} action value: use a printable token of at most "
                f"{ACTION_VALUE_MAX_LENGTH} characters"
            )
        key = _action_key(value)
        if key not in seen:
            seen.add(key)
            out.append(value)
    return tuple(sorted(out, key=_action_key))


@dataclass(frozen=True)
class IpsFieldMapping:
    source_ip: IpsFieldReference
    destination_ip: IpsFieldReference
    destination_port: IpsFieldReference
    signature: IpsFieldReference
    protocol: IpsFieldReference | None = None
    action: IpsFieldReference | None = None
    severity: IpsFieldReference | None = None
    blocked_actions: tuple[str, ...] = ()
    allowed_actions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        blocked = _action_values(self.blocked_actions, "BLOCKED")
        allowed = _action_values(self.allowed_actions, "ALLOWED")
        object.__setattr__(self, "blocked_actions", blocked)
        object.__setattr__(self, "allowed_actions", allowed)
        overlap = {_action_key(v) for v in blocked} & {_action_key(v) for v in allowed}
        if overlap:
            raise IpsProfileError("an action value is mapped to both BLOCKED and ALLOWED")
        if (blocked or allowed) and self.action is None:
            raise IpsProfileError("an action map needs an ACTION field mapping")

    @classmethod
    def from_profile(cls, profile: IpsSourceProfile) -> IpsFieldMapping:
        raw_mapping = profile.mapping or {}

        def ref(field: IpsLogicalField, *, required: bool = False) -> IpsFieldReference | None:
            raw = raw_mapping.get(field.value)
            if raw is None:
                if required:
                    raise IpsProfileError(f"{field.value} is required")
                return None
            if not isinstance(raw, dict):
                raise IpsProfileError(f"{field.value} must be a field reference")
            return IpsFieldReference.from_dict(raw)

        action_map = profile.action_value_map or {}
        return cls(
            source_ip=ref(IpsLogicalField.SOURCE_IP, required=True),  # type: ignore[arg-type]
            destination_ip=ref(  # type: ignore[arg-type]
                IpsLogicalField.DESTINATION_IP, required=True
            ),
            destination_port=ref(  # type: ignore[arg-type]
                IpsLogicalField.DESTINATION_PORT, required=True
            ),
            protocol=ref(IpsLogicalField.PROTOCOL),
            signature=ref(IpsLogicalField.SIGNATURE, required=True),  # type: ignore[arg-type]
            action=ref(IpsLogicalField.ACTION),
            severity=ref(IpsLogicalField.SEVERITY),
            blocked_actions=tuple(action_map.get("BLOCKED", ())),
            allowed_actions=tuple(action_map.get("ALLOWED", ())),
        )

    def reference_for(self, field: IpsLogicalField) -> IpsFieldReference | None:
        return getattr(self, field.value.lower())

    def mapped(self) -> dict[IpsLogicalField, IpsFieldReference]:
        return {
            field: reference
            for field in IpsLogicalField
            if (reference := self.reference_for(field)) is not None
        }

    def collection_fields(self) -> frozenset[IpsLogicalField]:
        if self.protocol is not None:
            return REQUIRED_FIELDS | {IpsLogicalField.PROTOCOL}
        return REQUIRED_FIELDS

    def action_value_map(self) -> dict[str, list[str]]:
        if not (self.blocked_actions or self.allowed_actions):
            return {}
        return {"BLOCKED": list(self.blocked_actions), "ALLOWED": list(self.allowed_actions)}

    def mapping_dict(self) -> dict[str, dict[str, str] | None]:
        out: dict[str, dict[str, str] | None] = {}
        for field in IpsLogicalField:
            reference = self.reference_for(field)
            out[field.value] = reference.as_dict() if reference is not None else None
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.mapping_dict(),
            "ACTION_VALUE_MAP": self.action_value_map(),
        }

    def apply_to(self, profile: IpsSourceProfile) -> None:
        profile.mapping = self.mapping_dict()
        profile.action_value_map = self.action_value_map()


def _normalized(name: str) -> IpsFieldReference:
    return IpsFieldReference(IpsReferenceKind.NORMALIZED, name)


def _default_mapping() -> IpsFieldMapping:
    return IpsFieldMapping(
        source_ip=_normalized("sourceip"),
        destination_ip=_normalized("destinationip"),
        destination_port=_normalized("destinationport"),
        protocol=_normalized("protocolid"),
        signature=_normalized("qid"),
        action=None,
        severity=_normalized("severity"),
    )


IPS_PRESETS: dict[IpsPreset, IpsFieldMapping] = {
    IpsPreset.CISCO_FTD: _default_mapping(),
    IpsPreset.PALO_ALTO_THREAT: _default_mapping(),
}


def profile_ineligibility(profile: IpsSourceProfile) -> str | None:
    """Why a later IPS collection run must not use this profile."""
    if not profile.enabled:
        return "the profile is disabled"
    if profile.validated_profile_version != profile.profile_version:
        return (
            f"profile version {profile.profile_version} has not been validated "
            f"(last validated: {profile.validated_profile_version})"
        )
    if profile.validation_status != IpsProfileValidationStatus.VALID:
        return f"profile validation is {profile.validation_status}"
    return None
