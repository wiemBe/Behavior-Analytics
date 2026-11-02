"""Registration and lookup of monitored QRadar consoles.

Kept out of the CLI so the same idempotent path serves an admin API later, and
so it can be tested without a subprocess.

Connection details live on the row rather than in provider source or global
settings: a deployment monitors N consoles, each with its own credentials.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.instance import QRadarInstance


class InstanceNotFound(LookupError):
    """No registered instance matched the requested name."""


@dataclass(frozen=True)
class RegistrationResult:
    instance: QRadarInstance
    created: bool
    #: The row is enabled, and it was created, a connection setting changed, or
    #: a disabled console was just enabled. The caller enqueues after commit.
    queue_inventory: bool

    @property
    def action(self) -> str:
        return "created" if self.created else "updated"


def _connection_changed(
    existing: QRadarInstance,
    *,
    console_host: str,
    api_version: str,
    ca_bundle_path: str | None,
    provider_kind: str,
    verify_ssl: bool,
    sec_token: str | None,
    mcp_base_url: str | None,
) -> bool:
    """True when this update changes how we reach the console.

    Description, the stored version and the health fields are not connection
    settings: rewriting them must not kick a fresh inventory. A token or MCP
    URL of None means "leave the stored value", which is not a change.
    """
    if (
        existing.console_host != console_host
        or existing.api_version != api_version
        or existing.ca_bundle_path != ca_bundle_path
        or existing.provider_kind != provider_kind
        or existing.verify_ssl != verify_ssl
    ):
        return True
    if sec_token is not None and existing.sec_token != sec_token:
        return True
    return mcp_base_url is not None and (
        existing.mcp_base_url != mcp_base_url or existing.mcp_enabled is not True
    )


def read_token_file(path: str | Path) -> str:
    """Load a SEC token from disk.

    Stripped: a token file written by an editor ends in a newline, and a
    newline inside a SEC header produces a 401 indistinguishable from a wrong
    token. Errors name the path, never the contents.
    """
    p = Path(path)
    try:
        token = p.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError(f"token file could not be read: {p} ({exc.strerror})") from None
    if not token:
        raise ValueError(f"token file is empty: {p}")
    return token


class InstanceRegistry:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_name(self, name: str) -> QRadarInstance:
        instance = await self.session.scalar(
            select(QRadarInstance).where(QRadarInstance.name == name)
        )
        if instance is None:
            raise InstanceNotFound(f"no QRadar instance registered under the name {name!r}")
        return instance

    async def list_all(self) -> list[QRadarInstance]:
        rows = await self.session.scalars(select(QRadarInstance).order_by(QRadarInstance.name))
        return list(rows.all())

    async def register(
        self,
        *,
        name: str,
        console_host: str,
        sec_token: str | None = None,
        api_version: str = "20.0",
        ca_bundle_path: str | None = None,
        provider_kind: str = "rest",
        verify_ssl: bool = True,
        mcp_base_url: str | None = None,
        description: str | None = None,
        enabled: bool = True,
    ) -> RegistrationResult:
        """Create or update the instance named `name`.

        Idempotent by name: running registration twice updates the existing row
        rather than creating a second console with the same identity. `name` is
        already unique in the schema, so a duplicate would fail anyway — this
        makes the intended outcome the easy one.

        A `sec_token` of None leaves any stored token untouched, so re-running
        registration to change (say) the API version does not wipe credentials.

        `queue_inventory` is true only for an enabled console that was created,
        whose connection settings changed, or that was disabled and is now
        enabled. The caller enqueues after it commits. A disabled console is
        never queued: the beat skips it, and a kick would too.
        """
        if provider_kind == "rest" and not console_host.startswith("https://"):
            raise ValueError(
                "console_host must use https:// — a SEC token must not cross plaintext"
            )
        if not verify_ssl:
            raise ValueError("verify_ssl=False is not supported; supply a CA bundle instead")
        if ca_bundle_path is not None and not Path(ca_bundle_path).exists():
            raise ValueError(f"CA bundle does not exist: {ca_bundle_path}")

        existing = await self.session.scalar(
            select(QRadarInstance).where(QRadarInstance.name == name)
        )

        if existing is None:
            instance = QRadarInstance(
                id=uuid.uuid4(),
                name=name,
                description=description,
                console_host=console_host,
                api_version=api_version,
                sec_token=sec_token,
                verify_ssl=True,
                ca_bundle_path=ca_bundle_path,
                provider_kind=provider_kind,
                mcp_base_url=mcp_base_url,
                mcp_enabled=mcp_base_url is not None,
                enabled=enabled,
            )
            self.session.add(instance)
            await self.session.flush()
            return RegistrationResult(instance, created=True, queue_inventory=enabled)

        changed = _connection_changed(
            existing,
            console_host=console_host,
            api_version=api_version,
            ca_bundle_path=ca_bundle_path,
            provider_kind=provider_kind,
            verify_ssl=True,
            sec_token=sec_token,
            mcp_base_url=mcp_base_url,
        )
        was_enabled = bool(existing.enabled)
        existing.console_host = console_host
        existing.api_version = api_version
        existing.ca_bundle_path = ca_bundle_path
        existing.provider_kind = provider_kind
        existing.enabled = enabled
        existing.verify_ssl = True
        if description is not None:
            existing.description = description
        if sec_token is not None:
            existing.sec_token = sec_token
        if mcp_base_url is not None:
            existing.mcp_base_url = mcp_base_url
            existing.mcp_enabled = True
        await self.session.flush()
        # Enabled and (the connection changed, or the console was just turned on).
        # A disabled row is not queued even when its connection settings changed.
        queue = enabled and (changed or not was_enabled)
        return RegistrationResult(existing, created=False, queue_inventory=queue)
