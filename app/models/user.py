from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.finance import DelayedCustomerPublic


class LoginRequest(BaseModel):
    identifier: str = Field(min_length=3, description="Email address or phone number")
    password: str = Field(min_length=6)


class RegisterRequest(BaseModel):
    user_id: str = Field(min_length=3)
    full_name: str = Field(min_length=2)
    email: EmailStr
    phone_number: str = Field(min_length=8)
    password: str = Field(min_length=6)
    role: Literal["admin", "customer"] = "customer"
    linked_admin_id: Optional[str] = None
    admin_password: Optional[str] = None


class UserPublic(BaseModel):
    id: str
    full_name: str
    email: EmailStr
    phone_number: str
    role: Literal["admin", "customer"]
    has_subscription: bool = False
    linked_admin_id: Optional[str] = None
    allow_collaborators: bool = True


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserPublic


class DashboardCard(BaseModel):
    title: str
    value: str
    description: str


class DashboardSummaryMetric(BaseModel):
    label: str
    value: str
    trend: str
    tone: Literal["positive", "negative", "neutral"]


class DashboardDailyCard(BaseModel):
    day: str
    invested: str
    returns: str
    profit_or_loss: str
    tone: Literal["positive", "negative", "neutral"]


class DashboardResponse(BaseModel):
    message: str
    user_id: str
    role: Literal["admin", "customer"]
    has_subscription: bool
    summary: list[DashboardSummaryMetric]
    daily_cards: list[DashboardDailyCard]
    attention_required_count: int = 0
    overdue_amount: str = "₹0"
    delayed_customers: list[DelayedCustomerPublic] = []


class UserInDB(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")
    full_name: str
    email: EmailStr
    phone_number: str
    role: Literal["admin", "customer"]
    has_subscription: bool = False
    hashed_password: str
    linked_admin_id: Optional[str] = None
    allow_collaborators: bool = True
    session_id: Optional[str] = None

    @classmethod
    def from_mongo(cls, document: dict[str, Any]) -> "UserInDB":
        payload = {**document, "_id": str(document["_id"])}
        return cls.model_validate(payload)
