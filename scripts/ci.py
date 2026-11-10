#!/usr/bin/env python3
"""Validate release PRs, main-source snapshots and retained release checkpoints."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "PositionBar/releases"
SOURCE = "sorcererxw/positionbar"
LOCALES = ("en", "zh-Hans", "zh-Hant", "ko", "ru", "pt-BR", "tr", "ja")
VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def github(endpoint, *, pages=False):
    args = ["gh", "api", endpoint]
    if pages:
        args += ["--paginate", "--slurp"]
    result = subprocess.run(args, capture_output=True, text=True)
    require(result.returncode == 0, "GitHub release inputs could not be verified")
    value = json.loads(result.stdout)
    return [item for page in value for item in page] if pages else value


def validate_plan(plan, filename):
    require(isinstance(plan, dict) and set(plan) == {"schemaVersion", "version", "source_sha", "changes"}
            and type(plan["schemaVersion"]) is int and plan["schemaVersion"] == 1,
            "Use only schemaVersion, version, source_sha and changes")
    require(isinstance(plan["version"], str) and re.fullmatch(VERSION, plan["version"]),
            "Version must be three numbers without leading zeroes")
    require(filename == f"release-plans/{plan['version']}.json", "Plan filename must match its version")
    require(isinstance(plan["source_sha"], str) and re.fullmatch(r"[a-f0-9]{40}", plan["source_sha"]),
            "source_sha must be a full private main commit")
    changes = plan["changes"]
    require(isinstance(changes, list) and 0 < len(changes) <= 3, "Provide one to three change categories")
    seen = set()
    for group in changes:
        require(isinstance(group, dict) and set(group) == {"type", "items"}, "Invalid change category")
        kind, items = group["type"], group["items"]
        require(isinstance(kind, str) and kind in ("new", "improved", "fixed") and kind not in seen,
                "Change categories must be new, improved or fixed, without duplicates")
        seen.add(kind)
        require(isinstance(items, dict) and set(items) == set(LOCALES), "Provide all eight changelog locales")
        for lines in items.values():
            require(isinstance(lines, list) and lines and all(isinstance(line, str) and line.strip()
                    and "\n" not in line and "\r" not in line for line in lines),
                    "Each locale needs nonempty, single-line changelog items")
    return plan


def changed_plan(files):
    plans = [item for item in files if item["filename"].startswith("release-plans/")]
    if not plans:
        return None
    require(len(files) == len(plans) == 1 and plans[0]["status"] == "added"
            and re.fullmatch(r"release-plans/" + VERSION + r"\.json", plans[0]["filename"]),
            "A release PR must only add one versioned plan; approved plans cannot be edited or deleted")
    return plans[0]


def approved(pr, reviews):
    latest = {}
    for review in reviews:
        if review.get("state") in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            latest[review["user"]["login"]] = review
    review = latest.get("sorcererxw", {})
    require(pr["user"]["login"] != "sorcererxw" and review.get("state") == "APPROVED"
            and review.get("commit_id") == pr["head"]["sha"],
            "The owner must approve this exact PR head; use a bot-authored release PR")


def pr_plan(number, *, merged=False):
    require(str(number).isdigit() and int(number) > 0, "Invalid release PR number")
    endpoint = f"repos/{REPOSITORY}/pulls/{number}"
    pr = github(endpoint)
    file = changed_plan(github(endpoint + "/files?per_page=100", pages=True))
    if file is None:
        return None
    require(pr["base"]["ref"] == "main" and pr["head"]["repo"]["full_name"] == REPOSITORY,
            "Release plans must be same-repository PRs targeting main")
    if merged:
        require(pr.get("merged") is True, "Publication requires a merged release PR")
        approved(pr, github(endpoint + "/reviews?per_page=100", pages=True))
    path = ROOT / file["filename"]
    require(path.is_file() and not path.is_symlink(), "Release plan must be a regular JSON file")
    raw = path.read_bytes()
    blob = hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()
    require(blob == file["sha"], "Checked-out plan differs from the reviewed PR file")
    plan = validate_plan(json.loads(raw), file["filename"])
    return dict(plan=plan, path=file["filename"], sha256=hashlib.sha256(raw).hexdigest(),
                target=pr["merge_commit_sha"] if merged else pr["head"]["sha"])


def publication_state(value, *, merged, resume):
    version = value["plan"]["version"]
    versions = []
    partial = False
    for release in github(f"repos/{REPOSITORY}/releases?per_page=100", pages=True):
        if release.get("draft") or release.get("prerelease"):
            continue
        match = re.fullmatch(r"positionbar-(" + VERSION + r")-([1-9][0-9]*)", release["tag_name"])
        require(match, "An existing release has an unknown identity")
        current = match[1]
        if current == version:
            require(merged, "This version has already been published")
            record = next((a for a in release["assets"] if a["name"] == "release.json"), None)
            if record:
                url = f"https://github.com/{REPOSITORY}/releases/download/{release['tag_name']}/release.json"
                with urllib.request.urlopen(url, timeout=30) as response:
                    published = json.load(response)
                require(published["releasePlanSHA256"] == value["sha256"], "Published plan differs")
                return "complete"
            partial = True
        else:
            versions.append(tuple(map(int, current.split("."))))
    require(not versions or tuple(map(int, version.split("."))) > max(versions),
            "Version must be newer than every published release")
    if partial:
        require(resume, "Partial publication exists; restore its original run instead of rebuilding")
        return "resume"
    return "ready"


def event_plan():
    require(os.environ.get("GITHUB_REPOSITORY") == REPOSITORY, "Unexpected release repository")
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    name = os.environ["GITHUB_EVENT_NAME"]
    merged = name != "pull_request"
    if name == "pull_request":
        value = pr_plan(event["number"])
    elif name == "workflow_dispatch":
        require(os.environ.get("GITHUB_REF") == "refs/heads/main", "Recovery must run on main")
        value = pr_plan(event["inputs"]["release_pr"], merged=True)
        require(value, "Recovery PR contains no release plan")
    else:
        require(name == "push" and os.environ.get("GITHUB_REF") == "refs/heads/main", "Unexpected release event")
        sha = os.environ["GITHUB_SHA"]
        prs = github(f"repos/{REPOSITORY}/commits/{sha}/pulls?per_page=100", pages=True)
        merged_prs = [pr for pr in prs if pr.get("merged_at") and pr.get("merge_commit_sha") == sha
                      and pr["base"]["ref"] == "main"]
        require(len(merged_prs) <= 1, "Ambiguous merged release PR")
        value = pr_plan(merged_prs[0]["number"], merged=True) if merged_prs else None
        if value is None:
            diff = subprocess.run(["git", "diff", "--name-only", event["before"], sha, "--", "release-plans"],
                                  cwd=ROOT, capture_output=True, text=True, check=True)
            require(not diff.stdout.strip(), "Release plans require an approved, squashed PR")
    if value is None:
        return {"release": "false"}
    state = publication_state(value, merged=merged, resume=bool(event.get("inputs", {}).get("resume_run_id")))
    return {"release": "true", "complete": str(state == "complete").lower(), "plan": value["path"],
            "source_sha": value["plan"]["source_sha"], "version": value["plan"]["version"],
            "target": value["target"]}


def verify_source(sha):
    require(re.fullmatch(r"[a-f0-9]{40}", sha), "Invalid source SHA")
    comparison = github(f"repos/{SOURCE}/compare/{sha}...main")
    require(comparison.get("status") in ("ahead", "identical")
            and comparison.get("merge_base_commit", {}).get("sha") == sha,
            "The approved source must be a commit on private main")


def recovery_artifact(run_id):
    require(re.fullmatch(r"[1-9][0-9]{0,19}", run_id), "Invalid recovery run ID")
    run = github(f"repos/{REPOSITORY}/actions/runs/{run_id}")
    require(run.get("repository", {}).get("full_name") == REPOSITORY and run.get("head_branch") == "main"
            and run.get("event") in ("push", "workflow_dispatch")
            and run.get("path") == ".github/workflows/release.yml", "Recovery must use this main release workflow")
    artifacts = github(f"repos/{REPOSITORY}/actions/runs/{run_id}/artifacts?per_page=100")
    live = {item["name"] for item in artifacts["artifacts"] if not item["expired"]}
    for name in ("homebrew-state", "release-state"):
        if name in live:
            return name
    raise ValueError("No retained checkpoint is available")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "source", "recovery"))
    parser.add_argument("--sha")
    parser.add_argument("--run-id")
    args = parser.parse_args()
    if args.command == "plan":
        for key, value in event_plan().items():
            print(f"{key}={value}")
    elif args.command == "source":
        verify_source(args.sha)
    elif args.command == "recovery":
        print("artifact=" + recovery_artifact(args.run_id))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as error:
        print(str(error) if isinstance(error, ValueError) else type(error).__name__, file=sys.stderr)
        sys.exit(1)
