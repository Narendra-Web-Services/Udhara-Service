from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


PaymentType = Literal["daily", "weekly", "monthly", "yearly"]
InstallmentStatus = Literal["paid", "partial", "skipped", "pending"]
PaymentMode = Literal["phonepe", "gpay", "cash"]


class VillageCreate(BaseModel):
    name: str = Field(min_length=2)
    day: str = Field(min_length=3)


class VillageUpdate(BaseModel):
    name: str = Field(min_length=2)
    day: str = Field(min_length=3)


class VillagePublic(BaseModel):
    id: str
    owner_user_id: str
    name: str
    day: str
    customer_count: int = 0


class CustomerCreate(BaseModel):
    full_name: str = Field(min_length=2)
    address: str = Field(min_length=5)
    amount_lent: float = Field(gt=0)
    payment_type: PaymentType
    installment_amount: float = Field(gt=0)
    installment_count: int = Field(gt=0)
    phone_number: str = Field(min_length=8)
    image_url: str | None = None
    aadhar_number: str = Field(min_length=6)
    aadhar_image_url: str | None = None


class CustomerUpdate(CustomerCreate):
    pass


class CustomerPublic(BaseModel):
    id: str
    owner_user_id: str
    village_id: str
    full_name: str
    address: str
    amount_lent: float
    payment_type: PaymentType
    installment_amount: float
    installment_count: int
    phone_number: str
    image_url: str | None = None
    aadhar_number: str
    aadhar_image_url: str | None = None
    external_customer_id: str
    overdue_installments: int = 0
    overdue_amount: float = 0
    total_collected: float = 0
    last_collected_at: datetime | None = None
    last_collected_by_name: str | None = None


class CollectionRecordCreate(BaseModel):
    amount_paid: float = Field(gt=0)
    payment_mode: PaymentMode
    note: str | None = Field(default=None, max_length=240)


class CollectionRecordPublic(BaseModel):
    id: str
    collection_batch_id: str
    batch_anchor_installment_id: str
    owner_user_id: str
    village_id: str
    customer_id: str
    installment_id: str
    amount_paid: float
    covered_installment_count: int = 1
    covered_installment_ids: list[str] = []
    payment_mode: PaymentMode
    collected_by_user_id: str
    collected_by_name: str
    collected_at: datetime
    status_after_payment: InstallmentStatus
    note: str | None = None


class DelayedCustomerPublic(BaseModel):
    customer_id: str
    full_name: str
    village_name: str
    phone_number: str
    overdue_installments: int
    overdue_amount: float
    last_collected_at: datetime | None = None
    last_collected_by_name: str | None = None


class InstallmentCalendarEntry(BaseModel):
    installment_id: str
    due_date: date
    amount_due: float
    amount_paid: float
    amount_remaining: float
    status: InstallmentStatus
    is_overdue: bool = False
    last_payment_mode: PaymentMode | None = None
    last_collected_at: datetime | None = None
    last_collected_by_name: str | None = None
    latest_payment_event_amount: float | None = None
    latest_payment_cover_count: int | None = None


class CustomerDetailResponse(BaseModel):
    customer: CustomerPublic
    installments_paid: int
    installments_left: int
    installments_skipped: int
    overdue_installments: int
    overdue_amount: float
    calendar: list[InstallmentCalendarEntry]
    collection_history: list[CollectionRecordPublic]


class InstallmentInDB(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")
    owner_user_id: str
    customer_id: str
    village_id: str
    due_date: datetime
    amount_due: float
    amount_paid: float = 0
    status: InstallmentStatus = "pending"


class CollectionRecordInDB(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")
    collection_batch_id: str
    batch_anchor_installment_id: str
    owner_user_id: str
    village_id: str
    customer_id: str
    installment_id: str
    amount_paid: float
    covered_installment_count: int = 1
    covered_installment_ids: list[str] = []
    payment_mode: PaymentMode
    collected_by_user_id: str
    collected_by_name: str
    collected_at: datetime
    status_after_payment: InstallmentStatus
    note: str | None = None


class VillageInDB(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")
    owner_user_id: str
    name: str
    day: str
    created_at: datetime
    updated_at: datetime


class CustomerInDB(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")
    owner_user_id: str
    village_id: str
    full_name: str
    address: str
    amount_lent: float
    payment_type: PaymentType
    installment_amount: float
    installment_count: int
    phone_number: str
    image_url: str | None = None
    aadhar_number: str
    aadhar_image_url: str | None = None
    external_customer_id: str
    created_at: datetime
    updated_at: datetime
