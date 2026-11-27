# PositionBar releases

Official notarized [Universal DMGs](https://github.com/PositionBar/releases/releases) for macOS 26+. New GitHub Releases contain only `PositionBar-V-B.dmg`. Sparkle in-app updates remain signed with the existing Ed25519 key.

Version metadata lives in private `sorcererxw/positionbar/versions/<version>.json`: version, `publishedOn` date and ten-language changes. A bot-authored version-only PR needs owner approval for its exact head and must merge into private main. Merging version metadata does not start App publication. Dispatch this repository's main `release.yml` separately with the version and its version PR's exact merge SHA; the executor independently verifies approval, source and reviewed bytes before allocating a Build ID. No private-repository Actions runner or dispatch secret is needed.

```text
.github/workflows/release.yml
scripts/ci.py       # approval, reviewed file identity, public versions and recovery
scripts/publish.py  # approved plan validation and publication of signed artifacts
scripts/cask.py     # verified DMG's Homebrew cask
tests/              # basic pure function tests
```

After approval, the executor reads the version pattern from `schemas/version.schema.json` at the exact source SHA. A matching completion receipt ends the run before source checkout. For a new or resumed release, a step without release credentials checks the reviewed SHA-256 and calls that checkout's changelog validator before dependency resolution or Build ID allocation.

The approved source builds/signs/notarizes the arm64/x86_64 DMG, signs ten notes and appcast, verifies public DMG bytes and Gatekeeper facts, updates [the tap](https://github.com/PositionBar/homebrew-tap), and selects latest. The tag `positionbar-V-B` points to the original executor commit. Signed feed/notes are committed atomically to this repository's **updates data branch**; notes and receipts are immutable, the feed cannot roll back, and branch updates never force. After public website readback verifies feed and notes bytes, `releases/V.json` is written to that branch as the final completion marker. These files are not Release attachments.

The Homebrew cask `version` contains only the App version (`V`). Its DMG URL includes the Build ID (`B`). Each version's URL and SHA-256 are immutable. The publisher updates the cask through GitHub's Contents API using its current blob SHA, so unrelated tap edits can proceed. The receipt records the last commit that changed `Casks/positionbar.rb`. A retry after a lost response verifies the cask at that commit before reusing it.

Canonical feed: `https://positionbar.com/updates/macos/appcast.xml`. The website backend proxies only the feed and supported notes filenames from the public updates branch. No website build or additional Cloudflare credential is needed on App publication. The website changelog reads only private-main version files and may appear before the DMG. `/download` resolves the latest stable DMG at request time.

Secrets: `GH_PAT`, `TAP_WRITE_TOKEN`, `DEVELOPER_ID_CERTIFICATE_P12_BASE64`, `DEVELOPER_ID_CERTIFICATE_PASSWORD`, `DEVELOPER_ID_PROVISIONING_PROFILE_BASE64`, `APP_STORE_API_PRIVATE_KEY`, `SPARKLE_PRIVATE_KEY`, and optional `SENTRY_AUTH_TOKEN` (for uploading macOS `.dSYM` bundles and publishing Sentry releases/deploys). Variables: `APP_STORE_API_KEY_ID`, `APP_STORE_API_ISSUER_ID`. The Apple API key is for notarization. There is no `WEBSITE_DEPLOY_HOOK` or TestFlight upload.

Start [release.yml](https://github.com/PositionBar/releases/actions/workflows/release.yml) on main, providing `version` and `source_sha`, or use an authorized GitHub CLI session:

```sh
gh workflow run release.yml --repo PositionBar/releases --ref main \
  -f version='<version>' -f source_sha='<version-pr-merge-sha>'
```

Use the exact version PR merge SHA rather than a later private main HEAD. `resume_run_id` identifies a completed run's `release-checkpoint` and retains original identity, DMG and signed bytes. Without a verified DMG use a fresh attempt and Build ID. Never rerun an allocation or clobber public files. A matching completion receipt skips packaging/publication. A partial release needs its exact checkpoint.

Activate the website endpoints first, initialize updates with the exact published 0.1.2 feed/notes/receipt, then enable DMG-only publication and remove the old Deploy Hook/secret. Existing 0.1.2 embeds the GitHub feed URL: users accept one manual installation of the migration release, then continue in-app updates through the new feed. Keep old 0.1.2 attachments during migration; do not repackage that release. Main code retains owner review and no-force-push protection; updates is public release data, not executable CI code.

For changes to approval, public asset verification or cask handling, run the affected module (`test_ci`, `test_release_asset` or `test_cask`) with `PYTHONPATH=tests python3 -B -m unittest <module> -q` within the shared 60-second budget. Formal signing, notarization and publication integrity checks remain mandatory.
