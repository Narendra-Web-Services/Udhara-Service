import hashlib
import hmac
import uuid
from datetime import datetime, timedelta
from typing import Literal

import razorpay
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from pymongo.collection import Collection

from app.api.deps import get_current_user, get_customer_collection, get_user_collection
from app.core.access_profile import build_user_public
from app.core.config import get_settings
from app.core.subscription_catalog import (
    AMOUNT_TO_PLAN,
    PAID_MONTHLY_INR,
    PaidTier,
    public_plans_catalog,
    total_charge_paise,
    yearly_price_inr,
)
from app.core.timezone import get_ist_timezone
from app.models.user import UserInDB, UserPublic

router = APIRouter(prefix="/subscription", tags=["subscription"])
IST = get_ist_timezone()


class SelectPlanRequest(BaseModel):
    # Paid tiers MUST go through Razorpay — this endpoint is free-only.
    tier: Literal["free"]
    billing_period: None = None


class SelectPlanResponse(BaseModel):
    user: UserPublic


@router.get("/plans")
def get_plans() -> dict:
    """Public catalog (no auth) so the picker can render before session is ready."""
    return public_plans_catalog()


@router.post("/select-plan", response_model=SelectPlanResponse)
def select_plan(
    payload: SelectPlanRequest,
    current_user: UserInDB = Depends(get_current_user),
    users_coll: Collection = Depends(get_user_collection),
    customers_coll: Collection = Depends(get_customer_collection),
) -> SelectPlanResponse:
    if current_user.role == "customer" and current_user.linked_admin_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the business owner can change the subscription plan.",
        )

    update_doc: dict = {
        "subscription_tier": "free",
        "billing_period": None,
        "has_subscription": True,
        "subscription_expires_at": None,
    }
    users_coll.update_one({"_id": current_user.id}, {"$set": update_doc})

    refreshed = users_coll.find_one({"_id": current_user.id})
    if refreshed is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    fresh_user = UserInDB.from_mongo(refreshed)
    return SelectPlanResponse(user=build_user_public(fresh_user, users_coll, customers_coll))


# ── Razorpay payment endpoints ────────────────────────────────────────────────

class CreateOrderRequest(BaseModel):
    tier: PaidTier
    billing_period: Literal["monthly", "yearly"]


class CreateOrderResponse(BaseModel):
    order_id: str
    amount: int       # in paise
    currency: str
    key_id: str       # safe to send to client


class VerifyPaymentRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


@router.post("/create-order", response_model=CreateOrderResponse)
def create_razorpay_order(
    payload: CreateOrderRequest,
    current_user: UserInDB = Depends(get_current_user),
) -> CreateOrderResponse:
    if current_user.role == "customer" and current_user.linked_admin_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the business owner can subscribe.",
        )

    settings = get_settings()
    base_inr = (
        PAID_MONTHLY_INR[payload.tier]
        if payload.billing_period == "monthly"
        else yearly_price_inr(payload.tier)
    )
    amount_paise = total_charge_paise(base_inr)

    client = razorpay.Client(auth=(settings.razorpay_key_id, settings.razorpay_key_secret))
    # Receipt encodes: user ID prefix + tier + billing period code + nonce.
    # Split with rsplit('_', 3) on verify: [uid_prefix, tier, bp_code, nonce].
    bp_code = "m" if payload.billing_period == "monthly" else "y"
    receipt = f"{current_user.id[:10]}_{payload.tier}_{bp_code}_{uuid.uuid4().hex[:8]}"
    order = client.order.create({
        "amount": amount_paise,
        "currency": "INR",
        "receipt": receipt,
    })

    return CreateOrderResponse(
        order_id=order["id"],
        amount=order["amount"],
        currency=order["currency"],
        key_id=settings.razorpay_key_id,
    )


@router.post("/verify-payment", response_model=SelectPlanResponse)
def verify_razorpay_payment(
    payload: VerifyPaymentRequest,
    current_user: UserInDB = Depends(get_current_user),
    users_coll: Collection = Depends(get_user_collection),
    customers_coll: Collection = Depends(get_customer_collection),
) -> SelectPlanResponse:
    settings = get_settings()

    # Verify HMAC-SHA256 signature — prevents activating without real payment.
    expected_sig = hmac.new(
        settings.razorpay_key_secret.encode(),
        f"{payload.razorpay_order_id}|{payload.razorpay_payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_sig, payload.razorpay_signature):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payment verification failed. Invalid signature.",
        )

    # Replay protection: reject if this payment_id was already used by anyone.
    already_used = users_coll.find_one({"verified_payment_ids": payload.razorpay_payment_id})
    if already_used is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payment has already been applied to an account.",
        )

    # Fetch Razorpay order and verify ownership via receipt prefix.
    client = razorpay.Client(auth=(settings.razorpay_key_id, settings.razorpay_key_secret))
    order = client.order.fetch(payload.razorpay_order_id)

    receipt: str = order.get("receipt", "")
    if not receipt.startswith(current_user.id[:10]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Order was not created for this account.",
        )

    # Determine tier + billing period from the paid amount (all 10 combos are unique amounts).
    amount_paise: int = order["amount"]
    plan = AMOUNT_TO_PLAN.get(amount_paise)
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unrecognised payment amount.",
        )
    tier, billing_period = plan
    expires_at = datetime.now(IST) + timedelta(days=30 if billing_period == "monthly" else 365)

    update_doc = {
        "subscription_tier": tier,
        "billing_period": billing_period,
        "has_subscription": True,
        "subscription_expires_at": expires_at,
    }
    users_coll.update_one(
        {"_id": current_user.id},
        {
            "$set": update_doc,
            # Record payment ID so it can never be replayed — checked above before activation.
            "$addToSet": {"verified_payment_ids": payload.razorpay_payment_id},
        },
    )

    refreshed = users_coll.find_one({"_id": current_user.id})
    if refreshed is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    fresh_user = UserInDB.from_mongo(refreshed)
    return SelectPlanResponse(user=build_user_public(fresh_user, users_coll, customers_coll))
