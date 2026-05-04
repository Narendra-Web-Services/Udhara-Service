from fastapi import APIRouter, Depends, HTTPException, status
from pymongo.collection import Collection

from app.api.deps import get_current_user, get_user_collection
from app.models.user import UserInDB

router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("/collaborators", response_model=list[dict])
def list_collaborators(
    current_user: UserInDB = Depends(get_current_user),
    collection: Collection = Depends(get_user_collection),
) -> list[dict]:
    """Return all collaborator (Normal user) accounts linked to the current admin."""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admin accounts have collaborators.",
        )
    docs = collection.find(
        {"role": "customer", "linked_admin_id": current_user.id},
        {"_id": 1, "full_name": 1, "phone_number": 1, "email": 1},
    )
    return [
        {
            "id": str(d["_id"]),
            "full_name": d["full_name"],
            "phone_number": d.get("phone_number", ""),
            "email": d.get("email", ""),
        }
        for d in docs
    ]


@router.patch("/collaborator-settings", response_model=dict)
def update_collaborator_settings(
    current_user: UserInDB = Depends(get_current_user),
    collection: Collection = Depends(get_user_collection),
) -> dict:
    """Toggle whether the current admin allows new collaborators to link to them."""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admin accounts can change this setting.",
        )
    new_value = not current_user.allow_collaborators
    collection.update_one({"_id": current_user.id}, {"$set": {"allow_collaborators": new_value}})
    return {"allow_collaborators": new_value}


@router.delete("/collaborators/{collaborator_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_collaborator(
    collaborator_id: str,
    current_user: UserInDB = Depends(get_current_user),
    collection: Collection = Depends(get_user_collection),
) -> None:
    """Permanently remove a worker account linked to this admin."""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admin accounts can remove collaborators.",
        )
    if collaborator_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot delete your own admin account.",
        )
    result = collection.delete_one(
        {
            "_id": collaborator_id,
            "role": "customer",
            "linked_admin_id": current_user.id,
        }
    )
    if result.deleted_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Collaborator not found or not linked to your account.",
        )
