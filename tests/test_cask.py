import base64
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, call

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import cask
from ci import git_blob_sha


def receipt(version="0.1.2", build=1074):
    tag = f"positionbar-{version}-{build}"
    return {"version": version, "build": build, "artifact": {
        "sha256": "b" * 64, "minimumSystemVersion": "26.0.0", "architectures": ["arm64", "x86_64"],
        "url": f"https://github.com/PositionBar/releases/releases/download/{tag}/PositionBar-{version}-{build}.dmg",
    }}


def github_file(raw):
    return {"type": "file", "encoding": "base64", "content": base64.b64encode(raw).decode("ascii"),
            "sha": git_blob_sha(raw)}


class RenderingTests(unittest.TestCase):
    def test_universal_cask_has_one_immutable_identity_and_no_arch_filter(self):
        release = receipt()
        contents = cask.render_cask(release)
        version_pattern = r"^0\.1\.[12]$"
        self.assertIn('  version "0.1.2"\n', contents)
        expected = ((0, 1, 2), release['artifact']['url'], 'b' * 64)
        self.assertEqual(cask.cask_identity(contents, version_pattern), expected)
        legacy = contents.replace('version "0.1.2"', 'version "0.1.2,1074"')
        self.assertEqual(cask.cask_identity(legacy, version_pattern), expected)
        for invalid in ('0.1.2,0', '0.1.2,invalid', '0.1.3', '0.01.2', '0.1.2\n'):
            with self.assertRaises(ValueError):
                cask.cask_identity(contents.replace('version "0.1.2"', f'version "{invalid}"'), version_pattern)
        self.assertIn('depends_on macos: :tahoe', contents)
        self.assertNotIn('depends_on arch:', contents)


class PublicationTests(unittest.TestCase):
    pattern = r"^0\.1\.[1-3]$"
    history = "repos/PositionBar/homebrew-tap/commits?sha=main&path=Casks/positionbar.rb&per_page=1"
    contents = "repos/PositionBar/homebrew-tap/contents/Casks/positionbar.rb"
    old, new = "a" * 40, "b" * 40

    def setUp(self):
        self.raw = cask.render_cask(receipt()).encode()
        self.old_raw = cask.render_cask(receipt("0.1.1", 1073)).encode()
        self.written = {"commit": {"sha": self.new}, "content": {"sha": git_blob_sha(self.raw)}}

    def test_update_and_creation_compare_the_file_and_verify_the_path_commit(self):
        for exists in (True, False):
            with self.subTest(exists=exists):
                reads = [[{"sha": self.old}], github_file(self.old_raw)] if exists else [[]]
                api = Mock(side_effect=[*reads, self.written, [{"sha": self.new}], github_file(self.raw)])
                self.assertEqual(cask.publish(receipt(), self.pattern, api), self.new)
                self.assertEqual(api.call_args_list[0], call(self.history))
                if exists:
                    self.assertEqual(api.call_args_list[1], call(self.contents + "?ref=" + self.old))
                put = api.call_args_list[len(reads)]
                self.assertEqual((put.args, put.kwargs["method"]), ((self.contents,), "PUT"))
                body = put.kwargs["body"]
                self.assertEqual(body.get("sha"), git_blob_sha(self.old_raw) if exists else None)
                self.assertEqual(base64.b64decode(body["content"]), self.raw)
                self.assertEqual(body["branch"], "main")
                self.assertEqual(api.call_args_list[-2:], [call(self.history), call(self.contents + "?ref=" + self.new)])

    def test_lost_response_can_resume_from_the_verified_last_cask_commit_without_writing(self):
        api = Mock(side_effect=[[{"sha": self.old}], github_file(self.old_raw), ValueError("unknown outcome")])
        with self.assertRaisesRegex(ValueError, "unknown outcome"):
            cask.publish(receipt(), self.pattern, api)
        self.assertEqual(api.call_count, 3)
        resumed = Mock(side_effect=[[{"sha": self.new}], github_file(self.raw), [{"sha": self.new}]])
        self.assertEqual(cask.publish(receipt(), self.pattern, resumed), self.new)
        self.assertEqual(resumed.call_args_list, [call(self.history), call(self.contents + "?ref=" + self.new),
                                                 call(self.history)])

    def test_downgrades_and_mutating_a_published_version_never_write(self):
        newer = cask.render_cask(receipt("0.1.3", 1075)).encode()
        for old, message in [(newer, "downgrade"), (self.raw.replace(b"1074", b"1073"), "immutable"),
                             (self.raw.replace(b"b" * 64, b"c" * 64), "immutable")]:
            with self.subTest(message=message):
                api = Mock(side_effect=[[{"sha": self.old}], github_file(old)])
                with self.assertRaisesRegex(ValueError, message):
                    cask.publish(receipt(), self.pattern, api)
                self.assertEqual(api.call_count, 2)

    def test_conflicts_or_unverified_writes_cannot_complete_publication(self):
        for responses in ([ValueError("conflict")], [{}], [{"commit": {"sha": self.new}, "content": {"sha": "0" * 40}}],
                          [self.written, [{"sha": self.old}]], [self.written, [{"sha": self.new}], github_file(self.old_raw)]):
            with self.subTest(responses=responses):
                api = Mock(side_effect=[[{"sha": self.old}], github_file(self.old_raw), *responses])
                with self.assertRaises(ValueError):
                    cask.publish(receipt(), self.pattern, api)
        changed = Mock(side_effect=[[{"sha": self.new}], github_file(self.raw), [{"sha": self.old}]])
        with self.assertRaisesRegex(ValueError, "changed during verification"):
            cask.publish(receipt(), self.pattern, changed)


if __name__ == '__main__': unittest.main()
