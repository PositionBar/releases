"""Write one cask after the release publisher verifies the public DMG and signed feed."""
from pathlib import Path
import re
import subprocess


def require(condition, message):
    if not condition:
        raise ValueError(message)


def render_cask(receipt):
    artifact = receipt["artifact"]
    require(artifact["minimumSystemVersion"] == "26.0.0" and artifact["architectures"] == ["arm64", "x86_64"],
            "Expected macOS 26 Universal DMG")
    return '\n'.join([
        'cask "positionbar" do',
        f'  version "{receipt["version"]},{receipt["build"]}"',
        f'  sha256 "{artifact["sha256"]}"', '',
        f'  url "{artifact["url"]}"',
        '  name "PositionBar"',
        '  desc "Read-only multi-account position monitor"',
        '  homepage "https://positionbar.com/"', '',
        '  auto_updates true',
        '  depends_on macos: :tahoe', '',
        '  app "PositionBar.app"', 'end', ''])


def cask_identity(contents):
    version = re.search(r'^  version "([0-9]+\.[0-9]+\.[0-9]+),([1-9][0-9]*)"$', contents, re.MULTILINE)
    sha = re.search(r'^  sha256 "([a-f0-9]{64})"$', contents, re.MULTILINE)
    url = re.search(r'^  url "([^"]+)"$', contents, re.MULTILINE)
    require(version and sha and url, "Existing cask identity is invalid")
    return tuple(map(int, version[1].split('.'))) + (int(version[2]),), url[1], sha[1]


def publish(root, receipt, environment):
    def git(root, *args):
        result = subprocess.run(["git", "-C", str(root), *map(str, args)], capture_output=True, text=True, env=environment)
        require(result.returncode == 0, "Tap Git operation failed; verify origin/main before retrying")
        return result.stdout.strip()

    root = Path(root).resolve()
    require(git(root, "remote", "get-url", "origin") in (
        "https://github.com/PositionBar/homebrew-tap", "https://github.com/PositionBar/homebrew-tap.git"),
        "Unexpected tap remote")
    require(git(root, "branch", "--show-current") == "main", "Publish cask only on tap main")
    status = git(root, "status", "--porcelain=v1", "--untracked-files=all")
    require(not status, "Tap checkout must be clean; recover from the package checkpoint")
    git(root, "fetch", "origin", "main")
    head, remote = git(root, "rev-parse", "HEAD"), git(root, "rev-parse", "origin/main")
    contents = render_cask(receipt)
    if head != remote:
        behind = subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", head, remote],
                                capture_output=True, env=environment).returncode == 0
        if behind:
            git(root, "merge", "--ff-only", "origin/main")
        else:
            require(git(root, "rev-list", "--count", f"{remote}..{head}") == "1"
                    and git(root, "rev-parse", "HEAD^") == remote
                    and git(root, "diff", "--name-only", f"{remote}..{head}") == "Casks/positionbar.rb"
                    and git(root, "show", "HEAD:Casks/positionbar.rb") == contents.strip(),
                    "Tap main diverged; do not overwrite another publication")
    path = root / "Casks/positionbar.rb"
    if path.exists():
        current = cask_identity(path.read_text())
        candidate = cask_identity(contents)
        require(candidate[0] >= current[0], "Refusing to downgrade the cask")
        require(candidate[0] != current[0] or candidate == current, "An existing version's URL and SHA are immutable")
    if not path.exists() or path.read_text() != contents:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.rb.tmp')
        temporary.write_text(contents)
        temporary.replace(path)
        git(root, "add", "Casks/positionbar.rb")
        git(root, "commit", "-m", f"Publish PositionBar {receipt['version']} ({receipt['build']})")
    commit = git(root, "rev-parse", "HEAD")
    if commit != git(root, "rev-parse", "origin/main"):
        git(root, "push", "origin", "HEAD:main")
    require(git(root, "ls-remote", "origin", "refs/heads/main").split()[0] == commit,
            "Tap push outcome is uncertain; read origin/main before retrying")
    require(git(root, "show", "HEAD:Casks/positionbar.rb") == contents.strip(), "Published cask differs")
    return git(root, "log", "-1", "--format=%H", "--", "Casks/positionbar.rb")
