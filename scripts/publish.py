#!/usr/bin/env python3
"""Publish the verified DMG, cask and signed update data outside Release assets."""
import argparse
import base64
from contextlib import nullcontext
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import cask


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def command(args, *, environment):
    result = subprocess.run(list(map(str, args)), capture_output=True, text=True, env=environment)
    require(result.returncode == 0, "GitHub publication failed; inspect public state before retrying")


def github_environment(token):
    require(token, "Missing GitHub publication credential")
    environment = {key: value for key, value in os.environ.items()
                   if key not in ("GH_TOKEN", "GITHUB_TOKEN", "GH_PAT", "TAP_WRITE_TOKEN")
                   and not key.startswith("ACTIONS_")}
    environment.update(GH_TOKEN=token, GH_PROMPT_DISABLED="1", GIT_TERMINAL_PROMPT="0",
                       GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="credential.helper",
                       GIT_CONFIG_VALUE_0="!gh auth git-credential")
    return environment


def github_json(environment, endpoint, *, method="GET", body=None):
    args = ["gh", "api", "--method", method, endpoint]
    if body is not None:
        args += ["--input", "-"]
    result = subprocess.run(
        args,
        input=json.dumps(body) if body is not None else None,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode == 0:
        return json.loads(result.stdout)
    if method == "GET" and "HTTP 404" in result.stderr:
        return None
    raise ValueError("GitHub release state could not be verified")

def release_state(config, environment, tag):
    repository = config["github_repository"]
    encoded = urllib.parse.quote(tag, safe="")
    value = github_json(environment, f"repos/{repository}/releases/tags/{encoded}")
    if value is None:
        return None
    return {
        "draft": value.get("draft") is True,
        "prerelease": value.get("prerelease") is True,
        "assets": value.get("assets", []),
        "published_at": value.get("published_at"),
    }

def public_digest(url, expected_length=None, expected_sha256=None, destination=None):
    parsed = urllib.parse.urlparse(url)
    require(parsed.scheme == "https" and parsed.hostname in ("github.com", "positionbar.com"), "Unexpected public asset URL")
    hosts = ("github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com") \
        if parsed.hostname == "github.com" else ("positionbar.com",)
    request = urllib.request.Request(url, headers={"User-Agent": "PositionBar-cask-release"})
    temporary = Path(str(destination) + ".download") if destination else None
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            final = urllib.parse.urlparse(response.geturl())
            require(
                final.scheme == "https"
                and final.hostname in hosts,
                "Public asset redirected outside its trusted host",
            )
            require(response.status == 200, "Public cask artifact is unavailable")
            value = hashlib.sha256()
            length = 0
            with temporary.open("wb") if temporary else nullcontext() as target:
                for block in iter(lambda: response.read(1024 * 1024), b""):
                    value.update(block)
                    length += len(block)
                    if target:
                        target.write(block)
        actual = value.hexdigest()
        require(expected_length is None or length == expected_length, "Public cask artifact length differs")
        require(expected_sha256 is None or actual == expected_sha256, "Published cask artifact bytes differ")
        if temporary:
            temporary.replace(destination)
        return actual, length
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)

def verify_release_asset(config, environment, tag, name, sha256, length):
    state = release_state(config, environment, tag)
    require(state is not None, "GitHub release was not created")
    require(not state["draft"] and not state["prerelease"], "Cask release must be public stable release")
    matches = [asset for asset in state["assets"] if asset.get("name") == name]
    require(len(matches) <= 1, "Cask release contains duplicate assets")
    if not matches:
        return None
    asset = matches[0]
    url = asset.get("browser_download_url")
    require(url == release_asset_url(config["github_repository"], tag, name),
            "GitHub release asset URL differs")
    require(int(asset.get("size", 0)) == length, "GitHub release asset length differs")
    public_digest(url, length, sha256)
    return url

def release_asset_url(repository, tag, name):
    return f"https://github.com/{repository}/releases/download/{tag}/{name}"

def ensure_release_assets(config, environment, tag, assets, title, body):
    repository = config["github_repository"]
    state = release_state(config, environment, tag)
    if state is None:
        try:
            command(
                [
                    "gh",
                    "release",
                    "create",
                    tag,
                    *assets,
                    "--repo",
                    repository,
                    "--target",
                    config["release_target"],
                    "--title",
                    title,
                    "--notes",
                    body,
                    "--latest=false",
                ],
                environment=environment,
            )
        except ValueError as error:
            state = release_state(config, environment, tag)
            if state is None:
                raise ValueError("GitHub release creation outcome is uncertain; check the public Release") from error
    for _attempt in range(2):
        state = release_state(config, environment, tag)
        require(state is not None, "GitHub release was not created")
        require(not state["draft"] and not state["prerelease"], "Homebrew release must be public and stable")
        existing = {asset.get("name") for asset in state["assets"]}
        missing = [path for path in assets if Path(path).name not in existing]
        if not missing:
            break
        try:
            command(
                ["gh", "release", "upload", tag, *missing, "--repo", repository],
                environment=environment,
            )
        except ValueError as error:
            state = release_state(config, environment, tag)
            names = {asset.get("name") for asset in state["assets"]} if state else set()
            if any(Path(path).name not in names for path in missing):
                raise ValueError("GitHub release asset upload outcome is uncertain; check the public Release") from error
    urls = {}
    for path in assets:
        path = Path(path)
        url = verify_release_asset(config, environment, tag, path.name, digest(path), path.stat().st_size)
        require(url, f"GitHub release asset is missing: {path.name}")
        urls[path.name] = url
    return urls

