from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pymongo.collection import Collection

from app.api.deps import get_collection_record_collection, get_current_user, get_customer_collection, get_installment_collection, get_village_collection
from app.models.finance import DelayedCustomerPublic
from app.models.user import DashboardDailyCard, DashboardResponse, DashboardSummaryMetric, UserInDB

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _format_currency(value: int) -> str:
    amount = abs(int(round(value)))
    digits = str(amount)

    if len(digits) > 3:
        last_three = digits[-3:]
        remaining = digits[:-3]
        groups: list[str] = []
        while len(remaining) > 2:
            groups.insert(0, remaining[-2:])
            remaining = remaining[:-2]
        if remaining:
            groups.insert(0, remaining)
        digits = ",".join([*groups, last_three])

    sign = "-" if value < 0 else ""
    return f"{sign}₹{digits}"



def _build_daily_values(
    village_collection: Collection,
    customer_collection: Collection,
    installment_collection: Collection,
    owner_id: str,
) -> tuple[list[DashboardDailyCard], int, int]:
    village_documents = list(village_collection.find({"owner_user_id": owner_id}, {"_id": 1, "day": 1}))
    customer_documents = list(customer_collection.find({"owner_user_id": owner_id}, {"_id": 1, "village_id": 1, "amount_lent": 1}))
    installment_documents = list(installment_collection.find({"owner_user_id": owner_id}, {"customer_id": 1, "amount_paid": 1}))
    daily_map: dict[str, dict[str, float]] = {day: {"invested": 0.0, "returns": 0.0} for day in DAYS}
    village_day_by_id = {str(document["_id"]): document.get("day", "") for document in village_documents}
    customer_day_by_id: dict[str, str] = {}

    for document in customer_documents:
        day = village_day_by_id.get(str(document.get("village_id")), "")
        customer_id = str(document.get("_id"))
        customer_day_by_id[customer_id] = day
        if day in daily_map:
            daily_map[day]["invested"] += float(document.get("amount_lent", 0))

    for document in installment_documents:
        day = customer_day_by_id.get(str(document.get("customer_id")), "")
        if day in daily_map:
            daily_map[day]["returns"] += float(document.get("amount_paid", 0))

    invested_total = round(sum(values["invested"] for values in daily_map.values()))
    returns_total = round(sum(values["returns"] for values in daily_map.values()))

    cards: list[DashboardDailyCard] = []
    for day in DAYS:
        invested = round(daily_map[day]["invested"])
        returns = round(daily_map[day]["returns"])
        difference = returns - invested
        tone = "positive" if difference > 0 else "negative" if difference < 0 else "neutral"
        sign = "+" if difference > 0 else ""
        cards.append(
            DashboardDailyCard(
                day=day,
                invested=_format_currency(invested),
                returns=_format_currency(returns),
                profit_or_loss=f"{sign}{_format_currency(difference)}",
                tone=tone,
            )
        )
    return cards, invested_total, returns_total


def _build_summary(invested_total: int, returns_total: int) -> list[DashboardSummaryMetric]:
    profit_total = returns_total - invested_total
    margin = profit_total / invested_total if invested_total else 0
    profit_tone = "positive" if profit_total > 0 else "negative" if profit_total < 0 else "neutral"
    sign = "+" if profit_total > 0 else ""

    return [
        DashboardSummaryMetric(
            label="Amount invested",
            value=_format_currency(invested_total),
            trend="Total principal across all days",
            tone="neutral",
        ),
        DashboardSummaryMetric(
            label="Amount in returns",
            value=_format_currency(returns_total),
            trend="Total collected across all days",
            tone=profit_tone,
        ),
        DashboardSummaryMetric(
            label="Profit or loss",
            value=f"{sign}{_format_currency(profit_total)}",
            trend=f"Margin {margin * 100:.1f}%",
            tone=profit_tone,
        ),
    ]


