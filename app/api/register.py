from fastapi import APIRouter, Depends, HTTPException, status
from pymongo.collection import Collection
from uuid import uuid4

from app.api.deps import get_customer_collection, get_user_collection
from app.core.security import create_access_token, hash_password, verify_password
from app.core.access_profile import build_user_public, subscription_is_active
from app.core.subscription_catalog import collaborator_limit_for_tier, LEGACY_TIER_MAP
from app.models.user import AuthResponse, RegisterRequest, UserInDB

router = APIRouter(prefix="/register", tags=["register"])


@router.get("/admins", response_model=list[dict])
def list_admins(collection: Collection = Depends(get_user_collection)) -> list[dict]:
    """Return admin accounts that allow collaborators."""
    docs = collection.find(
        {"role": "admin", "allow_collaborators": {"$ne": False}},
        {"_id": 1, "full_name": 1, "phone_number": 1},
    )
    return [{"id": str(d["_id"]), "full_name": d["full_name"], "phone_number": d.get("phone_number", "")} for d in docs]


@router.post("", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(
    payload: RegisterRequest,
    collection: Collection = Depends(get_user_collection),
    customer_collection: Collection = Depends(get_customer_collection),
) -> AuthResponse:
    email = payload.email.strip().lower()
    phone_number = payload.phone_number.strip()

    existing_user = collection.find_one(
        {"$or": [{"email": email}, {"phone_number": phone_number}]},
        {"email": 1, "phone_number": 1},
    )
    if existing_user is not None:
        if existing_user.get("email") == email:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This email is already registered.")
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This phone number is already registered.")

    # For workers linked to an admin, verify the admin password and enforce plan limits.
    if payload.role == "customer" and payload.linked_admin_id:
        if not payload.admin_password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Admin password is required to register as a worker.",
            )
        admin_doc = collection.find_one({"_id": payload.linked_admin_id, "role": "admin"})
        if admin_doc is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Selected admin account not found.",
            )
        if not admin_doc.get("allow_collaborators", True):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This admin is not accepting new workers.",
            )
        if not verify_password(payload.admin_password, admin_doc["hashed_password"]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Admin password is incorrect. Please ask your admin for the correct password.",
            )

        # Check admin subscription is active (expired owners cannot add workers).
        admin_user = UserInDB.from_mongo(admin_doc)
        if not subscription_is_active(admin_user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="The admin's subscription has expired. Ask your admin to renew before adding workers.",
            )

        # Enforce worker (collaborator) limit for the admin's current plan.
        admin_tier = LEGACY_TIER_MAP.get(admin_user.subscription_tier, admin_user.subscription_tier)
        collab_limit = collaborator_limit_for_tier(admin_tier)
        if collab_limit == 0:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="The admin's current plan does not support adding workers. They need to upgrade.",
            )
        current_collab_count = collection.count_documents(
            {"role": "customer", "linked_admin_id": payload.linked_admin_id}
        )
        if current_collab_count >= collab_limit:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"This admin has reached the worker limit ({collab_limit}) for their current plan. "
                    "Ask your admin to upgrade to add more workers."
                ),
            )

    new_session_id = str(uuid4())
    user_document = {
        "_id": payload.user_id.strip(),
        "full_name": payload.full_name.strip(),
        "email": email,
        "phone_number": phone_number,
        "role": payload.role,
        "has_subscription": False,
        "subscription_tier": "pending",
        "billing_period": None,
        "hashed_password": hash_password(payload.password),
        "linked_admin_id": payload.linked_admin_id,
        "session_id": new_session_id,
    }
    collection.insert_one(user_document)

    access_token = create_access_token(user_document["_id"], new_session_id)
    inserted = collection.find_one({"_id": user_document["_id"]})
    if inserted is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Registration failed.")
    registered_user = UserInDB.from_mongo(inserted)
    return AuthResponse(
        access_token=access_token,
        user=build_user_public(registered_user, collection, customer_collection),
    )
