import logging

from .models import ComplianceResult, TradeInstruction
from .supabase_client import anon_client

logger = logging.getLogger(__name__)


async def check_compliance(instruction: TradeInstruction) -> ComplianceResult:
    """Query compliance_rules and evaluate against the proposed trade.

    Uses the anon client + Clerk JWT so RLS returns:
      - all scope='standard' rules (visible to any authenticated user)
      - only the requesting user's scope='user_defined' rules
    """
    client = await anon_client(instruction.clerk_jwt)

    response = await (
        client.table("compliance_rules")
        .select("*")
        .eq("active", True)
        .execute()
    )

    rules: list[dict] = response.data or []
    hard_blocks: list[dict] = []
    warnings: list[dict] = []
    ticker_upper = instruction.ticker.upper().removesuffix("USD").removesuffix("/USD")

    for rule in rules:
        # Defensive guard: RLS should already filter these, but double-check.
        if rule.get("scope") == "user_defined" and rule.get("user_id") != instruction.user_id:
            continue

        # Skip if this rule targets specific tickers and ours isn't one of them.
        applies_to_tickers: list[str] = rule.get("applies_to_tickers") or []
        if applies_to_tickers:
            normalised = [t.upper().removesuffix("USD").removesuffix("/USD") for t in applies_to_tickers]
            if ticker_upper not in normalised:
                continue

        # Skip if this rule targets a specific direction and it doesn't match.
        applies_to_direction: str | None = rule.get("applies_to_direction")
        if applies_to_direction and applies_to_direction != "both":
            if applies_to_direction != instruction.direction:
                continue

        severity: str = rule.get("severity", "")
        if severity == "hard_block":
            hard_blocks.append(rule)
        elif severity == "soft_warn":
            warnings.append(rule)
        # severity='info' is silently skipped (informational only)

    approved = len(hard_blocks) == 0

    if not approved:
        logger.warning(
            "Compliance BLOCKED user=%s %s %s — %d hard rule(s): %s",
            instruction.user_id,
            instruction.direction,
            instruction.ticker,
            len(hard_blocks),
            [r.get("rule_category") for r in hard_blocks],
        )
    elif warnings:
        logger.info(
            "Compliance APPROVED with %d warning(s) for user=%s %s %s",
            len(warnings),
            instruction.user_id,
            instruction.direction,
            instruction.ticker,
        )
    else:
        logger.info(
            "Compliance APPROVED (clean) for user=%s %s %s",
            instruction.user_id,
            instruction.direction,
            instruction.ticker,
        )

    return ComplianceResult(approved=approved, hard_blocks=hard_blocks, warnings=warnings)
