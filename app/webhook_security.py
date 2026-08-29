import hmac
import hashlib

def verify_razorpay_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    """
    Computes HMAC-SHA256 of raw_body using secret as the key,
    and safely compares the hex digest to the given signature.
    """
    if not signature or not secret:
        return False
        
    expected_mac = hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(expected_mac, signature)
