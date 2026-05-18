import json
import urllib.request
from jose import jwt
from jose.exceptions import JWTError

_PUBLIC_KEYS_URL = (
    "https://www.googleapis.com/robot/v1/metadata/x509/"
    "securetoken@system.gserviceaccount.com"
)
_FIREBASE_PROJECT_ID = "udhara-748db"
_ISSUER = f"https://securetoken.google.com/{_FIREBASE_PROJECT_ID}"


def _fetch_public_keys() -> dict[str, str]:
    with urllib.request.urlopen(_PUBLIC_KEYS_URL) as resp:
        return json.loads(resp.read().decode())


def verify_firebase_token(id_token: str) -> dict:
    """Verify a Firebase ID token using Google's public keys. No service account needed."""
    try:
        header = jwt.get_unverified_header(id_token)
    except JWTError as exc:
        raise ValueError("Invalid token header") from exc

    kid = header.get("kid")
    public_keys = _fetch_public_keys()

    if kid not in public_keys:
        raise ValueError("Unknown signing key")

    try:
        payload = jwt.decode(
            id_token,
            public_keys[kid],
            algorithms=["RS256"],
            audience=_FIREBASE_PROJECT_ID,
            issuer=_ISSUER,
        )
    except JWTError as exc:
        raise ValueError("Token verification failed") from exc

    return payload
