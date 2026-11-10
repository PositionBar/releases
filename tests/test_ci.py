from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ci


def plan():
    return {"schemaVersion": 1, "version": "0.1.0", "source_sha": "a" * 40,
            "changes": [{"type": "fixed", "items": {locale: ["Example fix"] for locale in ci.LOCALES}}]}


def pr():
    return {"number": 2, "merged": True, "merge_commit_sha": "b" * 40,
            "user": {"login": "sorcererxw-devbot"}, "base": {"ref": "main"},
            "head": {"sha": "c" * 40, "repo": {"full_name": ci.REPOSITORY}}}


class ReleasePlanTests(unittest.TestCase):
    def test_plan_rejects_missing_locale_duplicate_category_and_unapproved_metadata(self):
        ci.validate_plan(plan(), "release-plans/0.1.0.json")
        values = []
        value = plan(); del value["changes"][0]["items"]["ja"]; values.append(value)
        value = plan(); value["changes"] *= 2; values.append(value)
        value = plan(); value["build"] = 1015; values.append(value)
        value = plan(); value["source_sha"] = "main"; values.append(value)
        value = plan(); value["version"] = "00.1.0"; values.append(value)
        value = plan(); value["changes"][0]["items"]["en"] = [" "]; values.append(value)
        for value in values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                ci.validate_plan(value, "release-plans/0.1.0.json")
        with self.assertRaises(ValueError): ci.validate_plan(plan(), "release-plans/0.2.0.json")

    def test_only_one_added_plan_is_a_release(self):
        file = {"filename": "release-plans/0.1.0.json", "status": "added"}
        self.assertIsNone(ci.changed_plan([{"filename": "README.md", "status": "modified"}]))
        self.assertEqual(ci.changed_plan([file]), file)
        for files in ([dict(file, status="modified")], [dict(file, status="removed")],
                      [file, {"filename": "scripts/ci.py", "status": "modified"}],
                      [dict(file, filename="release-plans/../0.1.0.json")]):
            with self.assertRaises(ValueError): ci.changed_plan(files)

    def test_approval_is_owner_review_of_exact_head_and_cannot_be_self_approved(self):
        review = {"user": {"login": "sorcererxw"}, "state": "APPROVED", "commit_id": "c" * 40}
        ci.approved(pr(), [review])
        for reviews in ([], [dict(review, commit_id="d" * 40)],
                        [review, dict(review, state="CHANGES_REQUESTED")],
                        [review, dict(review, state="DISMISSED")]):
            with self.assertRaises(ValueError): ci.approved(pr(), reviews)
        value = pr(); value["user"]["login"] = "sorcererxw"
        with self.assertRaises(ValueError): ci.approved(value, [review])



if __name__ == "__main__": unittest.main()
