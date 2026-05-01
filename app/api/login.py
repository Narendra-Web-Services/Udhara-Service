from fastapi import APIRouter, Depends, HTTPException, status
from pymongo.collection import Collection

from app.api.deps import get_user_collection
from app.core.security import create_access_token, verify_password
from app.models.user import AuthResponse, LoginRequest, UserInDB, UserPublic

router = APIRouter(prefix="/login", tags=["login"])


@router.post("", response_model=AuthResponse)
def login(payload: LoginRequest, collection: Collection = Depends(get_user_collection)) -> AuthResponse:
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

    # Collaborators inherit the linked admin's subscription status
    effective_subscription = user.has_subscription
    if user.role == "customer" and user.linked_admin_id:
        admin_doc = collection.find_one({"_id": user.linked_admin_id}, {"has_subscription": 1})
        if admin_doc:
            effective_subscription = bool(admin_doc.get("has_subscription", False))

    token = create_access_token(user.id)
    return AuthResponse(
        access_token=token,
        user=UserPublic(
            id=user.id,
            full_name=user.full_name,
            email=user.email,
            phone_number=user.phone_number,
            role=user.role,
            has_subscription=effective_subscription,
            linked_admin_id=user.linked_admin_id,
            allow_collaborators=user.allow_collaborators,
        ),
    )