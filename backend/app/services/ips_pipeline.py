"""Shared names for the per-source IPS pipeline lock."""

IPS_PIPELINE_LOCK = "ips_pipeline"


def ips_pipeline_lock_key(qradar_log_source_id: int) -> str:
    """The lock validation and later collection share for one IPS source."""
    return f"{IPS_PIPELINE_LOCK}:{int(qradar_log_source_id)}"
