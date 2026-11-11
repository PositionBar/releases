# PositionBar releases

Official [Universal DMGs](https://github.com/PositionBar/releases/releases), signed Sparkle appcasts and changelogs. PositionBar requires macOS 26 or later and uses Developer ID signing and Apple notarization.

This repository only executes releases. Version metadata lives in the private application repository at `versions/<version>.json`. A bot-authored version-only PR must receive owner approval for its exact head and merge into private main. The source relay dispatches this repository's main `release.yml` with its version and exact merge SHA; the executor independently verifies both before allocating a Build ID.

```text
.github/workflows/release.yml
scripts/ci.py       # exact approval, source, metadata and recovery
scripts/publish.py  # immutable public bytes, completion receipt, website Hook
scripts/cask.py     # write the verified DMG's Homebrew cask
tests/            # basic pure function tests only
```

The approved source contains the native build/sign/notarize implementation. One workflow builds the arm64/x86_64 DMG, signs all ten notes and the feed, verifies public bytes and the notarized download, updates [the tap](https://github.com/PositionBar/homebrew-tap), selects latest, verifies the canonical feed, and uploads `release.json` last. The tag `positionbar-<version>-<build>` points to the original public executor commit. There is no release-plan directory, aggregate index or tap ledger.

Assets: `PositionBar-V-B.dmg`, English `PositionBar-V-B.txt`, nine `PositionBar-V-B.<locale>.txt`, signed `appcast.xml`, and final `release.json` containing version/build/source, metadata SHA, date/changes, artifact facts, feed SHA and tap commit. Website builds statically project only matching completed versions; imported 0.0.x history is never packaged.

Secrets: `GH_PAT`, `TAP_WRITE_TOKEN`, `DEVELOPER_ID_CERTIFICATE_P12_BASE64`, `DEVELOPER_ID_CERTIFICATE_PASSWORD`, `APP_STORE_API_PRIVATE_KEY`, `SPARKLE_PRIVATE_KEY`, `WEBSITE_DEPLOY_HOOK`. Variables: `APP_STORE_API_KEY_ID`, `APP_STORE_API_ISSUER_ID`. The API key is used for notarization, not TestFlight. The website Hook must target the existing Cloudflare main production trigger. Its build_uuid is queue acceptance, not proof of deployment.

Manual dispatch uses the same version/source validation. `resume_run_id` must identify a completed run with a new-format `release-checkpoint`; it preserves original identity, DMG and signed bytes. No verified DMG means a fresh attempt and a new Build ID. Do not rerun an allocation or clobber existing assets. A completed matching release skips packaging and only re-notifies the website. Old-format checkpoints are not supported.

Current public 0.1.1 (1073) remains available until the new baseline, tap, feed and website take over. Remove old plan-only required check contexts when activating this manual-only workflow; retain owner review and no-force-push protections. Maintenance PRs do not run App publication.

Only basic metadata/approval and cask rendering functions have local tests. When changing them, run `PYTHONPATH=tests python3 -B -m unittest test_ci test_cask -q` once within the shared 60-second local budget. No default regression, unsigned prebuild or UI harness. Formal signing, notarization and publication integrity checks remain mandatory.
