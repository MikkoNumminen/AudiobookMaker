---
name: release-cut
description: Cut a new AudiobookMaker release (bump APP_VERSION, tag vX.Y.Z, push, verify CI lands SHA-256 in release notes AND sidecar). Use this skill whenever the user says "cut a release", "bump the version", "ship X.Y.Z", "make a release", "release X.Y.Z", or asks to tag a new version. Auto-update is existential for this project — a release that ships without a valid SHA-256 locks every existing user out of one-click updates. This skill encodes every moving part so that can never happen again.
---

# Release-cut

Cut a new AudiobookMaker release end to end.

## Why this skill exists

Auto-update is the lifeline to existing users. `src/auto_updater.py` pulls
the latest release from GitHub and verifies the installer against a SHA-256
hash read from **either** the release-notes body **or** a sidecar
`.exe.sha256` asset. If neither is present, auto-update is silently broken
for every existing user.

v3.7.0 shipped without the hash. Every user had to go to GitHub, dismiss
SmartScreen, and manually install. That incident is why the CI pipeline has
three independent guards. Your job during a release cut is not to
re-implement those guards — CI does that. Your job is to:

1. Keep two files in sync so CI doesn't refuse to build.
2. Push the tag in a form CI recognises.
3. After CI is green, verify the guarantee held end-to-end on the live
   release.

## What CI does so you don't have to

`.github/workflows/build-release.yml` fires on any `v*` tag. It:

- Runs the full pytest suite.
- Asserts `src/auto_updater.py::APP_VERSION` matches
  `installer/setup.iss::#define MyAppVersion`. Drift → build fails.
- Rewrites both values to match the tag name (strips the leading `v`).
- Builds the exe and installer, computes the SHA-256, writes it into
  `release_notes.md`, uploads a `AudiobookMaker-Setup-<v>.exe.sha256`
  sidecar asset, and refuses to publish if the notes are missing the
  SHA-256 line.
- After publish, re-fetches the live release body and asserts the hash
  survived.

You never need to hand-edit release notes, compute hashes, or upload assets.
Trust the pipeline; verify its output.

## The cut

### Step 1 — decide the version

Semver-ish. Ask the user if unclear:

- Patch (`3.9.1` → `3.9.2`): bug fixes, no user-visible behavior changes.
- Minor (`3.9.x` → `3.10.0`): new features, new engines, new UI.
- Major: only if upgrade migration is required.

### Step 2 — pull and read

```bash
git checkout master
git pull --ff-only origin master
```

Re-read [TODO.md](../../../TODO.md) before committing anything, per the
project's shared-board protocol.

### Step 3 — bump both version strings together

They MUST match before the tag is pushed, otherwise the CI version-drift
guard fails the build.

- `src/auto_updater.py` — line near top: `APP_VERSION = "X.Y.Z"`
- `installer/setup.iss` — line near top: `#define MyAppVersion "X.Y.Z"`

Update README download links at the same time. They point at
`https://github.com/MikkoNumminen/AudiobookMaker/releases/download/vX.Y.Z/AudiobookMaker-Setup-X.Y.Z.exe`
— grep for the old version string and replace all occurrences in
`README.md`.

### Step 4 — commit and push

One commit, conventional:

```
release: bump APP_VERSION and installer to X.Y.Z
```

Body is optional; use it only if the release has a headline change worth
recording there. The detailed changelog will be written by CI from the
template in `build-release.yml` — you don't hand-author it.

```bash
git add src/auto_updater.py installer/setup.iss README.md
git commit -m "release: bump APP_VERSION and installer to X.Y.Z"
git push origin master
```

### Step 5 — tag and push the tag

```bash
git tag vX.Y.Z
git push origin vX.Y.Z
```

The `v` prefix is required — `build-release.yml` triggers on `v*` only.

### Step 6 — watch CI

```bash
gh run list --limit 5
gh run watch <run-id>
```

Or: `gh run view <run-id> --log-failed` if it fails. Three likely failure
modes:

1. **Version drift** — the two version strings don't match. Fix whichever
   is wrong, commit, re-tag (delete + recreate + force push tag).
2. **Tests failed** — CI runs the full suite before building. Fix on
   master, delete tag, re-tag.
3. **SHA guard failed** — should not happen unless the release-notes
   template in the workflow was broken in a prior commit. Investigate.

### Step 7 — verify the live release honours the contract

This is the step most likely to be skipped. Don't skip it.

```bash
gh release view vX.Y.Z -R MikkoNumminen/AudiobookMaker --json body,assets
```

Check both:

- The body contains `SHA-256: <64 hex chars>`.
- The assets list includes `AudiobookMaker-Setup-X.Y.Z.exe.sha256` as a
  separate sidecar file.

If either is missing, auto-update is broken. Treat as P0 — either re-cut
the release or push a follow-up that writes the sidecar asset manually.

### Step 8 — smoke test auto-update from the previous version

Optional but cheap: if the user has a previous version installed, ask them
to open the app and confirm the update banner appears and the "Update
now" button downloads + verifies + installs without errors. This is the
only real end-to-end check on the `_fetch_sidecar_sha256` fallback.

### Step 9 — clean up TODO.md

If there was a release-related claim in `TODO.md`, remove it per the
project's single-source-of-truth protocol. Push.

## Tag re-do

If you need to re-cut a tag (wrong version, CI failure after commit
already pushed to master):

```bash
git tag -d vX.Y.Z
git push origin :refs/tags/vX.Y.Z
# make the fix
git tag vX.Y.Z
git push origin vX.Y.Z
```

Do not delete a tag that already has a published release attached — those
releases are visible on GitHub and deleting the tag orphans them. Instead
publish a patch release with a higher version.

## Things NOT to do

- **Do not hand-edit release notes after publish.** The auto-updater parses
  the body. Stripping the SHA line would recreate the v3.7.0 incident.
- **Do not bypass the CI guards** (`--force-tag`, manual release via web
  UI, etc.). They exist because a prior release broke without them.
- **Do not batch a release with unrelated work.** The release commit is
  one logical change — the version bump. Other work goes in a separate
  commit beforehand or afterwards.
- **Do not commit with an AI co-author line** or any mention of Claude /
  AI / assistant in the commit message or release body. The repo
  contributor list stays clean.
