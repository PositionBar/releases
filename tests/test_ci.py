import base64
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, sentinel

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ci
import publish


class VersionTests(unittest.TestCase):
    def test_only_the_requested_added_version_is_accepted(self):
        file = {"filename": "versions/0.1.2.json", "status": "added"}
        self.assertEqual(ci.changed_version([file], '0.1.2'), file)
        for files in ([], [dict(file, status='modified')], [dict(file, filename='versions/0.1.3.json')],
                      [file, {"filename": "README.md"}]):
            with self.subTest(files=files), self.assertRaises(ValueError):
                ci.changed_version(files, '0.1.2')

    def test_release_tag_uses_the_supplied_whole_version_and_a_positive_build(self):
        pattern = r"^(1|2)\.0\.7$"
        self.assertEqual(ci.release_tag("positionbar-2.0.7-12345678901234567890", pattern),
                         ("2.0.7", 12345678901234567890))
        self.assertEqual(ci.release_tag("positionbar-1.0.7-1", pattern), ("1.0.7", 1))
        for tag in ("positionbar-3.0.7-1", "positionbar-01.0.7-2", "positionbar-2.0.7-0",
                    "positionbar-2.0.7-01", "positionbar-2.0.7--1", "positionbar-2.0.7-１",
                    "positionbar-2.0.7-1-extra", "2.0.7-1"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                ci.release_tag(tag, pattern)

    def test_github_contents_must_be_a_file_matching_the_raw_git_blob(self):
        raw = b"hello world\n"
        content = {"type": "file", "encoding": "base64", "content": base64.encodebytes(raw).decode(),
                   "sha": "3b18e512dba79e4c8300dd08aeb37f8e728b8dad"}
        self.assertEqual(ci.file_contents(content), raw)
        for change in ({"type": "symlink"}, {"encoding": "none"}, {"content": "!!!"},
                       {"content": "YQ=="}, {"sha": None}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                ci.file_contents({**content, **change})

    def test_matching_old_receipt_is_complete_only_for_the_approved_identity(self):
        request = {"version": "0.1.2", "source_sha": "a" * 40, "metadataSHA256": "b" * 64,
                   "releaseTarget": "c" * 40}
        published = {**request, "status": "published", "build": 1085, "releaseTarget": "d" * 40}
        self.assertEqual(ci.completion_receipt(json.dumps(published).encode(), request, 1085), published)
        for change in ({"version": "0.1.3"}, {"source_sha": "e" * 40}, {"metadataSHA256": "f" * 64},
                       {"status": "packaged"}, {"build": 1086}, {"build": "1085"}):
            raw = json.dumps({**published, **change}).encode()
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "completion receipt differs"):
                ci.completion_receipt(raw, request, 1085)

    def test_approved_plan_checks_exact_bytes_before_loading_the_pinned_validator(self):
        value = {"version": "0.1.2"}
        raw = json.dumps(value).encode()
        request = {"version": value["version"], "source_sha": "c" * 40,
                   "metadataSHA256": hashlib.sha256(raw).hexdigest()}
        with tempfile.TemporaryDirectory() as temporary, patch.object(publish, "native_tools") as load_tools:
            state = Path(temporary)
            source = state / "source"
            metadata = source / "versions" / "0.1.2.json"
            metadata.parent.mkdir(parents=True)
            (state / "request.json").write_text(json.dumps(request))
            metadata.write_bytes(raw + b"\n")
            with self.assertRaisesRegex(ValueError, "hash differs"):
                publish.approved_plan(state, source)

            target = source / "other.json"
            target.write_bytes(raw)
            metadata.unlink()
            metadata.symlink_to(target)
            with self.assertRaisesRegex(ValueError, "regular file"):
                publish.approved_plan(state, source)
            load_tools.assert_not_called()

            metadata.unlink()
            metadata.write_bytes(raw)
            native = load_tools.return_value
            native.versions.historical.return_value = False
            native.versions.validate.return_value = sentinel.plan
            self.assertEqual(publish.approved_plan(state, source), (native, request, sentinel.plan))
            load_tools.assert_called_once_with(source, request["source_sha"])
            native.versions.validate.assert_called_once_with(value, metadata.name)

            native.versions.validate.side_effect = ValueError("canonical validator rejected metadata")
            with self.assertRaisesRegex(ValueError, "canonical validator rejected metadata"):
                publish.approved_plan(state, source)

    def test_source_revision_and_clean_tree_are_checked_before_import(self):
        expected = "c" * 40
        for responses in (["d" * 40], [expected + "\n", b" M scripts/macos_release.py\n"]):
            with self.subTest(responses=responses), patch.object(publish.subprocess, "check_output", side_effect=responses):
                with self.assertRaisesRegex(ValueError, "SHA mismatch|must be clean"):
                    publish.native_tools(Path("source"), expected)

    def test_owner_must_approve_the_exact_non_owner_head(self):
        pr = {"user": {"login": "release-bot"}, "head": {"sha": "c" * 40}}
        review = {"user": {"login": "sorcererxw"}, "state": "APPROVED", "commit_id": "c" * 40}
        ci.approved(pr, [review])
        for reviews in ([], [dict(review, commit_id="d" * 40)], [review, dict(review, state="CHANGES_REQUESTED")]):
            with self.assertRaises(ValueError): ci.approved(pr, reviews)
        with self.assertRaises(ValueError): ci.approved({**pr, "user": {"login": "sorcererxw"}}, [review])

    def test_update_data_keeps_published_notes_and_receipts_immutable(self):
        files = {"appcast.xml": b"signed feed", "PositionBar-0.1.3-1086.txt": b"reviewed notes", "releases/0.1.3.json": b"{}"}
        existing = {name: hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest() for name, raw in files.items()}
        self.assertEqual(publish.update_entries(files, existing), [])
        self.assertEqual(len(publish.update_entries(files, {})), 3)
        self.assertEqual(publish.update_entries({"appcast.xml": b"new signed feed"}, existing)[0]["content"], "new signed feed")
        for name in ("PositionBar-0.1.3-1086.txt", "releases/0.1.3.json"):
            with self.assertRaises(ValueError): publish.update_entries({name: b"different bytes"}, existing)


if __name__ == '__main__': unittest.main()
