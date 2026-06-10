# Scrubbing a file from origin with `gh api` (no local checkout needed)

When a file that must not be public lands on origin (copyright leak,
secret, anything CLAUDE.md marks P0), the fastest safe fix edits or
deletes it **directly on GitHub via the Contents API**. This works even
when another session owns the main worktree, when the local tree has
unrelated WIP you must not disturb, or when you are not on the machine
that pushed it.

This removes the file from the **tip** of the branch. The content still
exists in history — a history rewrite (`git filter-repo` + force-push)
is destructive and **always needs the user's explicit go-ahead first**
(CLAUDE.md). Tip-scrub now, ask about history second.

## Delete a file from the branch tip

1. Get the file's current blob SHA:

   ```bash
   gh api repos/{owner}/{repo}/contents/<path>?ref=master --jq .sha
   ```

2. Delete it (one commit on master, no checkout involved):

   ```bash
   gh api -X DELETE repos/{owner}/{repo}/contents/<path> \
     -f message="chore: remove <generic description>" \
     -f sha="<blob-sha>" \
     -f branch=master
   ```

## Replace a file's contents on the branch tip

1. Get the blob SHA (same as above).
2. PUT the scrubbed content (base64-encoded):

   ```bash
   base64 -i scrubbed_local_copy.md | tr -d '\n' > /tmp/b64
   gh api -X PUT repos/{owner}/{repo}/contents/<path> \
     -f message="docs: genericize <file>" \
     -f sha="<blob-sha>" \
     -f branch=master \
     -f content="$(cat /tmp/b64)"
   ```

## Rules that ride along

- The commit `message` is public: keep it generic — never name the
  leaked work/author in the very message that removes it, and no vendor
  branding (CLAUDE.md).
- After the tip-scrub, `git fetch` in local checkouts before their next
  push, or they will re-push the leaked blob.
- Then surface to the user: what leaked, when, the scrub commit, and
  the history-rewrite question. Do not rewrite history on your own.
