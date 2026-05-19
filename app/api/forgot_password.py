import random
import string
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from pymongo.collection import Collection

from app.api.deps import get_otp_collection, get_user_collection
from app.core.email import send_otp_email
from app.core.security import hash_password

router = APIRouter(prefix="/forgot-password", tags=["forgot-password"])


class SendOtpRequest(BaseModel):
    identifier: str = Field(min_length=3, description="Email address or phone number")


class VerifyOtpRequest(BaseModel):
    identifier: str = Field(min_length=3)
    otp: str = Field(min_length=6, max_length=6)


class ResetPasswordRequest(BaseModel):
    identifier: str = Field(min_length=3)
    otp: str = Field(min_length=6, max_length=6)
    new_password: str = Field(min_length=6)


def _generate_otp(length: int = 6) -> str:
    return "".join(random.choices(string.digits, k=length))


def _find_user(identifier: str, collection: Collection):
    identifier = identifier.strip()
    return collection.find_one({
        "$or": [
            {"email": identifier.lower()},
            {"phone_number": identifier},
        ]
    })


@router.post("/send-otp")
def send_otp(
    payload: SendOtpRequest,
    user_collection: Collection = Depends(get_user_collection),
    otp_collection: Collection = Depends(get_otp_collection),
) -> dict:
    document = _find_user(payload.identifier, user_collection)
    if not document:
        # Return success even if user not found to avoid user enumeration
        return {"message": "If an account exists, an OTP has been sent to the registered email."}

    otp = _generate_otp()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)

    otp_collection.replace_one(
        {"user_id": str(document["_id"])},
        {"user_id": str(document["_id"]), "otp": otp, "expires_at": expires_at},
        upsert=True,
    )

    try:
        send_otp_email(
            to_email=document["email"],
            otp=otp,
            full_name=document.get("full_name", "User"),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send OTP email. Please try again.",
        ) from exc

    return {"message": "OTP sent to your registered email address."}


@router.post("/verify-otp")
def verify_otp(
    payload: VerifyOtpRequest,
    user_collection: Collection = Depends(get_user_collection),
    otp_collection: Collection = Depends(get_otp_collection),
) -> dict:
    document = _find_user(payload.identifier, user_collection)
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")

    user_id = str(document["_id"])
    otp_doc = otp_collection.find_one({"user_id": user_id})

    if not otp_doc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="OTP not found. Please request a new one.")

    if otp_doc.get("otp") != payload.otp:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Incorrect OTP. Please try again.")

    if datetime.now(timezone.utc) > otp_doc["expires_at"].replace(tzinfo=timezone.utc):
        otp_collection.delete_one({"user_id": user_id})
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="OTP has expired. Please request a new one.")

    return {"message": "OTP verified."}


@router.post("/reset")
def reset_password(
    payload: ResetPasswordRequest,
    user_collection: Collection = Depends(get_user_collection),
    otp_collection: Collection = Depends(get_otp_collection),
) -> dict:
    document = _find_user(payload.identifier, user_collection)
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")

    user_id = str(document["_id"])
    otp_doc = otp_collection.find_one({"user_id": user_id})

    if not otp_doc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="OTP not found. Please request a new one.")

    if otp_doc.get("otp") != payload.otp:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Incorrect OTP.")

    if datetime.now(timezone.utc) > otp_doc["expires_at"].replace(tzinfo=timezone.utc):
        otp_collection.delete_one({"user_id": user_id})
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="OTP has expired. Please request a new one.")

    user_collection.update_one(
        {"_id": document["_id"]},
        {"$set": {"hashed_password": hash_password(payload.new_password)}},
    )
    otp_collection.delete_one({"user_id": user_id})

    return {"message": "Password reset successfully."}
