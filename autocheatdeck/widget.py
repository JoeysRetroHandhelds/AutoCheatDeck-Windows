"""Install the Game Bar widget from AutoCheatDeck itself (one click, one UAC prompt).

The release zip carries the signed widget package in widget/ (with the framework packages a
sideloaded UWP app needs in widget/Dependencies/<arch>/). Windows only installs it once its signing
certificate is trusted, which takes administrator rights:

1. elevated (one UAC prompt, skipped once done): add the certificate to the machine's Trusted
   People, and exempt the widget from loopback isolation. The certificate is built into
   AutoCheatDeck, passed inline and loaded from memory (its thumbprint checked), never from a file
   the user's programs could swap;
2. as the user (no prompt): Add-AppxPackage with the dependencies, so it installs for this account.

Loopback: Windows keeps packaged apps such as Game Bar widgets from connecting to 127.0.0.1, where
AutoCheatDeck's API listens, unless the package is exempted. Visual Studio adds that exemption when
it deploys a widget, so a widget installed any other way can't reach the app without it.
"""

import glob
import hashlib
import os
import platform
import re
import subprocess
import zipfile

from . import elevate

PACKAGE_NAME = "JoeysRetroHandhelds.AutoCheatDeckWidget"
PUBLISHER = "CN=JoeysRetroHandhelds"


def publisher_id(publisher: str) -> str:
    """Windows' 13-character publisher id: the first 64 bits of SHA-256 over the UTF-16 publisher,
    plus a zero bit, in Crockford-style base32 (e.g. Microsoft's is 8wekyb3d8bbwe)."""
    bits = int.from_bytes(hashlib.sha256(publisher.encode("utf-16-le")).digest()[:8], "big") << 1
    alphabet = "0123456789abcdefghjkmnpqrstvwxyz"
    return "".join(alphabet[(bits >> (5 * (12 - i))) & 31] for i in range(13))


PACKAGE_FAMILY = "{}_{}".format(PACKAGE_NAME, publisher_id(PUBLISHER))
CERT_THUMBPRINT = "5C77A765E59001854442E91D04D81081825447DC"
# widget/AutoCheatDeckWidget.cer (CN=JoeysRetroHandhelds), the public half of the signing certificate.
CERT_BASE64 = (
    "MIIDGjCCAgKgAwIBAgIQMkB8RsNzH5tJCkvgAQbqJTANBgkqhkiG9w0BAQsFADAeMRwwGgYDVQQDDBNKb2V5c1JldHJvSGFu"
    "ZGhlbGRzMB4XDTI2MTAyNTA0MDMyMVoXDTM2MTAyNTA0MTMxNlowHjEcMBoGA1UEAwwTSm9leXNSZXRyb0hhbmRoZWxkczCC"
    "ASIwDQYJKoZIhvcNAQEBBQADggEPADCCAQoCggEBAL/OGM80KnBFRP/wc0ghRNAUD/BnVXWsYlsRLq/5Udy8h6WdhtCpLLPu"
    "lGnvq2DlYpEiVq59691TU1hawSly5Hz5HZuZRohrglp+gT32rV8MKRw9fixShaEJBdGYvdxKjs+1qWoK9ykJMcE/IQ2BFgwB"
    "0XfBB6oRWKiTp0SuKbHMYBkTlt4V1mskUUJ6+ZoLJSsUpg+Q+8+d5gYnKes9/Dk8mWuXHG1I7D8xbXwgSGPvtyM5deN/hRjQ"
    "tbyZQ8UKqsrZj6PPdmu8KZ3eZYzpjhqrxod0x18NQRq7wUP5GW2kokqXe4QNCzw8jINVh+ajqaUbXLHm0LNIgDZWUc6Jz40C"
    "AwEAAaNUMFIwDgYDVR0PAQH/BAQDAgeAMBMGA1UdJQQMMAoGCCsGAQUFBwMDMAwGA1UdEwEB/wQCMAAwHQYDVR0OBBYEFJjZ"
    "ReXzmKCrH+grJ7rYl8AaMa/UMA0GCSqGSIb3DQEBCwUAA4IBAQCrmyWHYW/LyEQ/UqQ88aSu/UV735+gC7gOXWNSYK1RvabO"
    "67gIJVFrxtqWiGXSPkZkGCtq/StY4NBlUGUvXuIVxlpna4QcUGEAicL+JR9NM3z5mCUSLpGWwWhIxXfDyoVCTgAX9g2eHZOK"
    "fVfDZWv/42/lcOSpXZ2MrhP/39JUb7XRo4qvZK20TgDVmduXZu8pt1W6+46jS0mlI0lhlwVx6joIVmrQdlnHHRb/4995tRZ4"
    "tXodEIfvuXAw8xDNrsoUvqzFuDTA+kmtOqzkTDocpn+VcgbXswIsXSKhXLBOcn7XF+4MR3UuaL3FJSLA3gnDWjQb/pfs6Hig"
    "kGNZi5XL"
)

