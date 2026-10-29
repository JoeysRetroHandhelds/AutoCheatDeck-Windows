"""SSL context with certificate verification always on.

Uses certifi's bundle when it is installed, else a Linux distro bundle (shared with the Decky
plugin, whose bundled Python has no usable default CA path), else the system store (Windows).
"""

import os
import ssl
import urllib.parse
import urllib.request

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


def open_url(request: urllib.request.Request, allowed_hosts: set[str], timeout: float = 30):
    """urlopen that only follows redirects to https URLs on allowed_hosts, so a redirect elsewhere
    is refused before anything is sent there."""

    class Redirects(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            parsed = urllib.parse.urlparse(newurl)
            if parsed.scheme != "https" or parsed.hostname not in allowed_hosts:
                raise ValueError("Redirect not allowed: {}".format(newurl))
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    opener = urllib.request.build_opener(Redirects(), urllib.request.HTTPSHandler(context=create_context()))
    return opener.open(request, timeout=timeout)
