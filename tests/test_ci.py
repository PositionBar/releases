import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

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

    def test_reviewed_file_is_bound_to_git_blob_and_cannot_be_a_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "release-plans/0.1.0.json"; path.parent.mkdir()
            raw = (json.dumps(plan()) + "\n").encode(); path.write_bytes(raw)
            file = {"filename": "release-plans/0.1.0.json", "status": "added",
                    "sha": hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()}
            with patch.object(ci, "ROOT", root), patch.object(ci, "github", side_effect=[pr(), [file]]):
                self.assertEqual(ci.pr_plan(2)["sha256"], hashlib.sha256(raw).hexdigest())
            path.write_bytes(raw + b" ")
            with patch.object(ci, "ROOT", root), patch.object(ci, "github", side_effect=[pr(), [file]]):
                with self.assertRaisesRegex(ValueError, "reviewed"): ci.pr_plan(2)
            path.unlink(); path.symlink_to(root / "missing")
            with patch.object(ci, "ROOT", root), patch.object(ci, "github", side_effect=[pr(), [file]]):
                with self.assertRaises(ValueError): ci.pr_plan(2)

    def test_main_snapshot_may_remain_pinned_when_main_advances(self):
        for status in ("ahead", "identical"):
            with patch.object(ci, "github", return_value={"status": status, "merge_base_commit": {"sha": "a" * 40}}):
                ci.verify_source("a" * 40)
        for status, sha in (("behind", "a" * 40), ("diverged", "b" * 40), ("ahead", "b" * 40)):
            with patch.object(ci, "github", return_value={"status": status, "merge_base_commit": {"sha": sha}}):
                with self.assertRaises(ValueError): ci.verify_source("a" * 40)

    def test_duplicate_or_backwards_version_and_partial_rebuild_are_rejected(self):
        value = {"plan": plan(), "sha256": "d" * 64}
        release = {"tag_name": "positionbar-0.1.0-1056", "assets": []}
        with patch.object(ci, "github", return_value=[release]):
            with self.assertRaises(ValueError): ci.publication_state(value, merged=False, resume=False)
            with self.assertRaisesRegex(ValueError, "original run"):
                ci.publication_state(value, merged=True, resume=False)
            self.assertEqual(ci.publication_state(value, merged=True, resume=True), "resume")
        with patch.object(ci, "github", return_value=[dict(release, tag_name="positionbar-0.2.0-1057")]):
            with self.assertRaisesRegex(ValueError, "newer"): ci.publication_state(value, merged=True, resume=False)
        with patch.object(ci, "github", return_value=[dict(release, tag_name="positionbar-0.2.0-1057"), release]):
            with self.assertRaisesRegex(ValueError, "newer"): ci.publication_state(value, merged=True, resume=True)

    def test_completed_duplicate_is_a_noop_only_when_approved_plan_digest_matches(self):
        release = {"tag_name": "positionbar-0.1.0-1056", "assets": [{"name": "release.json"}]}
        value = {"plan": plan(), "sha256": "d" * 64}
        from io import BytesIO
        with patch.object(ci, "github", return_value=[release]), \
             patch.object(ci.urllib.request, "urlopen", return_value=BytesIO(json.dumps({"releasePlanSHA256": "d" * 64}).encode())):
            self.assertEqual(ci.publication_state(value, merged=True, resume=False), "complete")
        with patch.object(ci, "github", return_value=[release]), \
             patch.object(ci.urllib.request, "urlopen", return_value=BytesIO(json.dumps({"releasePlanSHA256": "e" * 64}).encode())):
            with self.assertRaises(ValueError): ci.publication_state(value, merged=True, resume=False)

    def test_unreviewed_direct_main_push_cannot_add_a_plan(self):
        with tempfile.TemporaryDirectory() as temporary:
            event = Path(temporary) / "event.json"; event.write_text(json.dumps({"before": "b" * 40}))
            with patch.dict(os.environ, GITHUB_REPOSITORY=ci.REPOSITORY, GITHUB_EVENT_NAME="push",
                            GITHUB_EVENT_PATH=str(event), GITHUB_REF="refs/heads/main", GITHUB_SHA="c" * 40), \
                 patch.object(ci, "github", return_value=[]), \
                 patch.object(ci.subprocess, "run", return_value=type("Result", (), {"stdout": "release-plans/0.1.0.json"})()):
                with self.assertRaisesRegex(ValueError, "approved"): ci.event_plan()

    def test_maintenance_pr_never_allocates_or_releases(self):
        with tempfile.TemporaryDirectory() as temporary:
            event = Path(temporary) / "event.json"; event.write_text('{"number": 2}')
            with patch.dict(os.environ, GITHUB_REPOSITORY=ci.REPOSITORY, GITHUB_EVENT_NAME="pull_request",
                            GITHUB_EVENT_PATH=str(event)), patch.object(ci, "pr_plan", return_value=None):
                self.assertEqual(ci.event_plan(), {"release": "false"})

    def test_recovery_accepts_only_own_main_workflow(self):
        run = {"repository": {"full_name": ci.REPOSITORY}, "head_branch": "main",
               "event": "push", "path": ".github/workflows/release.yml"}
        artifacts = {"artifacts": [{"name": "release-state", "expired": False},
                                    {"name": "homebrew-state", "expired": False}]}
        with patch.object(ci, "github", side_effect=[run, artifacts]):
            self.assertEqual(ci.recovery_artifact("123"), "homebrew-state")
        for key, value in (("head_branch", "feature"), ("event", "pull_request"),
                           ("repository", {"full_name": "other/repo"}), ("path", ".github/workflows/other.yml")):
            with patch.object(ci, "github", return_value=dict(run, **{key: value})):
                with self.assertRaises(ValueError): ci.recovery_artifact("123")

    def test_only_release_job_has_signing_secrets_and_recovery_keeps_exact_bytes(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/release.yml").read_text()
        verify, publish = workflow.split("\n  release:\n", 1)
        self.assertNotIn("DEVELOPER_ID_CERTIFICATE", verify)
        self.assertNotIn("SPARKLE_PRIVATE_KEY", verify)
        self.assertIn("cancel-in-progress: false", publish)
        self.assertIn("--plan", publish)
        self.assertIn("RELEASE_TARGET:", publish)
        self.assertIn("Recheck approval and version inside the single writer", publish)
        self.assertIn("steps.publish.outputs.complete != 'true'", publish)
        self.assertLess(publish.index("Check migrated credentials"), publish.index("Reserve the direct-release identity"))
        self.assertIn("PositionBar-*.dmg", publish)
        self.assertNotIn("PositionBar-*.zip", publish)


if __name__ == "__main__": unittest.main()
