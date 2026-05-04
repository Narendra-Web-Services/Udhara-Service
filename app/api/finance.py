from datetime import UTC, date, datetime, time, timedelta
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pymongo.collection import Collection

from app.api.deps import (
    get_collection_record_collection,
    get_current_user,
    get_customer_collection,
    get_installment_collection,
    get_village_collection,
)
from app.core.finance_scope import villages_mongo_filter
from app.models.finance import (
    CollectionRecordCreate,
    CollectionRecordPublic,
    CollectionTimeseriesPoint,
    CollectionTransactionRow,
    CollectionsReportResponse,
    CustomerCreate,
    CustomerDetailResponse,
    CustomerPublic,
    CustomerUpdate,
    DelayedCustomerPublic,
    InstallmentCalendarEntry,
    VillageCreate,
    VillagePublic,
    VillageUpdate,
)
from app.core.subscription_catalog import customer_limit_for_tier
from app.models.user import UserInDB

router = APIRouter(tags=["finance"])

_VALID_PAYMENT_MODES = ("phonepe", "gpay", "cash")


def _effective_owner_id(user: UserInDB) -> str:
    """Collaborators (role=customer with linked_admin_id) see their admin's data."""
    if user.role == "customer" and user.linked_admin_id:
        return user.linked_admin_id
    return user.id


def _require_write_access(user: UserInDB) -> None:
    """Block write/delete operations for collaborator (Normal user) accounts."""
    if user.role == "customer":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Collaborator accounts can view data but cannot create, edit or delete.",
        )

def _village_finance_scope(document: dict) -> str:
    raw = document.get("finance_scope")
    if raw in ("daily", "weekly", "monthly", "yearly"):
        return raw
    return "weekly"


def _serialize_village(document: dict, customer_count: int = 0) -> VillagePublic:
    return VillagePublic(
        id=str(document["_id"]),
        owner_user_id=document["owner_user_id"],
        name=document["name"],
        day=document["day"],
        finance_scope=_village_finance_scope(document),
        customer_count=customer_count,
    )


def _serialize_customer(document: dict) -> CustomerPublic:
    return CustomerPublic(
        id=str(document["_id"]),
        owner_user_id=document["owner_user_id"],
        village_id=document["village_id"],
        full_name=document["full_name"],
        address=document["address"],
        amount_lent=float(document["amount_lent"]),
        payment_type=document["payment_type"],
        installment_amount=float(document["installment_amount"]),
        installment_count=int(document["installment_count"]),
        phone_number=document["phone_number"],
        image_url=document.get("image_url"),
        aadhar_number=document["aadhar_number"],
        aadhar_image_url=document.get("aadhar_image_url"),
        external_customer_id=document["external_customer_id"],
        overdue_installments=int(document.get("overdue_installments", 0)),
        overdue_amount=float(document.get("overdue_amount", 0)),
        due_this_month_installments=int(document.get("due_this_month_installments", 0)),
        due_this_month_amount=float(document.get("due_this_month_amount", 0)),
        due_this_year_installments=int(document.get("due_this_year_installments", 0)),
        due_this_year_amount=float(document.get("due_this_year_amount", 0)),
        due_today_installments=int(document.get("due_today_installments", 0)),
        due_today_amount=float(document.get("due_today_amount", 0)),
        total_collected=float(document.get("total_collected", 0)),
        last_collected_at=document.get("last_collected_at"),
        last_collected_by_name=document.get("last_collected_by_name"),
    )


def _serialize_collection_record(document: dict) -> CollectionRecordPublic:
    return CollectionRecordPublic(
        id=str(document["_id"]),
        collection_batch_id=document.get("collection_batch_id", str(document["_id"])),
        batch_anchor_installment_id=document.get("batch_anchor_installment_id", document["installment_id"]),
        owner_user_id=document["owner_user_id"],
        village_id=document["village_id"],
        customer_id=document["customer_id"],
        installment_id=document["installment_id"],
        amount_paid=float(document.get("batch_total_amount", document["amount_paid"])),
        covered_installment_count=int(document.get("covered_installment_count", 1)),
        covered_installment_ids=[str(item) for item in document.get("covered_installment_ids", [document["installment_id"]])],
        payment_mode=document["payment_mode"],
        collected_by_user_id=document["collected_by_user_id"],
        collected_by_name=document["collected_by_name"],
        collected_at=document["collected_at"],
        status_after_payment=document["status_after_payment"],
        note=document.get("note"),
    )


