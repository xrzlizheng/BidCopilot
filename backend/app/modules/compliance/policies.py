from dataclasses import replace
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.compliance.models import CompliancePolicy
from app.modules.compliance.registry import Rule, load_rule_set


def apply_policies(rules: list[Rule], policies: list[CompliancePolicy]) -> list[Rule]:
    by_rule = {policy.rule_id: policy for policy in policies}
    effective: list[Rule] = []
    for rule in rules:
        policy = by_rule.get(rule.rule_id)
        if policy is None:
            effective.append(rule)
            continue
        if not policy.enabled:
            continue
        updated = rule
        if policy.severity_override:
            updated = replace(updated, severity=policy.severity_override)
        if policy.threshold_override is not None:
            updated = replace(updated, threshold=policy.threshold_override)
        effective.append(updated)
    return effective


async def effective_rules_for_tenant(
    session: AsyncSession, tenant_id: UUID
) -> tuple[str, list[Rule], int]:
    version, rules = load_rule_set()
    policies = list(
        (
            await session.execute(
                select(CompliancePolicy).where(CompliancePolicy.tenant_id == tenant_id)
            )
        )
        .scalars()
        .all()
    )
    return version, apply_policies(rules, policies), len(policies)


async def list_policy_rows(session: AsyncSession, tenant_id: UUID) -> list[dict[str, Any]]:
    _, rules = load_rule_set()
    policies = {
        policy.rule_id: policy
        for policy in (
            await session.execute(
                select(CompliancePolicy).where(CompliancePolicy.tenant_id == tenant_id)
            )
        )
        .scalars()
        .all()
    }
    rows = []
    for rule in rules:
        policy = policies.get(rule.rule_id)
        rows.append(
            {
                "rule_id": rule.rule_id,
                "name": rule.name,
                "category": rule.category,
                "type": rule.type,
                "default_severity": rule.severity,
                "effective_severity": (
                    policy.severity_override
                    if policy and policy.severity_override
                    else rule.severity
                ),
                "effective_threshold": (
                    policy.threshold_override
                    if policy and policy.threshold_override is not None
                    else rule.threshold
                ),
                "enabled": policy.enabled if policy else True,
                "overridden": policy is not None,
            }
        )
    return rows


async def update_policy(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    rule_id: str,
    enabled: bool,
    severity_override: str | None,
    user_id: UUID,
    threshold_override: float | None = None,
) -> CompliancePolicy:
    _, rules = load_rule_set()
    if rule_id not in {rule.rule_id for rule in rules}:
        raise NotFoundError("合规规则不存在")
    policy = (
        await session.execute(
            select(CompliancePolicy).where(
                CompliancePolicy.tenant_id == tenant_id,
                CompliancePolicy.rule_id == rule_id,
            )
        )
    ).scalar_one_or_none()
    if policy is None:
        policy = CompliancePolicy(tenant_id=tenant_id, rule_id=rule_id)
        session.add(policy)
    policy.enabled = enabled
    policy.severity_override = severity_override
    policy.threshold_override = threshold_override
    policy.updated_by = user_id
    await session.commit()
    await session.refresh(policy)
    return policy
