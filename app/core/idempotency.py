import hashlib
import json


def request_hash(method: str, path: str, body: object) -> str:
    canon = json.dumps([method, path, body], sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canon.encode()).hexdigest()