def _group_collection_history(collection_history: list[dict]) -> list[CollectionRecordPublic]:
    grouped: dict[str, dict] = {}

    for record in collection_history:
        batch_id = record.get("collection_batch_id", str(record["_id"]))
        if batch_id not in grouped:
            grouped[batch_id] = {
                **record,
                "covered_installment_ids": [],
                "covered_installment_count": 0,
                "batch_total_amount": 0.0,
            }

        grouped_record = grouped[batch_id]
        installment_id = str(record["installment_id"])
        if installment_id not in grouped_record["covered_installment_ids"]:
            grouped_record["covered_installment_ids"].append(installment_id)
            grouped_record["covered_installment_count"] += 1
        grouped_record["batch_total_amount"] += float(record["amount_paid"])

    sorted_grouped_records = sorted(
        grouped.values(),
        key=lambda item: item.get("collected_at") or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )
    return [_serialize_collection_record(grouped_record) for grouped_record in sorted_grouped_records]


def _installment_due_date(document: dict) -> date:
    due = document["due_date"]
    return due.date() if isinstance(due, datetime) else due


def _is_installment_overdue(document: dict, now: datetime) -> bool:
    return document.get("status") != "paid" and _installment_due_date(document) < now.date()


def _build_customer_metrics(
    owner_id: str,
    customer_ids: list[str],
    installment_collection: Collection,
    collection_record_collection: Collection,
) -> dict[str, dict[str, object]]:
    metrics = {
        customer_id: {
            "overdue_installments": 0,
            "overdue_amount": 0.0,
            "due_this_month_installments": 0,
            "due_this_month_amount": 0.0,
            "due_this_year_installments": 0,
            "due_this_year_amount": 0.0,
            "due_today_installments": 0,
            "due_today_amount": 0.0,
            "total_collected": 0.0,
            "last_collected_at": None,
            "last_collected_by_name": None,
        }
        for customer_id in customer_ids
    }
    if not customer_ids:
        return metrics

    now = datetime.now(UTC)
    installment_documents = list(
        installment_collection.find(
            {"owner_user_id": owner_id, "customer_id": {"$in": customer_ids}},
            {"customer_id": 1, "amount_due": 1, "amount_paid": 1, "due_date": 1, "status": 1},
        )
    )
    collection_documents = list(
        collection_record_collection.find(
            {"owner_user_id": owner_id, "customer_id": {"$in": customer_ids}},
            {"customer_id": 1, "collected_at": 1, "collected_by_name": 1},
        ).sort("collected_at", -1)
    )

    for installment in installment_documents:
        customer_id = installment["customer_id"]
        metric = metrics.get(customer_id)
        if metric is None:
            continue
        amount_due = float(installment.get("amount_due", 0))
        amount_paid = float(installment.get("amount_paid", 0))
        metric["total_collected"] = float(metric["total_collected"]) + amount_paid
        if _is_installment_overdue(installment, now):
            metric["overdue_installments"] = int(metric["overdue_installments"]) + 1
            metric["overdue_amount"] = float(metric["overdue_amount"]) + max(amount_due - amount_paid, 0)

        status = str(installment.get("status", "pending"))
        remaining = max(amount_due - amount_paid, 0)
        if remaining > 0 and status in {"pending", "partial"}:
            d_only = _installment_due_date(installment)
            if d_only.year == now.year and d_only.month == now.month:
                metric["due_this_month_installments"] = int(metric["due_this_month_installments"]) + 1
                metric["due_this_month_amount"] = float(metric["due_this_month_amount"]) + remaining
            if d_only.year == now.year:
                metric["due_this_year_installments"] = int(metric["due_this_year_installments"]) + 1
                metric["due_this_year_amount"] = float(metric["due_this_year_amount"]) + remaining
            if d_only == now.date():
                metric["due_today_installments"] = int(metric["due_today_installments"]) + 1
                metric["due_today_amount"] = float(metric["due_today_amount"]) + remaining

    for record in collection_documents:
        customer_id = record["customer_id"]
        metric = metrics.get(customer_id)
        if metric is None or metric["last_collected_at"] is not None:
            continue
        metric["last_collected_at"] = record.get("collected_at")
        metric["last_collected_by_name"] = record.get("collected_by_name")

    return metrics