def ensure_latest_release(config, environment, tag, build):
    endpoint = f"repos/{config['github_repository']}/releases/latest"
    latest = github_json(environment, endpoint)
    if latest is not None:
        match = re.fullmatch(r"positionbar-[0-9]+\.[0-9]+\.[0-9]+-([0-9]+)", latest.get("tag_name", ""))
        require(match is not None, "Latest tap release has an unknown identity")
        require(int(match[1]) <= int(build), "A newer Homebrew release is already latest; do not roll it back")
    if latest is None or latest.get("tag_name") != tag:
        try:
            command(["gh", "release", "edit", tag, "--repo", config["github_repository"], "--latest"],
                    environment=environment)
        except ValueError as error:
            latest = github_json(environment, endpoint)
            if latest is None or latest.get("tag_name") != tag:
                raise ValueError("Latest release update outcome is uncertain; check the public Release") from error
        latest = github_json(environment, endpoint)
        require(latest is not None and latest.get("tag_name") == tag, "Latest release did not select this build")


def update_entries(files, existing):
    entries = []
    for name, raw in files.items():
        sha = hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()
        require(name == "appcast.xml" or name not in existing or existing[name] == sha,
                "Published notes or completion receipt differ; recover the original checkpoint")
        if existing.get(name) != sha:
            entries.append({"path": name, "mode": "100644", "type": "blob", "content": raw.decode("utf-8")})
    return entries


def publish_updates(environment, files, build):
    endpoint = "repos/PositionBar/releases"
    ref = github_json(environment, endpoint + "/git/ref/heads/updates")
    parent = ref["object"]["sha"] if ref else None
    tree, existing = None, {}
    if parent:
        tree = github_json(environment, endpoint + f"/git/commits/{parent}")["tree"]["sha"]
        listing = github_json(environment, endpoint + f"/git/trees/{tree}?recursive=1")
        require(not listing.get("truncated"), "Update data tree is incomplete")
        existing = {item["path"]: item["sha"] for item in listing["tree"]}
        if "appcast.xml" in existing:
            blob = github_json(environment, endpoint + f"/git/blobs/{existing['appcast.xml']}")
            feed = ET.fromstring(base64.b64decode(blob["content"]))
            previous = feed.findall("./channel/item/{http://www.andymatuschak.org/xml-namespaces/sparkle}version")
            require(len(previous) == 1 and int(previous[0].text) <= build, "Do not roll back a newer update feed")
            if int(previous[0].text) == build and "appcast.xml" in files:
                raw = files["appcast.xml"]
                require(existing["appcast.xml"] == hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest(),
                        "A published build must retain its exact signed feed")
    entries = update_entries(files, existing)
    current = parent
    if entries:
        body = {"tree": entries}
        if tree:
            body["base_tree"] = tree
        new_tree = github_json(environment, endpoint + "/git/trees", method="POST", body=body)["sha"]
        commit = github_json(environment, endpoint + "/git/commits", method="POST", body={
            "message": f"Publish signed macOS update {build}", "tree": new_tree,
            "parents": [parent] if parent else [],
        })["sha"]
        # Ref updates are atomic and never force over concurrent publications.
        if parent:
            github_json(environment, endpoint + "/git/refs/heads/updates", method="PATCH", body={"sha": commit, "force": False})
        else:
            github_json(environment, endpoint + "/git/refs", method="POST", body={"ref": "refs/heads/updates", "sha": commit})
        current = commit
    # Verify the immutable commit; branch reads can lag a successful ref update.
    current_tree = github_json(environment, endpoint + f"/git/commits/{current}")["tree"]["sha"]
    actual = github_json(environment, endpoint + f"/git/trees/{current_tree}?recursive=1")
    require(not actual.get("truncated"), "Published update tree is incomplete")
    require(not update_entries(files, {item["path"]: item["sha"] for item in actual["tree"]}),
            "Published update bytes differ")


def native_tools(source):
    sys.path.insert(0, str(Path(source).resolve() / "scripts"))
    import macos_release
    return macos_release


