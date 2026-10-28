import base64
import hashlib
import os
import tempfile
import unittest
import zipfile
from unittest import mock

from autocheatdeck import widget

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = ('<?xml version="1.0"?><Bundle xmlns="http://schemas.microsoft.com/appx/2013/bundle">'
            '<Identity Name="JoeysRetroHandhelds.AutoCheatDeckWidget" Publisher="CN=JoeysRetroHandhelds" '
            'Version="{}"/></Bundle>')


def make_app(folder: str, version: str = "2.2.0.0") -> str:
    os.makedirs(os.path.join(folder, "widget", "Dependencies", "x64"))
    with zipfile.ZipFile(os.path.join(folder, "widget", "AutoCheatDeckWidget.msixbundle"), "w") as archive:
        archive.writestr("AppxMetadata/AppxBundleManifest.xml", MANIFEST.format(version))
    for name in ("Microsoft.NET.Native.Framework.2.2.appx", "Microsoft.VCLibs.x64.14.00.appx"):
        open(os.path.join(folder, "widget", "Dependencies", "x64", name), "wb").close()
    return folder


class WidgetTest(unittest.TestCase):
    def test_built_in_certificate_is_the_repo_one(self):
        with open(os.path.join(REPO, "widget", "AutoCheatDeckWidget.cer"), "rb") as f:
            der = f.read()
        self.assertEqual(base64.b64decode("".join(widget.CERT_BASE64)), der)
        self.assertEqual(hashlib.sha1(der).hexdigest().upper(), widget.CERT_THUMBPRINT)

    def test_package_family_uses_windows_publisher_id(self):
        # Microsoft's own publisher id, a well-known value, checks the algorithm.
        self.assertEqual(widget.publisher_id(
            "CN=Microsoft Corporation, O=Microsoft Corporation, L=Redmond, S=Washington, C=US"), "8wekyb3d8bbwe")
        self.assertEqual(widget.PACKAGE_FAMILY, "JoeysRetroHandhelds.AutoCheatDeckWidget_sbtj8m4gnqc1y")

    def test_state(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(widget.state(folder), "")  # no widget in this build
            self.assertEqual(widget.state(None), "")  # running from source
            make_app(folder)
            self.assertEqual(widget.bundle_version(widget.bundled(folder)), "2.2.0.0")
            for installed, exempt, expected in (("", False, "install"), ("2.1.0.0", True, "update"),
                                                ("2.2.0.0", True, "current"), ("10.0.0.0", True, "current"),
                                                ("2.2.0.0", False, "setup")):
                with mock.patch.object(widget, "installed_version", return_value=installed), \
                        mock.patch.object(widget, "loopback_exempt", return_value=exempt):
                    self.assertEqual(widget.state(folder), expected, installed)
            with mock.patch.object(widget, "installed_version", return_value="2.2.0.0"), \
                    mock.patch.object(widget, "loopback_exempt", return_value=False):
                self.assertEqual(widget.state(folder, setup_done=True), "current")  # remembered

    def test_install_trusts_the_certificate_once_then_installs_as_the_user(self):
        with tempfile.TemporaryDirectory() as folder:
            make_app(folder)
            with mock.patch.object(widget.platform, "machine", return_value="AMD64"), \
                    mock.patch.object(widget, "cert_trusted", return_value=False), \
                    mock.patch.object(widget, "loopback_exempt", return_value=False), \
                    mock.patch.object(widget.elevate, "run_powershell", return_value=True) as elevated, \
                    mock.patch.object(widget, "_powershell", return_value="") as user:
                widget.install(folder)
            self.assertEqual(elevated.call_args.kwargs["Thumbprint"], widget.CERT_THUMBPRINT)
            self.assertEqual(elevated.call_args.kwargs["Family"], widget.PACKAGE_FAMILY)  # loopback exemption
            command = user.call_args.args[0]
            self.assertIn("Add-AppxPackage -Path '", command)
            self.assertIn("Microsoft.VCLibs.x64.14.00.appx'", command)
            self.assertIn("-DependencyPath", command)

            with mock.patch.object(widget, "cert_trusted", return_value=True), \
                    mock.patch.object(widget, "loopback_exempt", return_value=True), \
                    mock.patch.object(widget.elevate, "run_powershell") as elevated, \
                    mock.patch.object(widget, "_powershell", return_value=""):
                widget.install(folder)
            elevated.assert_not_called()  # already trusted and exempted: no UAC prompt

            with mock.patch.object(widget, "cert_trusted", return_value=True), \
                    mock.patch.object(widget, "loopback_exempt", return_value=False), \
                    mock.patch.object(widget.elevate, "run_powershell", return_value=True) as elevated, \
                    mock.patch.object(widget, "_powershell", return_value=""):
                widget.install(folder)
            elevated.assert_called_once()  # trusted but not exempted (installed before this fix): prompt

    def test_declined_prompt_means_no_install(self):
        with tempfile.TemporaryDirectory() as folder:
            make_app(folder)
            with mock.patch.object(widget, "cert_trusted", return_value=False), \
                    mock.patch.object(widget, "loopback_exempt", return_value=False), \
                    mock.patch.object(widget.elevate, "run_powershell", return_value=False), \
                    mock.patch.object(widget, "_powershell") as user:
                with self.assertRaisesRegex(OSError, "administrator"):
                    widget.install(folder)
            user.assert_not_called()