def _enrich_customer(document: dict, metrics: dict[str, object] | None = None) -> dict:
    payload = dict(document)
    metrics = metrics or {}
    payload["overdue_installments"] = int(metrics.get("overdue_installments", 0))
    payload["overdue_amount"] = float(metrics.get("overdue_amount", 0))
    payload["due_this_month_installments"] = int(metrics.get("due_this_month_installments", 0))
    payload["due_this_month_amount"] = float(metrics.get("due_this_month_amount", 0))
    payload["due_this_year_installments"] = int(metrics.get("due_this_year_installments", 0))
    payload["due_this_year_amount"] = float(metrics.get("due_this_year_amount", 0))
    payload["due_today_installments"] = int(metrics.get("due_today_installments", 0))
    payload["due_today_amount"] = float(metrics.get("due_today_amount", 0))
    payload["total_collected"] = float(metrics.get("total_collected", 0))
    payload["last_collected_at"] = metrics.get("last_collected_at")
    payload["last_collected_by_name"] = metrics.get("last_collected_by_name")
    return payload


def _build_calendar_entries(
    installments: list[dict],
    latest_collection_by_installment: dict[str, dict],
    latest_anchor_collection_by_installment: dict[str, dict],
    now: datetime,
) -> list[InstallmentCalendarEntry]:
    calendar: list[InstallmentCalendarEntry] = []
    carried_balance = 0.0

    for item in installments:
        installment_id = str(item["_id"])
        own_remaining = max(float(item["amount_due"]) - float(item.get("amount_paid", 0)), 0)
        if item.get("status") != "paid":
            carried_balance += own_remaining
        else:
            carried_balance = max(carried_balance - float(item["amount_due"]), 0)

        latest_record = latest_collection_by_installment.get(installment_id, {})
        latest_anchor_record = latest_anchor_collection_by_installment.get(installment_id, {})
        display_record = latest_record or latest_anchor_record
        calendar.append(
            InstallmentCalendarEntry(
                installment_id=installment_id,
                due_date=item["due_date"].date(),
                amount_due=float(item["amount_due"]),
                amount_paid=float(item.get("amount_paid", 0)),
                amount_remaining=carried_balance if item.get("status") != "paid" else 0,
                status=item["status"],
                is_overdue=_is_installment_overdue(item, now),
                last_payment_mode=display_record.get("payment_mode"),
                last_collected_at=display_record.get("collected_at"),
                last_collected_by_name=display_record.get("collected_by_name"),
                latest_payment_event_amount=float(latest_anchor_record.get("batch_total_amount")) if latest_anchor_record.get("batch_total_amount") is not None else None,
                latest_payment_cover_count=int(latest_anchor_record.get("covered_installment_count")) if latest_anchor_record.get("covered_installment_count") is not None else None,
            )
        )

    return calendar


def _installment_delta(payment_type: str) -> timedelta:
    if payment_type == "daily":
        return timedelta(days=1)
    if payment_type == "weekly":
        return timedelta(days=7)
    if payment_type == "monthly":
        return timedelta(days=30)
    return timedelta(days=365)


def _create_installments_for_customer(
    owner_user_id: str,
    village_id: str,
    customer_id: str,
    payment_type: str,
    installment_amount: float,
    installment_count: int,
    installment_collection: Collection,
) -> None:
    now = datetime.now(UTC)
    step = _installment_delta(payment_type)
    documents = []

    for index in range(installment_count):
        documents.append(
            {
                "_id": f"ins-{uuid4().hex[:16]}",
                "owner_user_id": owner_user_id,
                "customer_id": customer_id,
                "village_id": village_id,
                "due_date": now + step * index,
                "amount_due": installment_amount,
                "amount_paid": 0.0,
                "status": "pending",
            }
        )

    if documents:
        installment_collection.insert_many(documents)


