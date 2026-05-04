"""Village list filters by finance workspace (daily / weekly / monthly / yearly)."""

from typing import Any


def villages_mongo_filter(owner_user_id: str, finance_scope: str) -> dict[str, Any]:
    """Villages visible in a workspace. Documents without finance_scope count as weekly (legacy)."""
    base: dict[str, Any] = {"owner_user_id": owner_user_id}
    if finance_scope == "weekly":
        base["$or"] = [
            {"finance_scope": "weekly"},
            {"finance_scope": {"$exists": False}},
            {"finance_scope": None},
        ]
    else:
        base["finance_scope"] = finance_scope
    return base
