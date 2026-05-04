from fastapi import APIRouter, Depends, HTTPException, status
from pymongo.collection import Collection
from uuid import uuid4

from app.api.deps import get_customer_collection, get_user_collection
from app.core.access_profile import build_user_public
from app.core.security import create_access_token, verify_password
from app.models.user import AuthResponse, LoginRequest, UserInDB

router = APIRouter(prefix="/login", tags=["login"])


@router.post("", response_model=AuthResponse)
def login(
    payload: LoginRequest,
    collection: Collection = Depends(get_user_collection),
    customer_collection: Collection = Depends(get_customer_collection),
) -> AuthResponse:
    identifier = payload.identifier.strip().lower()
    document = collection.find_one(
        {
            "$or": [
                {"email": identifier},
                {"phone_number": payload.identifier.strip()},
            ]
        }
    )
    if document is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    user = UserInDB.from_mongo(document)
    if not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    new_session_id = str(uuid4())
    collection.update_one({"_id": user.id}, {"$set": {"session_id": new_session_id}})

    token = create_access_token(user.id, new_session_id)
    return AuthResponse(
        access_token=token,
        user=build_user_public(user, collection, customer_collection),
    )