_FINANCE_SCOPES = frozenset({"daily", "weekly", "monthly", "yearly"})


@router.get("/villages", response_model=list[VillagePublic])
def list_villages(
    current_user: UserInDB = Depends(get_current_user),
    village_collection: Collection = Depends(get_village_collection),
    customer_collection: Collection = Depends(get_customer_collection),
    finance_scope: str = Query(default="weekly", description="Workspace filter: daily|weekly|monthly|yearly"),
) -> list[VillagePublic]:
    owner_id = _effective_owner_id(current_user)
    if finance_scope not in _FINANCE_SCOPES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid finance_scope")
    villages = list(village_collection.find(villages_mongo_filter(owner_id, finance_scope)).sort("day", 1))
    results: list[VillagePublic] = []

    for village in villages:
        customer_count = customer_collection.count_documents({
            "owner_user_id": owner_id,
            "village_id": str(village["_id"]),
        })
        results.append(_serialize_village(village, customer_count))

    return results


@router.post("/villages", response_model=VillagePublic, status_code=status.HTTP_201_CREATED)
def create_village(
    payload: VillageCreate,
    current_user: UserInDB = Depends(get_current_user),
    village_collection: Collection = Depends(get_village_collection),
) -> VillagePublic:
    _require_write_access(current_user)
    now = datetime.now(UTC)
    document = {
        "_id": f"vil-{uuid4().hex[:12]}",
        "owner_user_id": current_user.id,
        "name": payload.name.strip(),
        "day": payload.day.strip(),
        "finance_scope": payload.finance_scope,
        "created_at": now,
        "updated_at": now,
    }
    village_collection.insert_one(document)
    return _serialize_village(document)


@router.put("/villages/{village_id}", response_model=VillagePublic)
def update_village(
    village_id: str,
    payload: VillageUpdate,
    current_user: UserInDB = Depends(get_current_user),
    village_collection: Collection = Depends(get_village_collection),
    customer_collection: Collection = Depends(get_customer_collection),
) -> VillagePublic:
    _require_write_access(current_user)
    result = village_collection.find_one_and_update(
        {"_id": village_id, "owner_user_id": current_user.id},
        {"$set": {"name": payload.name.strip(), "day": payload.day.strip(), "updated_at": datetime.now(UTC)}},
        return_document=True,
    )
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Village not found")
    customer_count = customer_collection.count_documents({"owner_user_id": current_user.id, "village_id": village_id})
    return _serialize_village(result, customer_count)


