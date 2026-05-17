"""Customer / collaborator limits and public pricing catalog (INR, monthly / yearly)."""

from __future__ import annotations

from typing import Any, Literal

SubscriptionTier = Literal["pending", "free", "solo", "starter", "growth", "business", "enterprise"]
PaidTier = Literal["solo", "starter", "growth", "business", "enterprise"]
BillingPeriod = Literal["monthly", "yearly"]

# Ordered from cheapest to most expensive — used for upgrade comparisons.
PAID_TIER_ORDER: tuple[PaidTier, ...] = ("solo", "starter", "growth", "business", "enterprise")

# Legacy tier names from old DB records → new tier names.
LEGACY_TIER_MAP: dict[str, str] = {
    "basic": "growth",
    "pro": "business",
    "elite": "enterprise",
    "unlimited": "enterprise",
}

# How many workers (collaborators) each tier allows under one owner.
# 0 = no workers allowed; 10_000_000 = effectively unlimited.
TIER_COLLABORATOR_LIMIT: dict[str, int] = {
    "pending": 0,
    "free": 0,
    "solo": 0,       # owner only, no workers
    "starter": 5,
    "growth": 10,
    "business": 20,
    "enterprise": 10_000_000,
}

# Global customer count per owner (all villages + finance modes).
TIER_CUSTOMER_LIMIT: dict[str, int] = {
    "pending": 0,
    "free": 2,
    "solo": 10_000_000,
    "starter": 10_000_000,
    "growth": 10_000_000,
    "business": 10_000_000,
    "enterprise": 10_000_000,
}

# Monthly list prices (INR).
PAID_MONTHLY_INR: dict[PaidTier, int] = {
    "solo": 99,
    "starter": 150,
    "growth": 199,
    "business": 299,
    "enterprise": 599,
}

# Payment gateway charges passed on to the subscriber.
PLATFORM_FEE_PERCENT: float = 2.15   # % of base price
GST_PERCENT: float = 18.0            # % of base price (Indian GST on subscription)


def total_charge_paise(base_inr: int) -> int:
    """
    Base price + 2.15% platform fee + 18% GST, both applied on the base price.
    Total extra = 20.15% of base.
    This is the exact amount sent to Razorpay and verified on payment confirmation.
    """
    total = base_inr * (1 + (PLATFORM_FEE_PERCENT + GST_PERCENT) / 100)
    return round(total * 100)


def customer_limit_for_tier(tier: str) -> int:
    tier = LEGACY_TIER_MAP.get(tier, tier)
    return TIER_CUSTOMER_LIMIT.get(tier, TIER_CUSTOMER_LIMIT["free"])


def collaborator_limit_for_tier(tier: str) -> int:
    tier = LEGACY_TIER_MAP.get(tier, tier)
    return TIER_COLLABORATOR_LIMIT.get(tier, 0)


def yearly_price_inr(tier: PaidTier) -> int:
    """Yearly = 10× monthly (two months free) — simple, attractive annual anchor."""
    return PAID_MONTHLY_INR[tier] * 10


def yearly_savings_vs_monthly_x12_inr(tier: PaidTier) -> int:
    return PAID_MONTHLY_INR[tier] * 12 - yearly_price_inr(tier)


def build_amount_to_plan_lookup() -> dict[int, tuple[str, str]]:
    """Map each unique Razorpay amount (paise) → (tier, billing_period).

    All 10 combinations produce distinct amounts so this is a safe reverse lookup
    used in verify-payment to identify which plan was purchased without round-trips
    or extra storage.
    """
    lookup: dict[int, tuple[str, str]] = {}
    for tier in PAID_TIER_ORDER:
        monthly_paise = total_charge_paise(PAID_MONTHLY_INR[tier])
        yearly_paise = total_charge_paise(yearly_price_inr(tier))
        lookup[monthly_paise] = (tier, "monthly")
        lookup[yearly_paise] = (tier, "yearly")
    return lookup


# Eagerly built once at import time — no need to recompute per request.
AMOUNT_TO_PLAN: dict[int, tuple[str, str]] = build_amount_to_plan_lookup()


def public_plans_catalog() -> dict[str, Any]:
    """Single source of truth for the mobile plan picker."""
    paid_rows: list[dict[str, Any]] = []
    for tier in PAID_TIER_ORDER:
        monthly = PAID_MONTHLY_INR[tier]
        yearly = yearly_price_inr(tier)
        savings = yearly_savings_vs_monthly_x12_inr(tier)
        collab_limit = TIER_COLLABORATOR_LIMIT[tier]
        paid_rows.append(
            {
                "id": tier,
                "collaborator_limit": None if collab_limit >= 10_000_000 else collab_limit,
                "customer_limit": None,   # unlimited for all paid tiers
                "monthly_inr": monthly,
                "yearly_inr": yearly,
                "yearly_savings_inr_vs_monthly_x12": savings,
                "recommended": tier == "growth",
            }
        )
    return {
        "currency": "INR",
        "billing_note": "Yearly is billed as 10× monthly — about 17% less than paying monthly for 12 months.",
        "free": {
            "id": "free",
            "collaborator_limit": 0,
            "customer_limit": TIER_CUSTOMER_LIMIT["free"],
            "monthly_inr": 0,
            "yearly_inr": 0,
        },
        "paid_tiers": paid_rows,
    }
