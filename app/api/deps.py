from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pymongo.collection import Collection

from app.core.config import get_settings
from app.db.mongodb import collections_collection, customers_collection, installments_collection, users_collection, villages_collection
from app.models.user import UserInDB

bearer_scheme = HTTPBearer(auto_error=False)


def get_user_collection() -> Collection:
    return users_collection


def get_village_collection() -> Collection:
    return villages_collection


def get_customer_collection() -> Collection:
    return customers_collection


def get_installment_collection() -> Collection:
    return installments_collection


def get_collection_record_collection() -> Collection:
    return collections_collection


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    collection: Collection = Depends(get_user_collection),
) -> UserInDB:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    settings = get_settings()

    try:
        payload = jwt.decode(credentials.credentials, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        subject = payload.get("sub")
    except JWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token") from exc

    if not subject:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token")

    document = collection.find_one({"_id": subject})
    if document is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    return UserInDB.from_mongo(document)
