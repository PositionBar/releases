from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ci
import publish


class VersionTests(unittest.TestCase):
    def test_only_reviewed_shape_and_one_added_version_are_accepted(self):
        value = {"version": "0.1.3", "publishedOn": "2026-11-11", "changes": [{"type": "fixed", "items": {locale: ["Fix"] for locale in ci.LOCALES}}]}
        ci.validate(value, '0.1.3.json')
        for invalid in (dict(value, source_sha='a' * 40), dict(value, build=1074)):
            with self.assertRaises(ValueError): ci.validate(invalid, '0.1.3.json')
        for invalid in (dict(value, publishedOn='2026-02-30'), {key: item for key, item in value.items() if key != 'publishedOn'}):
            with self.assertRaises(ValueError): ci.validate(invalid, '0.1.3.json')
        incomplete = {**value, "changes": [{"type": "fixed", "items": {"en": ["Fix"]}}]}
        with self.assertRaises(ValueError): ci.validate(incomplete, '0.1.3.json')
        file = {"filename": "versions/0.1.2.json", "status": "added"}
        self.assertEqual(ci.changed_version([file], '0.1.2'), file)
        for files in ([dict(file, status='modified')], [file, {"filename": "README.md"}]):
            with self.assertRaises(ValueError): ci.changed_version(files, '0.1.2')

    def test_owner_must_approve_the_exact_non_owner_head(self):
        pr = {"user": {"login": "release-bot"}, "head": {"sha": "c" * 40}}
        review = {"user": {"login": "sorcererxw"}, "state": "APPROVED", "commit_id": "c" * 40}
        ci.approved(pr, [review])
        for reviews in ([], [dict(review, commit_id="d" * 40)], [review, dict(review, state="CHANGES_REQUESTED")]):
            with self.assertRaises(ValueError): ci.approved(pr, reviews)
        with self.assertRaises(ValueError): ci.approved({**pr, "user": {"login": "sorcererxw"}}, [review])

    def test_update_data_keeps_published_notes_and_receipts_immutable(self):
        import hashlib
        files = {"appcast.xml": b"signed feed", "PositionBar-0.1.3-1086.txt": b"reviewed notes", "releases/0.1.3.json": b"{}"}
        existing = {name: hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest() for name, raw in files.items()}
        self.assertEqual(publish.update_entries(files, existing), [])
        self.assertEqual(len(publish.update_entries(files, {})), 3)
        self.assertEqual(publish.update_entries({"appcast.xml": b"new signed feed"}, existing)[0]["content"], "new signed feed")
        for name in ("PositionBar-0.1.3-1086.txt", "releases/0.1.3.json"):
            with self.assertRaises(ValueError): publish.update_entries({name: b"different bytes"}, existing)


if __name__ == '__main__': unittest.main()
