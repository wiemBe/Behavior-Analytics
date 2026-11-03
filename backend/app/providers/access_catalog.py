"""The QRadar access catalog (ACC-1): every API the application calls, and what needs it.

Code, not data: the entries are reviewed with the provider they describe. A test reads
`qradar_rest.py` and fails when a path the provider calls is missing here, so a new call cannot
ship without saying what it feeds and what the token must be allowed to do.

`required_capability` is the QRadar capability **IBM documents for that API**, and it is set only
when IBM's reference says so. IBM's per-endpoint pages (REST API reference, v18.0 and later) state
no capability at all: they list the response codes (never a 403) and nothing about roles. So every
entry carries `required_capability = None` and `capability_verified = False`, and the closest role
permission IBM documents for the same data (the user-role table in the Administration Guide) is
given as `capability_hint`, labelled as a hint. The authoritative answer is the check itself: a 403
from the console names the entry the token cannot reach. The interactive API documentation on the
console (`https://<console>/api_doc/`) is the place to confirm a hint per version.

Nothing here is read from a console, so nothing here can contain a secret.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

#: Syncs an entry can start once it is reachable again. Each is run by the service that owns
#: the work; the catalog only names them.
SYNC_LOG_SOURCES = "log_source_inventory"
SYNC_RULES = "rule_inventory"
SYNC_OFFENSES = "offense_snapshot"
SYNC_VOLUME = "log_source_metrics"
SYNC_HEALTH = "health_cache"

#: Every sync name, in the order the API lists them.
SYNC_NAMES: tuple[str, ...] = (
    SYNC_LOG_SOURCES,
    SYNC_RULES,
    SYNC_OFFENSES,
    SYNC_VOLUME,
    SYNC_HEALTH,
)

ProbeKind = Literal["get", "ariel"]

_API_V18 = "https://ibmsecuritydocs.github.io/qradar_api_18.0/18.0--"
_ROLES = "https://www.ibm.com/docs/SS42VS_7.5/com.ibm.qradar.doc/t_qradar_adm_create_user.html"


@dataclass(frozen=True)
class CoveredCall:
    """A call the application makes that is not probed on its own, and why not."""

    method: str
    path: str
    reason: str


@dataclass(frozen=True)
class AccessEntry:
    id: str
    #: Short name for tables.
    title: str
    #: The probed call. `probe` "ariel" runs the stored probe search instead of a GET.
    method: str
    path: str
    probe: ProbeKind
    #: The `Range` header of the probe; None when the endpoint refuses one.
    range_header: str | None
    #: What this API feeds in this application, one English sentence.
    feeds: str
    #: The collectors and pages that depend on it.
    used_by: tuple[str, ...]
    #: Syncs started when this entry goes from refused or unreachable to reachable.
    syncs: tuple[str, ...]
    #: IBM's documented capability for this API; None when IBM documents none.
    required_capability: str | None
    capability_verified: bool
    #: The closest role permission IBM documents for the same data. A hint, never a fact.
    capability_hint: str | None
    #: IBM pages read for this entry.
    sources: tuple[str, ...]
    #: Other calls this entry stands for, with the reason each is not probed.
    covers: tuple[CoveredCall, ...] = ()


def _api(page: str) -> str:
    return f"{_API_V18}{page}.html"


ACCESS_CATALOG: tuple[AccessEntry, ...] = (
    AccessEntry(
        id="system_about",
        title="System information",
        method="GET",
        path="/system/about",
        probe="get",
        range_header=None,
        feeds="The connection check and the QRadar version shown for each console.",
        used_by=("console registration", "doctor qradar_auth", "Consoles page"),
        syncs=(SYNC_HEALTH,),
        required_capability=None,
        capability_verified=False,
        capability_hint=None,
        sources=(_api("system-about-GET"),),
    ),
    AccessEntry(
        id="managed_servers",
        title="Managed hosts",
        method="GET",
        path="/system/servers",
        probe="get",
        range_header="items=0-0",
        feeds="The managed-host status in the QRadar health section.",
        used_by=("Ariel load page, QRadar section", "doctor qradar_health"),
        syncs=(SYNC_HEALTH,),
        required_capability=None,
        capability_verified=False,
        capability_hint="Admin",
        sources=(_api("system-servers-GET"), _ROLES),
    ),
    AccessEntry(
        id="deployment_hosts",
        title="Deployment hosts",
        method="GET",
        path="/config/deployment/hosts",
        probe="get",
        range_header="items=0-0",
        feeds="Events per second against the allocation in the QRadar health section.",
        used_by=("Ariel load page, QRadar section", "doctor qradar_health"),
        syncs=(SYNC_HEALTH,),
        required_capability=None,
        capability_verified=False,
        capability_hint="Admin",
        sources=(_api("config-deployment-hosts-GET"), _ROLES),
    ),
    AccessEntry(
        id="license_pool",
        title="License pool",
        method="GET",
        path="/config/deployment/license_pool",
        probe="get",
        # A single-object endpoint: QRadar refuses a Range on it, so none is sent.
        range_header=None,
        feeds="The license allocation in the QRadar health section.",
        used_by=("Ariel load page, QRadar section", "doctor qradar_health"),
        syncs=(SYNC_HEALTH,),
        required_capability=None,
        capability_verified=False,
        capability_hint="Admin",
        sources=(_api("config-deployment-license_pool-GET"), _ROLES),
    ),
    AccessEntry(
        id="ariel_search_list",
        title="Ariel search list",
        method="GET",
        path="/ariel/searches",
        probe="get",
        range_header="items=0-0",
        feeds="How many Ariel searches QRadar holds, in the QRadar health section.",
        used_by=("Ariel load page, QRadar section", "doctor qradar_health"),
        syncs=(SYNC_HEALTH,),
        required_capability=None,
        capability_verified=False,
        capability_hint="Log Activity",
        sources=(_api("ariel-searches-GET"), _ROLES),
    ),
    AccessEntry(
        id="log_sources",
        title="Log sources",
        method="GET",
        path="/config/event_sources/log_source_management/log_sources",
        probe="get",
        range_header="items=0-0",
        feeds="The log source inventory, which every volume and health view starts from.",
        used_by=(
            "log source inventory sync",
            "Log sources page",
            "Fleet pages",
            "volume collection",
        ),
        syncs=(SYNC_LOG_SOURCES,),
        required_capability=None,
        capability_verified=False,
        capability_hint="Read-only Configuration, View Log Sources",
        sources=(_api("config-event_sources-log_source_management-log_sources-GET"), _ROLES),
        covers=(
            CoveredCall(
                "GET",
                "/config/event_sources/log_source_management/log_sources/{id}",
                "one log source of the list; taken as the same capability, not probed separately",
            ),
        ),
    ),
    AccessEntry(
        id="log_source_types",
        title="Log source types",
        method="GET",
        path="/config/event_sources/log_source_management/log_source_types",
        probe="get",
        range_header="items=0-0",
        feeds="The device type of each log source, which the Fleet classification uses.",
        used_by=("log source inventory sync", "Fleet pages"),
        syncs=(SYNC_LOG_SOURCES,),
        required_capability=None,
        capability_verified=False,
        capability_hint="Read-only Configuration, View Log Sources",
        sources=(
            _api("config-event_sources-log_source_management-log_source_types-GET"),
            _ROLES,
        ),
    ),
    AccessEntry(
        id="log_source_groups",
        title="Log source groups",
        method="GET",
        path="/config/event_sources/log_source_management/log_source_groups",
        probe="get",
        range_header="items=0-0",
        feeds="The log source groups shown beside each source and used by the Fleet pages.",
        used_by=("log source inventory sync", "Fleet pages"),
        syncs=(SYNC_LOG_SOURCES,),
        required_capability=None,
        capability_verified=False,
        capability_hint="Read-only Configuration, View Log Sources",
        sources=(
            _api("config-event_sources-log_source_management-log_source_groups-GET"),
            _ROLES,
        ),
    ),
    AccessEntry(
        id="custom_properties",
        title="Custom event properties",
        method="GET",
        path="/config/event_sources/custom_properties/regex_properties",
        probe="get",
        range_header="items=0-0",
        feeds="The property names offered by the WAF, IPS and property-watch mapping editors.",
        used_by=("WAF mapping", "IPS mapping", "Property watches"),
        syncs=(),
        required_capability=None,
        capability_verified=False,
        capability_hint="Log Activity, User Defined Event Properties",
        sources=(_api("config-event_sources-custom_properties-regex_properties-GET"), _ROLES),
    ),
    AccessEntry(
        id="property_expressions",
        title="Property expressions",
        method="GET",
        path="/config/event_sources/custom_properties/property_expressions",
        probe="get",
        range_header="items=0-0",
        feeds="Which log source types each custom property covers, in the mapping editors.",
        used_by=("WAF mapping", "IPS mapping", "Property watches"),
        syncs=(),
        required_capability=None,
        capability_verified=False,
        capability_hint="Log Activity, User Defined Event Properties",
        sources=(
            _api("config-event_sources-custom_properties-property_expressions-GET"),
            _ROLES,
        ),
    ),
    AccessEntry(
        id="analytics_rules",
        title="Analytics rules",
        method="GET",
        path="/analytics/rules",
        probe="get",
        range_header="items=0-0",
        feeds="The rule inventory behind the Rules and Detection coverage pages.",
        used_by=("rule inventory sync", "Rules page", "Detection coverage", "rule health"),
        syncs=(SYNC_RULES,),
        required_capability=None,
        capability_verified=False,
        capability_hint="Log Activity, View Custom Rules",
        sources=(_api("analytics-rules-GET"), _ROLES),
    ),
    AccessEntry(
        id="building_blocks",
        title="Building blocks",
        method="GET",
        path="/analytics/building_blocks",
        probe="get",
        range_header="items=0-0",
        feeds="The building blocks the rule inventory merges with the rules.",
        used_by=("rule inventory sync", "Rules page", "Detection coverage"),
        syncs=(SYNC_RULES,),
        required_capability=None,
        capability_verified=False,
        capability_hint="Log Activity, View Custom Rules",
        sources=(_api("analytics-building_blocks-GET"), _ROLES),
    ),
    AccessEntry(
        id="offenses",
        title="Offenses",
        method="GET",
        path="/siem/offenses",
        probe="get",
        range_header="items=0-0",
        feeds="The offense snapshots behind the Offenses page and the rule firing evidence.",
        used_by=("offense collection", "Offenses page", "rule metrics"),
        syncs=(SYNC_OFFENSES,),
        required_capability=None,
        capability_verified=False,
        capability_hint="Offenses",
        sources=(_api("siem-offenses-GET"), _ROLES),
    ),
    AccessEntry(
        id="ariel_searches",
        title="Ariel searches",
        method="POST",
        path="/ariel/searches",
        probe="ariel",
        range_header=None,
        feeds=(
            "Every measured view: volume per log source, WAF, IPS, property watches, "
            "catch-all senders, geography, rule matches, anomaly explanations and scheduled "
            "searches."
        ),
        used_by=(
            "volume collection",
            "WAF, IPS, property-watch, catch-all and geography collection",
            "rule matches",
            "anomaly explanations",
            "scheduled and manual searches",
            "history backfill",
        ),
        syncs=(SYNC_VOLUME,),
        required_capability=None,
        capability_verified=False,
        capability_hint="Log Activity",
        sources=(_api("ariel-searches-POST"), _ROLES),
        covers=(
            CoveredCall(
                "DELETE",
                "/ariel/searches/{search_id}",
                "the probe search is cancelled through it; not probed separately",
            ),
            CoveredCall(
                "GET",
                "/ariel/searches/{search_id}",
                "the status of a search the token created; not probed separately",
            ),
            CoveredCall(
                "GET",
                "/ariel/searches/{search_id}/results",
                "the rows of a search the token created; not probed separately",
            ),
        ),
    ),
)

CATALOG_BY_ID: dict[str, AccessEntry] = {entry.id: entry for entry in ACCESS_CATALOG}


def catalog_calls() -> set[str]:
    """Every "METHOD /path" the catalog accounts for, probed or covered."""
    calls: set[str] = set()
    for entry in ACCESS_CATALOG:
        calls.add(f"{entry.method} {entry.path}")
        calls.update(f"{cover.method} {cover.path}" for cover in entry.covers)
    return calls