def _build_delay_summary(
    village_collection: Collection,
    customer_collection: Collection,
    installment_collection: Collection,
    collection_record_collection: Collection,
    owner_id: str,
) -> tuple[int, int, list[DelayedCustomerPublic]]:
    now = datetime.now(UTC)
    villages = list(village_collection.find({"owner_user_id": owner_id}, {"_id": 1, "name": 1}))
    customers = list(customer_collection.find({"owner_user_id": owner_id}, {"_id": 1, "full_name": 1, "phone_number": 1, "village_id": 1}))
    customer_ids = [str(customer["_id"]) for customer in customers]
    village_name_by_id = {str(village["_id"]): village.get("name", "Unknown village") for village in villages}
    last_collection_by_customer: dict[str, dict] = {}
    delayed_map = {
        customer_id: {
            "overdue_installments": 0,
            "overdue_amount": 0.0,
        }
        for customer_id in customer_ids
    }

    if customer_ids:
        for record in collection_record_collection.find(
            {"owner_user_id": owner_id, "customer_id": {"$in": customer_ids}},
            {"customer_id": 1, "collected_at": 1, "collected_by_name": 1},
        ).sort("collected_at", -1):
            last_collection_by_customer.setdefault(record["customer_id"], record)

        for installment in installment_collection.find(
            {"owner_user_id": owner_id, "customer_id": {"$in": customer_ids}},
            {"customer_id": 1, "amount_due": 1, "amount_paid": 1, "due_date": 1, "status": 1},
        ):
            if installment.get("status") == "paid" or installment["due_date"].date() >= now.date():
                continue
            delayed_map[installment["customer_id"]]["overdue_installments"] += 1
            delayed_map[installment["customer_id"]]["overdue_amount"] += max(
                float(installment.get("amount_due", 0)) - float(installment.get("amount_paid", 0)),
                0,
            )

    delayed_customers: list[DelayedCustomerPublic] = []
    total_overdue_amount = 0
    attention_required_count = 0
    for customer in customers:
        customer_id = str(customer["_id"])
        overdue_installments = int(delayed_map[customer_id]["overdue_installments"])
        overdue_amount = round(float(delayed_map[customer_id]["overdue_amount"]))
        if overdue_installments <= 0:
            continue
        attention_required_count += 1
        total_overdue_amount += overdue_amount
        last_collection = last_collection_by_customer.get(customer_id)
        delayed_customers.append(
            DelayedCustomerPublic(
                customer_id=customer_id,
                full_name=customer.get("full_name", "Unknown customer"),
                village_name=village_name_by_id.get(str(customer.get("village_id")), "Unknown village"),
                phone_number=customer.get("phone_number", ""),
                overdue_installments=overdue_installments,
                overdue_amount=overdue_amount,
                last_collected_at=last_collection.get("collected_at") if last_collection else None,
                last_collected_by_name=last_collection.get("collected_by_name") if last_collection else None,
            )
        )

    delayed_customers.sort(key=lambda item: (item.overdue_amount, item.overdue_installments), reverse=True)
    return attention_required_count, total_overdue_amount, delayed_customers[:6]


@router.get("", response_model=DashboardResponse)
def dashboard(
    current_user: UserInDB = Depends(get_current_user),
    village_collection: Collection = Depends(get_village_collection),
    customer_collection: Collection = Depends(get_customer_collection),
    installment_collection: Collection = Depends(get_installment_collection),
    collection_record_collection: Collection = Depends(get_collection_record_collection),
) -> DashboardResponse:
    owner_id = current_user.linked_admin_id if (current_user.role == "customer" and current_user.linked_admin_id) else current_user.id
    daily_cards, invested_total, returns_total = _build_daily_values(
        village_collection,
        customer_collection,
        installment_collection,
        owner_id,
    )
    attention_required_count, overdue_amount, delayed_customers = _build_delay_summary(
        village_collection,
        customer_collection,
        installment_collection,
        collection_record_collection,
        owner_id,
    )

    return DashboardResponse(
        message=f"Welcome back, {current_user.full_name}.",
        user_id=current_user.id,
        role=current_user.role,
        has_subscription=current_user.has_subscription,
        summary=_build_summary(invested_total, returns_total),
        daily_cards=daily_cards,
        attention_required_count=attention_required_count,
        overdue_amount=_format_currency(overdue_amount),
        delayed_customers=delayed_customers,
    )