SETUP_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
# From memory, not a file: nothing the user's programs can swap before it is trusted.
$cert = [Security.Cryptography.X509Certificates.X509Certificate2]::new([Convert]::FromBase64String($Cert))
if ($cert.Thumbprint -ne $Thumbprint) { throw 'Unexpected certificate' }
$store = [Security.Cryptography.X509Certificates.X509Store]::new('TrustedPeople', 'LocalMachine')
$store.Open('ReadWrite')
try { $store.Add($cert) } finally { $store.Close() }
# Let the widget reach AutoCheatDeck on 127.0.0.1 (works before the package is installed).
& "$env:SystemRoot\System32\CheckNetIsolation.exe" LoopbackExempt -a "-n=$Family" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "CheckNetIsolation failed ($LASTEXITCODE)" }
"""

NO_WINDOW = elevate.NO_WINDOW


def bundled(app_dir: str) -> str | None:
    """The widget package shipped next to the app, if this build has one."""
    found = sorted(glob.glob(os.path.join(app_dir, "widget", "*.msixbundle")))
    return found[0] if found else None


def bundle_version(bundle: str) -> str:
    """The version in the bundle's manifest (e.g. "2.1.0.0"), "" if unreadable."""
    try:
        with zipfile.ZipFile(bundle) as archive:
            manifest = archive.read("AppxMetadata/AppxBundleManifest.xml").decode("utf-8", errors="replace")
    except (OSError, KeyError, zipfile.BadZipFile):
        return ""
    match = re.search(r"<Identity\b[^>]*\bVersion=\"([0-9.]+)\"", manifest)
    return match.group(1) if match else ""


def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version))


def _powershell(command: str) -> str:
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                            capture_output=True, text=True, creationflags=NO_WINDOW, timeout=120)
    if result.returncode != 0:
        raise OSError((result.stderr or result.stdout).strip() or "PowerShell failed")
    return result.stdout.strip()


def installed_version() -> str:
    """The installed widget's version for this user, "" if it isn't installed."""
    return _powershell("(Get-AppxPackage -Name '{}' | Select-Object -First 1).Version".format(PACKAGE_NAME))


def state(app_dir: str | None, setup_done: bool = False) -> str:
    """"install", "update", "setup" (installed, but it can't reach AutoCheatDeck), "current", or ""
    (nothing to offer: no bundled widget, or not Windows)."""
    bundle = bundled(app_dir) if app_dir else None
    if not bundle:
        return ""
    installed = installed_version()
    if not installed:
        return "install"
    if _version_tuple(bundle_version(bundle)) > _version_tuple(installed):
        return "update"
    # setup_done: AutoCheatDeck remembers a finished setup, in case the exemption list can't be read.
    return "current" if setup_done or loopback_exempt() else "setup"


def loopback_exempt() -> bool:
    """Is the widget allowed to connect to 127.0.0.1? (False if it can't be told.)"""
    try:
        listing = _powershell("& \"$env:SystemRoot\\System32\\CheckNetIsolation.exe\" LoopbackExempt -s")
    except (OSError, subprocess.SubprocessError):
        return False
    return PACKAGE_FAMILY.lower() in listing.lower()


def cert_trusted() -> bool:
    return bool(_powershell("Test-Path 'Cert:\\LocalMachine\\TrustedPeople\\{}'".format(CERT_THUMBPRINT)) == "True")


def dependencies(app_dir: str) -> list[str]:
    machine = platform.machine().lower()
    arch = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(machine, "x64")
    return sorted(glob.glob(os.path.join(app_dir, "widget", "Dependencies", arch, "*.*x")))


def install(app_dir: str, setup_done: bool = False) -> None:
    """Trust the certificate (one UAC prompt, if needed), then install or update the widget for
    this user. Raises OSError with Windows' message if something fails."""
    bundle = bundled(app_dir)
    if not bundle:
        raise OSError("This copy of AutoCheatDeck doesn't include the widget.")
    if not (cert_trusted() and (setup_done or loopback_exempt())):
        if not elevate.run_powershell(SETUP_SCRIPT, Cert="".join(CERT_BASE64), Thumbprint=CERT_THUMBPRINT,
                                      Family=PACKAGE_FAMILY):
            raise OSError("Installing the widget needs administrator approval (to trust its certificate "
                          "and let it reach AutoCheatDeck).")
    command = "Add-AppxPackage -Path {} -ForceUpdateFromAnyVersion -ForceApplicationShutdown".format(
        elevate.ps_literal(bundle))
    deps = dependencies(app_dir)
    if deps:
        command += " -DependencyPath " + ",".join(elevate.ps_literal(d) for d in deps)
    _powershell("$ErrorActionPreference = 'Stop'; " + command)
