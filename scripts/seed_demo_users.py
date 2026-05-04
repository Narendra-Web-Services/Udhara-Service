from pathlib import Path
import sys
from datetime import UTC, datetime, timedelta

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.security import hash_password
from app.db.mongodb import customers_collection, installments_collection, users_collection, villages_collection

DEMO_USERS = [
    {
        "_id": "USR-1001",
        "full_name": "Aarav Sharma",
        "email": "admin@example.com",
        "phone_number": "+919900000001",
        "role": "admin",
        "has_subscription": True,
        "hashed_password": hash_password("Password@123"),
    },
    {
        "_id": "USR-1002",
        "full_name": "Meera Iyer",
        "email": "customer@example.com",
        "phone_number": "+919900000002",
        "role": "customer",
        "has_subscription": True,
        "hashed_password": hash_password("Password@123"),
    },
    {
        "_id": "USR-1003",
        "full_name": "Rohit Verma",
        "email": "trial@example.com",
        "phone_number": "+919900000003",
        "role": "customer",
        "has_subscription": False,
        "hashed_password": hash_password("Password@123"),
    },
]

for user in DEMO_USERS:
    users_collection.replace_one({"_id": user["_id"]}, user, upsert=True)

villages_collection.delete_many({"owner_user_id": {"$in": [user["_id"] for user in DEMO_USERS]}})
customers_collection.delete_many({"owner_user_id": {"$in": [user["_id"] for user in DEMO_USERS]}})
installments_collection.delete_many({"owner_user_id": {"$in": [user["_id"] for user in DEMO_USERS]}})

for index, user in enumerate(DEMO_USERS, start=1):
    village_id = f"vil-demo-{index}"
    villages_collection.insert_one(
        {
            "_id": village_id,
            "owner_user_id": user["_id"],
            "name": f"Village {index}",
            "day": ["Monday", "Tuesday", "Wednesday"][index - 1],
            "finance_scope": "weekly",
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }
    )

    customer_id = f"cus-demo-{index}"
    customers_collection.insert_one(
        {
            "_id": customer_id,
            "owner_user_id": user["_id"],
            "village_id": village_id,
            "full_name": f"Customer {index}",
            "address": f"Main street {index}",
            "amount_lent": 10000 + index * 2500,
            "payment_type": "weekly",
            "installment_amount": 1500 + index * 100,
            "installment_count": 7,
            "phone_number": f"+91000000000{index}",
            "image_url": None,
            "aadhar_number": f"AADHAR00{index}",
            "aadhar_image_url": None,
            "external_customer_id": f"{user['_id']}-CUST{index}",
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }
    )

    for due_index in range(7):
        status = "paid" if due_index < 3 else "skipped" if due_index == 3 else "pending"
        amount_due = 1500 + index * 100
        amount_paid = amount_due if status == "paid" else amount_due / 2 if status == "skipped" else 0
        installments_collection.insert_one(
            {
                "_id": f"ins-demo-{index}-{due_index}",
                "owner_user_id": user["_id"],
                "customer_id": customer_id,
                "village_id": village_id,
                "due_date": datetime.now(UTC) + timedelta(days=due_index),
                "amount_due": amount_due,
                "amount_paid": amount_paid,
                "status": status,
            }
        )

print("Seeded demo users:")
for user in DEMO_USERS:
    print(
        f"- {user['_id']} | {user['email']} | {user['phone_number']} | {user['role']} | subscribed={user['has_subscription']}"
    )
