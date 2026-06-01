import json
import hashlib

def make_action_fingerprint(email_gmail_id: str, action: str, params: dict) -> str:
    """
    Compute a SHA-256 fingerprint for an action.

    Sorts the JSON representation of the dict {email_gmail_id, action, params}.
    Returns the hex digest string.
    """
    data = {
        "email_gmail_id": email_gmail_id,
        "action": action,
        "params": params or {}
    }
    json_s = json.dumps(data, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(json_s.encode('utf-8')).hexdigest()