@router.delete("/villages/{village_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_village(
    village_id: str,
    current_user: UserInDB = Depends(get_current_user),
    village_collection: Collection = Depends(get_village_collection),
    customer_collection: Collection = Depends(get_customer_collection),
    installment_collection: Collection = Depends(get_installment_collection),
) -> None:
    _require_write_access(current_user)
    customer_ids = [doc["_id"] for doc in customer_collection.find({"owner_user_id": current_user.id, "village_id": village_id}, {"_id": 1})]
    if customer_ids:
        installment_collection.delete_many({"owner_user_id": current_user.id, "customer_id": {"$in": customer_ids}})
        customer_collection.delete_many({"owner_user_id": current_user.id, "village_id": village_id})

    result = village_collection.delete_one({"_id": village_id, "owner_user_id": current_user.id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Village not found")


@router.get("/villages/{village_id}/customers", response_model=list[CustomerPublic])
def list_customers_for_village(
    village_id: str,
    current_user: UserInDB = Depends(get_current_user),
    customer_collection: Collection = Depends(get_customer_collection),
    installment_collection: Collection = Depends(get_installment_collection),
    collection_record_collection: Collection = Depends(get_collection_record_collection),
) -> list[CustomerPublic]:
    owner_id = _effective_owner_id(current_user)
    documents = list(customer_collection.find({"owner_user_id": owner_id, "village_id": village_id}).sort("created_at", -1))
    metrics_by_customer = _build_customer_metrics(
        owner_id,
        [str(document["_id"]) for document in documents],
        installment_collection,
        collection_record_collection,
    )
    return [_serialize_customer(_enrich_customer(document, metrics_by_customer.get(str(document["_id"])))) for document in documents]


@router.post("/villages/{village_id}/customers", response_model=CustomerPublic, status_code=status.HTTP_201_CREATED)
def create_customer(
    village_id: str,
    payload: CustomerCreate,
    current_user: UserInDB = Depends(get_current_user),
    village_collection: Collection = Depends(get_village_collection),
    customer_collection: Collection = Depends(get_customer_collection),
    installment_collection: Collection = Depends(get_installment_collection),
) -> CustomerPublic:
    _require_write_access(current_user)
    village = village_collection.find_one({"_id": village_id, "owner_user_id": current_user.id})
    if village is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Village not found")

    if current_user.subscription_tier == "pending":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "message": "Choose a plan before adding customers.",
                "code": "SUBSCRIPTION_REQUIRED",
            },
        )
    limit = customer_limit_for_tier(current_user.subscription_tier)
    existing_count = customer_collection.count_documents({"owner_user_id": current_user.id})
    if existing_count >= limit:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "message": "You have reached the customer limit for your plan. Upgrade to add more customers.",
                "code": "CUSTOMER_LIMIT_REACHED",
                "used": existing_count,
                "limit": limit,
            },
        )

    now = datetime.now(UTC)
    customer_id = f"cus-{uuid4().hex[:12]}"
    document = {
        "_id": customer_id,
        "owner_user_id": current_user.id,
        "village_id": village_id,
        "full_name": payload.full_name.strip(),
        "address": payload.address.strip(),
        "amount_lent": float(payload.amount_lent),
        "payment_type": payload.payment_type,
        "installment_amount": float(payload.installment_amount),
        "installment_count": int(payload.installment_count),
        "phone_number": payload.phone_number.strip(),
        "image_url": payload.image_url,
        "aadhar_number": payload.aadhar_number.strip(),
        "aadhar_image_url": payload.aadhar_image_url,
        "external_customer_id": f"{current_user.id}-{uuid4().hex[:6].upper()}",
        "created_at": now,
        "updated_at": now,
    }
    customer_collection.insert_one(document)
    _create_installments_for_customer(
        current_user.id,
        village_id,
        customer_id,
        payload.payment_type,
        float(payload.installment_amount),
        int(payload.installment_count),
        installment_collection,
    )
    return _serialize_customer(document)


@router.get("/customers/{customer_id}", response_model=CustomerDetailResponse)
def get_customer_detail(
    customer_id: str,
    current_user: UserInDB = Depends(get_current_user),
    customer_collection: Collection = Depends(get_customer_collection),
    installment_collection: Collection = Depends(get_installment_collection),
    collection_record_collection: Collection = Depends(get_collection_record_collection),
) -> CustomerDetailResponse:
    owner_id = _effective_owner_id(current_user)
    customer = customer_collection.find_one({"_id": customer_id, "owner_user_id": owner_id})
    if customer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")

    installments = list(installment_collection.find({"owner_user_id": owner_id, "customer_id": customer_id}).sort("due_date", 1))
    collection_history = list(collection_record_collection.find({"owner_user_id": owner_id, "customer_id": customer_id}).sort("collected_at", -1))
    latest_collection_by_installment: dict[str, dict] = {}
    latest_anchor_collection_by_installment: dict[str, dict] = {}
    for record in collection_history:
        latest_collection_by_installment.setdefault(record["installment_id"], record)
        anchor_installment_id = record.get("batch_anchor_installment_id")
        if anchor_installment_id:
            latest_anchor_collection_by_installment.setdefault(anchor_installment_id, record)

    now = datetime.now(UTC)
    paid = sum(1 for item in installments if item["status"] == "paid")
    skipped = sum(1 for item in installments if item["status"] == "skipped")
    left = sum(1 for item in installments if item["status"] in {"pending", "partial"})
    metrics_by_customer = _build_customer_metrics(owner_id, [customer_id], installment_collection, collection_record_collection)
    m = metrics_by_customer.get(customer_id, {})
    overdue_installments = int(m.get("overdue_installments", 0))
    overdue_amount = float(m.get("overdue_amount", 0))

    return CustomerDetailResponse(
        customer=_serialize_customer(_enrich_customer(customer, m)),
        installments_paid=paid,
        installments_left=left,
        installments_skipped=skipped,
        overdue_installments=overdue_installments,
        overdue_amount=overdue_amount,
        calendar=_build_calendar_entries(installments, latest_collection_by_installment, latest_anchor_collection_by_installment, now),
        collection_history=_group_collection_history(collection_history),
    )


