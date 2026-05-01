from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import OperationFailure, PyMongoError

from app.core.config import get_settings

settings = get_settings()


def _build_client() -> MongoClient:
	client = MongoClient(settings.mongodb_url, serverSelectionTimeoutMS=5000)
	database = client[settings.mongodb_db]

	try:
		database.list_collection_names()
	except OperationFailure as exc:
		if exc.code == 13 or "requires authentication" in str(exc).lower():
			raise RuntimeError(
				"MongoDB requires authentication. Update backend/.env MONGODB_URL to include a username and password, for example "
				"mongodb://username:password@localhost:27017/app_development?authSource=admin. MongoDB creates the "
				"app_development database automatically after the first successful authenticated write."
			) from exc
		raise RuntimeError(f"MongoDB rejected the connection: {exc}") from exc
	except PyMongoError as exc:
		raise RuntimeError(f"Unable to connect to MongoDB using MONGODB_URL={settings.mongodb_url!r}: {exc}") from exc

	return client


client = _build_client()
database: Database = client[settings.mongodb_db]
users_collection: Collection = database["users"]
villages_collection: Collection = database["villages"]
customers_collection: Collection = database["customers"]
installments_collection: Collection = database["installments"]
collections_collection: Collection = database["collections"]


def ensure_finance_indexes() -> None:
	users_collection.create_index("email")
	villages_collection.create_index([("owner_user_id", 1), ("day", 1)])
	customers_collection.create_index([("owner_user_id", 1), ("village_id", 1)])
	installments_collection.create_index([("owner_user_id", 1), ("customer_id", 1), ("due_date", 1)])
	collections_collection.create_index([("owner_user_id", 1), ("customer_id", 1), ("collected_at", -1)])
	collections_collection.create_index([("installment_id", 1), ("collected_at", -1)])


ensure_finance_indexes()
