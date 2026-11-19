"""Print a new VAPID key pair for Atlas phone notifications (Web Push, RFC 8292).

Run once:  python scripts/generate_vapid_keys.py
Then store the values yourself; nothing is written to disk or sent anywhere:
  - Supabase secrets: WEB_PUSH_VAPID_PUBLIC_KEY, WEB_PUSH_VAPID_PRIVATE_KEY, WEB_PUSH_SUBJECT
  - GitHub Actions variable (public, used by the Atlas build): WEB_PUSH_VAPID_PUBLIC_KEY
Never commit the private key. Replacing the pair invalidates every existing phone subscription.
"""
import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def generate():
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_raw = private_key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    private_raw = private_key.private_numbers().private_value.to_bytes(32, "big")
    return b64url(public_raw), b64url(private_raw)


if __name__ == "__main__":
    public_key, private_key = generate()
    print(f"WEB_PUSH_VAPID_PUBLIC_KEY={public_key}")
    print(f"WEB_PUSH_VAPID_PRIVATE_KEY={private_key}")
    print("WEB_PUSH_SUBJECT=mailto:<維護者聯絡信箱> 或 https://<聯絡頁面>")