@router.put("/customers/{customer_id}", response_model=CustomerPublic)
def update_customer(
    customer_id: str,
    payload: CustomerUpdate,
    current_user: UserInDB = Depends(get_current_user),
    customer_collection: Collection = Depends(get_customer_collection),
    installment_collection: Collection = Depends(get_installment_collection),
) -> CustomerPublic:
    _require_write_access(current_user)
    existing = customer_collection.find_one({"_id": customer_id, "owner_user_id": current_user.id})
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")

    update_document = {
        "full_name": payload.full_name.strip(),
        "address": payload.address.strip(),
        "amount_lent": float(payload.amount_lent),
        "payment_type": payload.payment_type,
        "installment_amount": float(payload.installment_amount),
        "installment_count": int(payload.installment_count),
        "phone_number": payload.phone_number.strip(),
        "image_url": payload.image_url,
        "aadhar_number": payload.aadhar_number.strip(),
        "aadhar_image_url": payload.aadhar_image_url,
        "updated_at": datetime.now(UTC),
    }

    customer_collection.update_one({"_id": customer_id, "owner_user_id": current_user.id}, {"$set": update_document})

    if (
        existing["payment_type"] != payload.payment_type
        or float(existing["installment_amount"]) != float(payload.installment_amount)
        or int(existing["installment_count"]) != int(payload.installment_count)
    ):
        installment_collection.delete_many({"owner_user_id": current_user.id, "customer_id": customer_id, "status": {"$in": ["pending", "partial", "skipped"]}})
        _create_installments_for_customer(
            current_user.id,
            existing["village_id"],
            customer_id,
            payload.payment_type,
            float(payload.installment_amount),
            int(payload.installment_count),
            installment_collection,
        )

    document = customer_collection.find_one({"_id": customer_id, "owner_user_id": current_user.id})
    return _serialize_customer(document)


