"""Write one cask after the release publisher verifies the public DMG and signed feed."""
import base64
import re

from ci import file_contents, git_blob_sha, require


def render_cask(receipt):
    artifact = receipt["artifact"]
    require(artifact["minimumSystemVersion"] == "26.0.0" and artifact["architectures"] == ["arm64", "x86_64"],
            "Expected macOS 26 Universal DMG")
    return '\n'.join([
        'cask "positionbar" do',
        f'  version "{receipt["version"]}"',
        f'  sha256 "{artifact["sha256"]}"', '',
        f'  url "{artifact["url"]}"',
        '  name "PositionBar"',
        '  desc "Portfolio tracker for the macOS menu bar"',
        '  homepage "https://positionbar.com/"', '',
        '  auto_updates true',
        '  depends_on macos: :tahoe', '',
        '  app "PositionBar.app"', 'end', ''])


def cask_identity(contents, version_pattern):
    version = re.search(r'^  version "([^",\r\n]+)(?:,[1-9][0-9]*)?"$', contents, re.MULTILINE)
    sha = re.search(r'^  sha256 "([a-f0-9]{64})"$', contents, re.MULTILINE)
    url = re.search(r'^  url "([^"]+)"$', contents, re.MULTILINE)
    require(version and re.fullmatch(version_pattern, version[1]) and sha and url, "Existing cask identity is invalid")
    return tuple(map(int, version[1].split('.'))), url[1], sha[1]


def publish(receipt, version_pattern, api):
    endpoint = "repos/PositionBar/homebrew-tap"
    path = "Casks/positionbar.rb"
    contents_url = f"{endpoint}/contents/{path}"

    def last_change():
        commits = api(f"{endpoint}/commits?sha=main&path={path}&per_page=1")
        require(isinstance(commits, list), "Tap commit history could not be verified")
        if not commits:
            return None
        sha = commits[0].get("sha") if isinstance(commits[0], dict) else None
        require(isinstance(sha, str) and re.fullmatch(r"[a-f0-9]{40}", sha), "Invalid tap commit")
        return sha

    def contents_at(commit):
        result = api(f"{contents_url}?ref={commit}") if commit else None
        return file_contents(result) if result is not None else None

    previous_commit = last_change()
    previous = contents_at(previous_commit)
    contents = render_cask(receipt)
    raw = contents.encode("utf-8")
    candidate = cask_identity(contents, version_pattern)
    if previous == raw:
        require(last_change() == previous_commit, "Tap cask changed during verification")
        return previous_commit

    committer = {"name": "github-actions[bot]", "email": "41898282+github-actions[bot]@users.noreply.github.com"}
    body = {
        "branch": "main", "message": f"Publish PositionBar {receipt['version']} ({receipt['build']})",
        "content": base64.b64encode(raw).decode("ascii"), "committer": committer,
    }
    if previous is not None:
        current = cask_identity(previous.decode("utf-8"), version_pattern)
        require(candidate[0] >= current[0], "Refusing to downgrade the cask")
        require(candidate[0] != current[0] or candidate == current, "An existing version's URL and SHA are immutable")
        body["sha"] = git_blob_sha(previous)

    result = api(contents_url, method="PUT", body=body)
    outcome = result if isinstance(result, dict) else {}
    commit = (outcome.get("commit") or {}).get("sha")
    require(isinstance(commit, str) and re.fullmatch(r"[a-f0-9]{40}", commit)
            and (outcome.get("content") or {}).get("sha") == git_blob_sha(raw),
            "Tap write outcome is uncertain; inspect the cask before retrying")
    require(last_change() == commit and contents_at(commit) == raw, "Published tap cask differs")
    return commit
