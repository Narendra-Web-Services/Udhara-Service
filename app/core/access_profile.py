"""Resolve effective subscription + usage for admins and collaborators."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pymongo.collection import Collection

from app.core.subscription_catalog import (
    LEGACY_TIER_MAP,
    collaborator_limit_for_tier,
    customer_limit_for_tier,
)
from app.models.user import UserInDB, UserPublic


def _normalize_tier(tier: str) -> str:
    return LEGACY_TIER_MAP.get(tier, tier)


def _subscription_is_expired(user: UserInDB) -> bool:
    """Paid tiers expire; free and pending tiers never do."""
    normalized = _normalize_tier(user.subscription_tier)
    if normalized in ("pending", "free"):
        return False
    if user.subscription_expires_at is None:
        return False
    exp = user.subscription_expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return exp < datetime.now(timezone.utc)


def subscription_is_active(user: UserInDB) -> bool:
    """True if the user has a non-pending, non-expired subscription."""
    return user.subscription_tier != "pending" and not _subscription_is_expired(user)


def _owner_id_for_subscription(user: UserInDB) -> str:
    if user.role == "customer" and user.linked_admin_id:
        return user.linked_admin_id
    return user.id


def _source_document_for_subscription(user: UserInDB, users_coll: Collection) -> dict[str, Any] | None:
    owner_id = _owner_id_for_subscription(user)
    return users_coll.find_one({"_id": owner_id})


def count_customers_for_owner(customer_coll: Collection, owner_id: str) -> int:
    return int(customer_coll.count_documents({"owner_user_id": owner_id}))


def count_collaborators_for_owner(users_coll: Collection, owner_id: str) -> int:
    return int(users_coll.count_documents({"role": "customer", "linked_admin_id": owner_id}))


def subscription_usage_for_dashboard(user: UserInDB, users_coll: Collection, customers_coll: Collection) -> dict[str, Any]:
    pub = build_user_public(user, users_coll, customers_coll)
    return {
        "has_subscription": pub.has_subscription,
        "subscription_tier": pub.subscription_tier,
        "billing_period": pub.billing_period,
        "customer_usage_used": pub.customer_usage_used,
        "customer_usage_limit": pub.customer_usage_limit,
        "collaborator_usage_used": pub.collaborator_usage_used,
        "collaborator_usage_limit": pub.collaborator_usage_limit,
        "subscription_expires_at": pub.subscription_expires_at,
    }


def build_user_public(user: UserInDB, users_coll: Collection, customers_coll: Collection) -> UserPublic:
    """Subscription and usage follow the business owner (admin), including for collaborators."""
    doc = _source_document_for_subscription(user, users_coll)
    if doc is None:
        return UserPublic(
            id=user.id,
            full_name=user.full_name,
            email=user.email,
            phone_number=user.phone_number,
            role=user.role,
            has_subscription=False,
            linked_admin_id=user.linked_admin_id,
            allow_collaborators=user.allow_collaborators,
            subscription_tier="pending",
            billing_period=None,
            customer_usage_used=0,
            customer_usage_limit=customer_limit_for_tier("free"),
            collaborator_usage_used=0,
            collaborator_usage_limit=0,
            subscription_expires_at=None,
        )

    sub_user = UserInDB.from_mongo(doc)
    owner_id = _owner_id_for_subscription(user)
    tier = _normalize_tier(sub_user.subscription_tier)

    customer_used = count_customers_for_owner(customers_coll, owner_id)
    customer_limit = customer_limit_for_tier(tier)

    collab_used = count_collaborators_for_owner(users_coll, owner_id)
    collab_limit = collaborator_limit_for_tier(tier)

    has_access = tier != "pending" and not _subscription_is_expired(sub_user)

    return UserPublic(
        id=user.id,
        full_name=user.full_name,
        email=user.email,
        phone_number=user.phone_number,
        role=user.role,
        has_subscription=has_access,
        linked_admin_id=user.linked_admin_id,
        allow_collaborators=user.allow_collaborators if user.role == "customer" else sub_user.allow_collaborators,
        subscription_tier=tier,  # type: ignore[arg-type]
        billing_period=sub_user.billing_period,
        customer_usage_used=customer_used,
        customer_usage_limit=customer_limit,
        collaborator_usage_used=collab_used,
        collaborator_usage_limit=collab_limit,
        subscription_expires_at=sub_user.subscription_expires_at,
    )
