import json
import urllib.request

from app.core.config import get_settings

_BREVO_URL = "https://api.brevo.com/v3/smtp/email"
_FROM_EMAIL = "narendrawebservices@gmail.com"
_FROM_NAME = "Narendra Web Services"


def send_otp_email(to_email: str, otp: str, full_name: str) -> None:
    html = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:auto;padding:32px 24px;background:#f4f7fc;border-radius:16px">
      <h2 style="color:#0f2240;margin-bottom:4px">Password Reset</h2>
      <p style="color:#64748b;margin-top:0">Hi {full_name},</p>
      <p style="color:#64748b">Use the OTP below to reset your Udhara account password. It expires in <b>10 minutes</b>.</p>
      <div style="background:#ffffff;border-radius:12px;padding:24px;text-align:center;margin:24px 0;border:1.5px solid #e2eaf4">
        <span style="font-size:36px;font-weight:900;letter-spacing:10px;color:#2f7dff">{otp}</span>
      </div>
      <p style="color:#94a3b8;font-size:12px">If you did not request a password reset, you can safely ignore this email.</p>
    </div>
    """

    payload = json.dumps({
        "sender": {"name": _FROM_NAME, "email": _FROM_EMAIL},
        "to": [{"email": to_email}],
        "subject": "Your Udhara Password Reset OTP",
        "htmlContent": html,
    }).encode()

    req = urllib.request.Request(
        _BREVO_URL,
        data=payload,
        headers={
            "api-key": get_settings().brevo_api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(req) as resp:
        if resp.status not in (200, 201):
            body = resp.read().decode()
            raise RuntimeError(f"Brevo API error {resp.status}: {body}")