@router.post("/installments/{installment_id}/collect", response_model=CollectionRecordPublic, status_code=status.HTTP_201_CREATED)
def collect_installment_payment(
    installment_id: str,
    payload: CollectionRecordCreate,
    current_user: UserInDB = Depends(get_current_user),
    installment_collection: Collection = Depends(get_installment_collection),
    collection_record_collection: Collection = Depends(get_collection_record_collection),
) -> CollectionRecordPublic:
    owner_id = _effective_owner_id(current_user)
    installment = installment_collection.find_one({"_id": installment_id, "owner_user_id": owner_id})
    if installment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Installment not found")

    relevant_installments = list(
        installment_collection.find(
            {
                "owner_user_id": owner_id,
                "customer_id": installment["customer_id"],
                "due_date": {"$lte": installment["due_date"]},
            }
        ).sort("due_date", 1)
    )
    unpaid_installments = [
        item for item in relevant_installments if max(float(item.get("amount_due", 0)) - float(item.get("amount_paid", 0)), 0) > 0
    ]
    remaining = sum(max(float(item.get("amount_due", 0)) - float(item.get("amount_paid", 0)), 0) for item in unpaid_installments)
    if remaining <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This installment is already fully paid.")
    if payload.amount_paid > remaining:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Only {remaining:.2f} is pending up to this installment.")

    amount_to_allocate = float(payload.amount_paid)
    batch_id = f"bat-{uuid4().hex[:12]}"
    covered_installment_ids: list[str] = []
    collected_at = datetime.now(UTC)
    selected_record: dict | None = None
    last_record: dict | None = None

    for item in unpaid_installments:
        item_amount_due = float(item.get("amount_due", 0))
        item_already_paid = float(item.get("amount_paid", 0))
        item_remaining = max(item_amount_due - item_already_paid, 0)
        if item_remaining <= 0 or amount_to_allocate <= 0:
            continue

        applied_amount = min(item_remaining, amount_to_allocate)
        updated_amount_paid = item_already_paid + applied_amount
        next_status = "paid" if updated_amount_paid >= item_amount_due else "partial"
        covered_installment_ids.append(str(item["_id"]))
        installment_collection.update_one(
            {"_id": item["_id"], "owner_user_id": owner_id},
            {"$set": {"amount_paid": updated_amount_paid, "status": next_status}},
        )

        record = {
            "_id": f"col-{uuid4().hex[:12]}",
            "collection_batch_id": batch_id,
            "batch_anchor_installment_id": installment_id,
            "owner_user_id": owner_id,
            "village_id": item["village_id"],
            "customer_id": item["customer_id"],
            "installment_id": str(item["_id"]),
            "amount_paid": applied_amount,
            "batch_total_amount": float(payload.amount_paid),
            "covered_installment_count": 0,
            "covered_installment_ids": [],
            "payment_mode": payload.payment_mode,
            "collected_by_user_id": current_user.id,
            "collected_by_name": current_user.full_name,
            "collected_at": collected_at,
            "status_after_payment": next_status,
            "note": payload.note.strip() if payload.note else None,
        }
        collection_record_collection.insert_one(record)
        last_record = record
        if str(item["_id"]) == installment_id:
            selected_record = record

        amount_to_allocate -= applied_amount

    for installment_record in collection_record_collection.find({"collection_batch_id": batch_id}):
        collection_record_collection.update_one(
            {"_id": installment_record["_id"]},
            {"$set": {"covered_installment_count": len(covered_installment_ids), "covered_installment_ids": covered_installment_ids}},
        )
        if selected_record and installment_record["_id"] == selected_record["_id"]:
            selected_record["covered_installment_count"] = len(covered_installment_ids)
            selected_record["covered_installment_ids"] = covered_installment_ids
        if last_record and installment_record["_id"] == last_record["_id"]:
            last_record["covered_installment_count"] = len(covered_installment_ids)
            last_record["covered_installment_ids"] = covered_installment_ids

    response_record = selected_record or last_record
    if response_record is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No payment could be allocated.")
    return _serialize_collection_record(response_record)


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


