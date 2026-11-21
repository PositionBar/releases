#!/usr/bin/env python3
"""Accept one approved version file from an exact private-main PR merge."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

REPOSITORY = "PositionBar/releases"
SOURCE = "sorcererxw/positionbar"


def require(condition, message):
    if not condition:
        raise ValueError(message)

def release_tag(tag, version_pattern):
    match = re.fullmatch(r"positionbar-(.+)-([1-9][0-9]*)", tag)
    require(match and re.fullmatch(version_pattern, match[1]), "Unknown public release identity")
    return match[1], int(match[2])


def github(endpoint, *, pages=False, missing=False):
    args = ["gh", "api", endpoint]
    if pages:
        args += ["--paginate", "--slurp"]
    result = subprocess.run(args, capture_output=True, text=True)
    if missing and result.returncode != 0 and "HTTP 404" in result.stderr:
        return None
    require(result.returncode == 0, "GitHub release inputs could not be verified")
    value = json.loads(result.stdout)
    return [item for page in value for item in page] if pages else value


def git_blob_sha(raw):
    return hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()


def file_contents(content):
    require(content.get("type") == "file" and content.get("encoding") == "base64"
            and isinstance(content.get("content"), str), "GitHub contents must be a regular file")
    raw = base64.b64decode("".join(content["content"].splitlines()), validate=True)
    require(git_blob_sha(raw) == content.get("sha"), "GitHub contents differ from their blob SHA")
    return raw


def source_file(sha, path):
    content = github(f"repos/{SOURCE}/contents/{path}?ref={sha}")
    return file_contents(content), content["sha"]


def approved(pr, reviews):
    latest = {}
    for review in reviews:
        if review.get("state") in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            latest[review["user"]["login"]] = review
    review = latest.get("sorcererxw", {})
    require(pr["user"]["login"] != "sorcererxw" and review.get("state") == "APPROVED"
            and review.get("commit_id") == pr["head"]["sha"],
            "The owner must approve this exact PR head; use a bot-authored release PR")

def verify_source(sha):
    require(re.fullmatch(r"[a-f0-9]{40}", sha), "Invalid source SHA")
    comparison = github(f"repos/{SOURCE}/compare/{sha}...main")
    require(comparison.get("status") in ("ahead", "identical")
            and comparison.get("merge_base_commit", {}).get("sha") == sha,
            "The approved source must be a commit on private main")


def changed_version(files, version):
    require(len(files) == 1 and files[0].get("status") == "added"
            and files[0].get("filename") == f"versions/{version}.json",
            "A release PR must only add its version JSON")
    return files[0]


def completion_receipt(raw, request, build):
    receipt = json.loads(raw)
    require(isinstance(receipt, dict) and receipt.get("status") == "published"
            and type(receipt.get("build")) is int and receipt["build"] == build
            and all(receipt.get(key) == request[key] for key in ("version", "source_sha", "metadataSHA256")),
            "Existing completion receipt differs")
    return receipt


def request():
    require(os.environ.get("GITHUB_REPOSITORY") == REPOSITORY
            and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and os.environ.get("GITHUB_REF") == "refs/heads/main", "Dispatch the release workflow on main")
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_bytes())
    inputs = event["inputs"]
    version, sha = inputs["version"], inputs["source_sha"]
    verify_source(sha)
    prs = github(f"repos/{SOURCE}/commits/{sha}/pulls?per_page=100", pages=True)
    matches = [pr for pr in prs if pr.get("merged_at") and pr.get("merge_commit_sha") == sha
               and pr["base"]["ref"] == "main" and pr["head"]["repo"]["full_name"] == SOURCE]
    require(len(matches) == 1, "Source must be the exact merge of one same-repository version PR")
    endpoint = f"repos/{SOURCE}/pulls/{matches[0]['number']}"
    pr = github(endpoint)
    approved(pr, github(endpoint + "/reviews?per_page=100", pages=True))
    file = changed_version(github(endpoint + "/files?per_page=100", pages=True), version)
    schema, _ = source_file(sha, "schemas/version.schema.json")
    version_pattern = re.compile(json.loads(schema)["properties"]["version"]["pattern"])
    require(isinstance(version, str) and version_pattern.fullmatch(version), "Invalid version")
    require(not version.startswith("0.0."), "0.0.x is history, not an App release")
    raw, blob = source_file(sha, file["filename"])
    require(blob == file["sha"], "Reviewed metadata differs")
    value = {"version": version, "source_sha": sha, "metadataSHA256": hashlib.sha256(raw).hexdigest(),
             "sourcePR": pr["number"], "releaseTarget": os.environ["GITHUB_SHA"]}
    require(re.fullmatch(r"[a-f0-9]{40}", value["releaseTarget"]), "Invalid workflow revision")
    complete, partial, others = None, False, []
    for release in github(f"repos/{REPOSITORY}/releases?per_page=100", pages=True):
        if release.get("draft") or release.get("prerelease"):
            continue
        public_version, build = release_tag(release["tag_name"], version_pattern)
        if public_version != version:
            others.append(tuple(map(int, public_version.split("."))))
            continue
        require(not complete and not partial, "More than one public build exists for this version")
        marker = github(f"repos/{REPOSITORY}/contents/releases/{version}.json?ref=updates", missing=True)
        if marker:
            complete = completion_receipt(file_contents(marker), value, build)
        else:
            partial = True
    if not complete:
        require(not others or tuple(map(int, version.split("."))) > max(others), "Version must exceed all public versions")
        require(not partial or inputs.get("resume_run_id"), "Partial publication requires its exact checkpoint")
    return value, complete


def prepare_request(state, resume):
    value, complete = request()
    state.mkdir(parents=True, exist_ok=True)
    path = state / "request.json"
    if complete:
        return complete
    if resume:
        require(path.is_file(), "Missing recovery request")
        retained = json.loads(path.read_bytes())
        require(all(retained.get(key) == value[key] for key in ("version", "source_sha", "metadataSHA256", "sourcePR")),
                "Recovery version, source or approved metadata differs")
        require(re.fullmatch(r"[a-f0-9]{40}", retained.get("releaseTarget", "")), "Missing original tag target")
        original = github(f"repos/{REPOSITORY}/compare/{retained['releaseTarget']}...main")
        require(original.get("merge_base_commit", {}).get("sha") == retained["releaseTarget"], "Original executor is not on main")
    else:
        require(not path.exists() and os.environ.get("GITHUB_RUN_ATTEMPT") == "1",
                "Recover the checkpoint instead of rerunning a new allocation")
        path.write_text(json.dumps(value, indent=2) + "\n")
    return complete


def recovery_artifact(run_id):
    require(re.fullmatch(r"[1-9][0-9]{0,19}", run_id), "Invalid recovery run ID")
    run = github(f"repos/{REPOSITORY}/actions/runs/{run_id}")
    require(run.get("repository", {}).get("full_name") == REPOSITORY and run.get("head_branch") == "main"
            and run.get("event") == "workflow_dispatch" and run.get("path") == ".github/workflows/release.yml"
            and run.get("status") == "completed", "Recover only a completed run of this main workflow")
    artifacts = github(f"repos/{REPOSITORY}/actions/runs/{run_id}/artifacts?per_page=100")
    require(any(item["name"] == "release-checkpoint" and not item["expired"] for item in artifacts["artifacts"]),
            "No checkpoint from the new release architecture; start a fresh attempt")
    return "release-checkpoint"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("request", "prepare", "recovery"))
    parser.add_argument("--state", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--run-id")
    args = parser.parse_args()
    if args.command == "recovery":
        print("artifact=" + recovery_artifact(args.run_id))
    else:
        if args.command == "prepare":
            complete = prepare_request(args.state, args.resume)
        else:
            _, complete = request()
        print("complete=" + str(complete is not None).lower())


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as error:
        print(str(error) if isinstance(error, ValueError) else type(error).__name__, file=sys.stderr)
        sys.exit(1)
