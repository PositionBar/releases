import hashlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import publish


class ReleaseAssetTests(unittest.TestCase):
    def test_only_one_exact_public_dmg_can_be_distributed(self):
        name = "PositionBar-1.2.3-1085.dmg"
        url = publish.release_asset_url("PositionBar/releases", "positionbar-1.2.3-1085", name)
        asset = {"name": name, "browser_download_url": url, "size": 4}
        stable = {"draft": False, "prerelease": False, "assets": [asset]}
        publish.verify_release_asset(stable, name, url, 4)
        for state in (None, dict(stable, draft=True), dict(stable, prerelease=True),
                      dict(stable, assets=[]), dict(stable, assets=[asset, asset]),
                      dict(stable, assets=[asset, {"name": "appcast.xml"}]),
                      dict(stable, assets=[dict(asset, name="other.dmg")]),
                      dict(stable, assets=[dict(asset, browser_download_url=url + "?other")]),
                      dict(stable, assets=[dict(asset, size=5)])):
            with self.subTest(state=state), self.assertRaises(ValueError):
                publish.verify_release_asset(state, name, url, 4)

    def test_lost_upload_response_requires_the_requested_asset_on_readback(self):
        artifact = Path("PositionBar-1.2.3-1085.dmg")
        empty = {"draft": False, "prerelease": False, "assets": []}
        present = dict(empty, assets=[{"name": artifact.name}])
        config = {"github_repository": "PositionBar/releases", "release_target": "a" * 40}
        for readback in (empty, present):
            states = [empty, empty, readback, readback]
            with self.subTest(readback=readback), patch.object(publish, "release_state", side_effect=states), \
                    patch.object(publish, "command", side_effect=ValueError("lost response")) as upload:
                if readback["assets"]:
                    publish.upload_release_asset(config, {}, "tag", artifact, "title", "body")
                else:
                    with self.assertRaisesRegex(ValueError, "upload outcome is uncertain"):
                        publish.upload_release_asset(config, {}, "tag", artifact, "title", "body")
                upload.assert_called_once()

    def test_only_matching_download_bytes_reach_the_file_passed_for_notarization_checks(self):
        url = "https://github.com/PositionBar/releases/releases/download/tag/approved.dmg"
        raw = b"approved public dmg"
        expected = hashlib.sha256(raw).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "approved.dmg"
            for length, sha in ((len(raw) + 1, expected), (len(raw), "0" * 64), (len(raw), expected)):
                response = io.BytesIO(raw)
                response.status = 200
                response.geturl = lambda: "https://release-assets.githubusercontent.com/asset"
                with self.subTest(length=length, sha=sha), patch.object(publish.urllib.request, "urlopen", return_value=response):
                    if (length, sha) == (len(raw), expected):
                        publish.public_digest(url, length, sha, destination)
                        self.assertEqual(destination.read_bytes(), raw)
                    else:
                        with self.assertRaises(ValueError):
                            publish.public_digest(url, length, sha, destination)
                        self.assertEqual(list(destination.parent.iterdir()), [])
