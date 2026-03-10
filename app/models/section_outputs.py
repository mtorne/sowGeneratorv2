"""Pydantic output models for structured SoW section generation.

These models define the expected JSON schema for sections that use
structured (JSON) output mode.  The LLM is prompted to return a JSON
object matching each model's schema; ``WriterAgent`` optionally validates
the raw JSON string and ``DocumentBuilder`` uses the parsed dict for DOCX
rendering via :meth:`DocumentBuilder._inject_structured_section`.

Sections covered
----------------
* ``MILESTONE PLAN``      → :class:`MilestonePlanOutput`
* ``HIGH AVAILABILITY``   → :class:`HighAvailabilityOutput`
* ``BACKUP STRATEGY``     → :class:`BackupStrategyOutput`
* ``DISASTER RECOVERY``   → :class:`DisasterRecoveryOutput`
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# MILESTONE PLAN
# ---------------------------------------------------------------------------

class MilestoneEntry(BaseModel):
    """A single milestone row inside :class:`MilestonePlanOutput`.

    Matches the template table columns: Milestone | Target Date | Completed | Comments.
    """

    milestone: str = Field(
        ...,
        description=(
            "Short milestone event name, e.g. 'Project Kick-Off with Oracle Architect', "
            "'Current State Architecture Diagram provided'."
        ),
    )
    target_date: str = Field(
        default="PENDING TO REVIEW",
        description="Target date (DD-MM-YYYY) or 'PENDING TO REVIEW' if unknown.",
    )
    completed: str = Field(
        default="",
        description="Completion date (DD-MM-YYYY) — leave empty if not yet completed.",
    )
    comments: str = Field(
        default="",
        description="Optional brief comment or note about this milestone.",
    )


class MilestonePlanOutput(BaseModel):
    """Structured output for the MILESTONE PLAN section.

    Produces rows to be appended to the existing template milestone table
    (Milestone | Target Date | Completed | Comments).
    """

    milestones: list[MilestoneEntry] = Field(
        ...,
        min_length=4,
        max_length=8,
        description="Ordered list of 4-8 project milestones.",
    )


# ---------------------------------------------------------------------------
# HIGH AVAILABILITY
# ---------------------------------------------------------------------------

class HighAvailabilityOutput(BaseModel):
    """Structured output for the HIGH AVAILABILITY section.

    Each field contains a list of bullet strings (no prefix characters).
    """

    oci_ha_capabilities: list[str] = Field(
        ...,
        description="OCI HA constructs used: Fault Domains, ADs, Load Balancer, Data Guard, etc.",
    )
    redundancy_architecture: list[str] = Field(
        ...,
        description="How redundancy is achieved at each tier: compute, DB, storage, network.",
    )
    failover_strategy: list[str] = Field(
        ...,
        description="Failover mechanism: automatic vs. manual, DNS-based, OCI Full Stack DR, etc.",
    )
    rto_rpo_targets: list[str] = Field(
        ...,
        description="RTO and RPO targets.  Use 'PENDING TO REVIEW' when not specified.",
    )


# ---------------------------------------------------------------------------
# BACKUP STRATEGY
# ---------------------------------------------------------------------------

class BackupStrategyOutput(BaseModel):
    """Structured output for the BACKUP STRATEGY section."""

    data_backup: list[str] = Field(
        ...,
        description="Data-tier backup: OCI Block Volume Backups, MySQL Automated Backups, etc.",
    )
    application_backup: list[str] = Field(
        ...,
        description="Application state: OCIR images, OKE PVs, Vault secrets, config files.",
    )
    recovery_procedures: list[str] = Field(
        ...,
        description="Numbered restore steps, e.g. '1. Initiate OCI Block Volume restore …'.",
    )
    retention_policy: list[str] = Field(
        ...,
        description="Backup frequency and retention period per tier.  'PENDING TO REVIEW' if unknown.",
    )


# ---------------------------------------------------------------------------
# DISASTER RECOVERY
# ---------------------------------------------------------------------------

class DisasterRecoveryOutput(BaseModel):
    """Structured output for the DISASTER RECOVERY section."""

    dr_strategy: list[str] = Field(
        ...,
        description="DR tier (active-passive / active-active / warm standby) and OCI feature used.",
    )
    geographic_redundancy: list[str] = Field(
        ...,
        description="OCI Regions and ADs involved; workload distribution across DR topology.",
    )
    data_replication: list[str] = Field(
        ...,
        description="Replication mechanism: OCI Data Guard, MySQL InnoDB, Cross-Region replication, etc.",
    )
    dr_testing_plan: list[str] = Field(
        ...,
        description="DR drill frequency, runbook location, RTO/RPO targets.  'PENDING TO REVIEW' if unknown.",
    )