def publish(state, source, tap):
    native = native_tools(source)
    native.require_ci()
    context = json.loads((state / "request.json").read_bytes())
    native.checkout_revision(context["source_sha"])
    version = context["version"]
    metadata_path = Path(source) / "versions" / f"{version}.json"
    plan = native.versions.load(metadata_path)
    require(not native.versions.historical(version) and digest(metadata_path) == context["metadataSHA256"]
            and metadata_path.read_bytes() == (state / "version.json").read_bytes(), "Frozen version metadata differs")
    output = state / "package"
    receipt = json.loads((output / "package.json").read_bytes())
    require(receipt.get("status") == "packaged" and receipt.get("registryStatus") == "succeeded"
            and receipt.get("version") == version
            and all(receipt.get(key) == context[key] for key in ("source_sha", "metadataSHA256")),
            "Package checkpoint is not complete or belongs to another release")
    build = receipt["build"]
    require(type(build) is int and build > 0, "Invalid Build ID")
    tag = f"positionbar-{version}-{build}"
    config = {"github_repository": "PositionBar/releases", "release_target": context["releaseTarget"]}
    environment = github_environment(os.environ.get("GH_TOKEN"))
    identity = native.load_identity(state / "identity/build-identity.plist", context["source_sha"])
    require(identity["CFBundleVersion"] == str(build) and identity["CFBundleShortVersionString"] == version,
            "Registered identity differs")
    artifact = output / f"PositionBar-{version}-{build}.dmg"
    expected_url = release_asset_url(config["github_repository"], tag, artifact.name)
    require(receipt["artifact"].get("url") == expected_url and digest(artifact) == receipt["artifact"]["sha256"]
            and artifact.stat().st_size == receipt["artifact"]["length"], "Checkpoint DMG bytes differ")
    feed, notes = output / "appcast.xml", output / f"PositionBar-{version}-{build}.txt"
    paths = [notes, *(notes.with_suffix(f".{locale}.txt") for locale in native.versions.LOCALES[1:]), feed]
    native.verify_appcast_file(feed, notes, artifact, version, build, native.MINIMUM, native.UPDATES,
                              expected_url.rsplit('/', 1)[0] + '/', plan)
    title, body = f"PositionBar {version} ({build})", native.versions.notes(plan)
    ensure_release_assets(config, environment, tag, [artifact], title, body)
    require({asset.get("name") for asset in release_state(config, environment, tag)["assets"]} == {artifact.name},
            "New GitHub Releases must contain only the DMG")
    target = github_json(environment, f"repos/PositionBar/releases/git/ref/tags/{tag}")
    require(target and target.get("object", {}).get("type") == "commit"
            and target["object"].get("sha") == context["releaseTarget"], "Release tag target differs from the original executor")
    # Reverify the public bytes before either distribution entry point changes.
    with tempfile.TemporaryDirectory(prefix="positionbar-public-") as temporary:
        downloaded = Path(temporary) / artifact.name
        public_digest(expected_url, artifact.stat().st_size, digest(artifact), downloaded)
        facts = native.verify_notarized_dmg(downloaded, version, identity, native.UPDATES)
        require(all(receipt["artifact"].get(key) == value for key, value in facts.items()), "Public DMG facts differ")
    tap_environment = github_environment(os.environ.get("TAP_WRITE_TOKEN"))
    tap_commit = cask.publish(tap, receipt, tap_environment)
    ensure_latest_release(config, environment, tag, build)
    release = release_state(config, environment, tag)
    published_on = dt.datetime.fromisoformat(release["published_at"].replace('Z', '+00:00')).date().isoformat()
    record = {"status": "published", "version": version, "build": build,
              "source_sha": context["source_sha"], "metadataSHA256": context["metadataSHA256"],
              "publishedOn": published_on, "changes": plan["changes"], "artifact": receipt["artifact"],
              "appcast": native.UPDATES["feedURL"],
              "appcastSHA256": digest(feed), "appcastLength": feed.stat().st_size,
              "tapCommit": tap_commit, "releaseTarget": context["releaseTarget"]}
    marker = output / "release.json"
    if marker.exists():
        require(json.loads(marker.read_bytes()) == record, "Completion checkpoint differs")
    else:
        native.write_json(marker, record)
    files = {path.name: path.read_bytes() for path in paths}
    publish_updates(environment, files, build)
    public_digest(native.UPDATES["feedURL"], feed.stat().st_size, digest(feed))
    for path in paths[:-1]:
        public_digest(native.UPDATES["feedURL"].rsplit('/', 1)[0] + '/' + path.name, path.stat().st_size, digest(path))
    publish_updates(environment, {f"releases/{version}.json": marker.read_bytes()}, build)
    print(f"Published {version} ({build}) from {context['source_sha']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--tap", type=Path)
    args = parser.parse_args()
    require(args.source and args.tap, "Provide source and tap checkouts")
    publish(args.state.resolve(), args.source.resolve(), args.tap.resolve())


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError, ET.ParseError, subprocess.SubprocessError) as error:
        print(str(error) if isinstance(error, ValueError) else type(error).__name__, file=sys.stderr)
        sys.exit(1)