@router.get("/collections/report", response_model=CollectionsReportResponse)
def collections_report(
    finance_scope: str = Query(default="weekly"),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    village_id: str | None = Query(default=None),
    customer_id: str | None = Query(default=None),
    payment_mode: str | None = Query(default=None),
    current_user: UserInDB = Depends(get_current_user),
    village_collection: Collection = Depends(get_village_collection),
    customer_collection: Collection = Depends(get_customer_collection),
    collection_record_collection: Collection = Depends(get_collection_record_collection),
) -> CollectionsReportResponse:
    """Aggregated collections by day (line chart) and per-batch rows with optional filters."""
    owner_id = _effective_owner_id(current_user)
    allowed_scopes = {"daily", "weekly", "monthly", "yearly"}
    if finance_scope not in allowed_scopes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid finance_scope")

    today = datetime.now(UTC).date()
    end_day = date_to or today
    start_day = date_from or (end_day - timedelta(days=29))
    if start_day > end_day:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="date_from must be on or before date_to")

    village_ids = [str(document["_id"]) for document in village_collection.find(villages_mongo_filter(owner_id, finance_scope), {"_id": 1})]
    if not village_ids:
        return CollectionsReportResponse(total_amount=0.0, transaction_count=0, series=[], transactions=[])

    if village_id is not None:
        if village_id not in village_ids:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Village not found")
        allowed_village_ids = [village_id]
    else:
        allowed_village_ids = village_ids

    start_dt = datetime.combine(start_day, time.min, tzinfo=UTC)
    end_dt = datetime.combine(end_day, time(23, 59, 59, 999999), tzinfo=UTC)

    match_filter: dict = {
        "owner_user_id": owner_id,
        "village_id": {"$in": allowed_village_ids},
        "collected_at": {"$gte": start_dt, "$lte": end_dt},
    }

    if customer_id is not None:
        customer = customer_collection.find_one({"_id": customer_id, "owner_user_id": owner_id})
        if customer is None or str(customer["village_id"]) not in allowed_village_ids:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")
        match_filter["customer_id"] = customer_id

    if payment_mode is not None:
        if payment_mode not in _VALID_PAYMENT_MODES:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payment_mode")
        match_filter["payment_mode"] = payment_mode

    pipeline = [
        {"$match": match_filter},
        {
            "$group": {
                "_id": "$collection_batch_id",
                "collected_at": {"$max": "$collected_at"},
                "amount_paid": {"$sum": "$amount_paid"},
                "village_id": {"$first": "$village_id"},
                "customer_id": {"$first": "$customer_id"},
                "payment_mode": {"$first": "$payment_mode"},
                "collected_by_name": {"$first": "$collected_by_name"},
                "note": {"$first": "$note"},
            }
        },
        {"$sort": {"collected_at": -1}},
        {"$limit": 500},
    ]
    grouped = list(collection_record_collection.aggregate(pipeline))

    by_day: dict[str, float] = {}
    by_day_count: dict[str, int] = {}
    total_amount = 0.0

    customer_ids = sorted({str(row["customer_id"]) for row in grouped})
    village_id_set = sorted({str(row["village_id"]) for row in grouped})

    customer_docs = (
        list(customer_collection.find({"_id": {"$in": customer_ids}}, {"full_name": 1})) if customer_ids else []
    )
    village_docs = (
        list(village_collection.find({"_id": {"$in": village_id_set}}, {"name": 1})) if village_id_set else []
    )

    customer_names = {str(doc["_id"]): str(doc.get("full_name", "")) for doc in customer_docs}
    village_names = {str(doc["_id"]): str(doc.get("name", "")) for doc in village_docs}

    transactions: list[CollectionTransactionRow] = []
    for row in grouped:
        cat_raw = row["collected_at"]
        cat = _ensure_utc(cat_raw) if isinstance(cat_raw, datetime) else datetime.now(UTC)
        day_key = cat.date().isoformat()

        amt = float(row["amount_paid"])
        total_amount += amt
        by_day[day_key] = by_day.get(day_key, 0.0) + amt
        by_day_count[day_key] = by_day_count.get(day_key, 0) + 1

        cid = str(row["customer_id"])
        vid = str(row["village_id"])
        mode = row["payment_mode"]
        if mode not in _VALID_PAYMENT_MODES:
            mode = "cash"

        transactions.append(
            CollectionTransactionRow(
                id=str(row["_id"]),
                collected_at=cat,
                amount_paid=amt,
                payment_mode=mode,  # type: ignore[arg-type]
                collected_by_name=str(row.get("collected_by_name", "")),
                customer_id=cid,
                customer_full_name=customer_names.get(cid, cid),
                village_id=vid,
                village_name=village_names.get(vid, vid),
                note=row.get("note"),
            )
        )

    series: list[CollectionTimeseriesPoint] = []
    cursor_day = start_day
    while cursor_day <= end_day:
        key = cursor_day.isoformat()
        series.append(
            CollectionTimeseriesPoint(
                date=cursor_day,
                amount=float(by_day.get(key, 0.0)),
                count=int(by_day_count.get(key, 0)),
            )
        )
        cursor_day += timedelta(days=1)

    return CollectionsReportResponse(
        total_amount=total_amount,
        transaction_count=len(transactions),
        series=series,
        transactions=transactions,
    )


@router.delete("/customers/{customer_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_customer(
    customer_id: str,
    current_user: UserInDB = Depends(get_current_user),
    customer_collection: Collection = Depends(get_customer_collection),
    installment_collection: Collection = Depends(get_installment_collection),
) -> None:
    _require_write_access(current_user)
    installment_collection.delete_many({"owner_user_id": current_user.id, "customer_id": customer_id})
    result = customer_collection.delete_one({"_id": customer_id, "owner_user_id": current_user.id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")