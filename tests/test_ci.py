from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ci
import publish


class VersionTests(unittest.TestCase):
    def test_only_reviewed_shape_and_one_added_version_are_accepted(self):
        value = {"version": "0.1.2", "changes": [{"type": "fixed", "items": {locale: ["Fix"] for locale in ci.LOCALES}}]}
        ci.validate(value, '0.1.2.json')
        for invalid in (dict(value, source_sha='a' * 40), dict(value, build=1074)):
            with self.assertRaises(ValueError): ci.validate(invalid, '0.1.2.json')
        incomplete = {**value, "changes": [{"type": "fixed", "items": {"en": ["Fix"]}}]}
        with self.assertRaises(ValueError): ci.validate(incomplete, '0.1.2.json')
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


if __name__ == '__main__': unittest.main()
