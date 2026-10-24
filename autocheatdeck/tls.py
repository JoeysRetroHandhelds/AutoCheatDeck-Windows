"""SSL context that works under Decky's bundled Python.

Decky's PyInstaller build has no usable default CA path, so certificate checks fail with
"unable to get local issuer certificate". Point it at the distro's bundle instead of
turning verification off.
"""

import os
import ssl

CA_BUNDLES = (
    "/etc/ssl/certs/ca-certificates.crt",  # SteamOS, Arch, Debian
    "/etc/pki/tls/certs/ca-bundle.crt",  # Fedora, Bazzite
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",  # Fedora atomic (Anatase)
    "/etc/ssl/cert.pem",
)


def _find_bundle() -> str | None:
    try:
        import certifi  # type: ignore

        return certifi.where()
    except ImportError:
        pass
    for path in CA_BUNDLES:
        if os.path.isfile(path):
            return path
    return None


def create_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=_find_bundle())
