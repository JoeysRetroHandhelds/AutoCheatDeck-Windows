"""Sign a release zip for the updater (run by CI on tag builds).

Usage: python tools/sign_release.py <zip> <tag>   (key in the RELEASE_SIGNING_KEY env var)

Writes <zip>.sig: an Ed25519 signature over updater.signed_message(tag, sha256 of the zip).
To make a new key: python tools/sign_release.py --new-key
"""

import base64
import hashlib
import os
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from autocheatdeck import updater  # noqa: E402


def main() -> None:
    if sys.argv[1:] == ["--new-key"]:
        key = Ed25519PrivateKey.generate()
        raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                serialization.NoEncryption())
        public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        print("RELEASE_SIGNING_KEY secret:", base64.b64encode(raw).decode())
        print("updater.PUBLIC_KEY:        ", public.hex())
        return
    zip_path, tag = sys.argv[1:3]
    secret = os.environ.get("RELEASE_SIGNING_KEY", "").strip()
    if not secret:
        sys.exit("RELEASE_SIGNING_KEY is not set; refusing to publish an unsigned release")
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(secret))
    with open(zip_path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    signature = key.sign(updater.signed_message(tag, digest))
    with open(zip_path + ".sig", "w", encoding="ascii") as f:
        f.write(base64.b64encode(signature).decode())


if __name__ == "__main__":
    main()
