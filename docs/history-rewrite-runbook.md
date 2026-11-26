<!-- SPDX-License-Identifier: Apache-2.0 -->
# Runbook: removing a record from the git history of the private instance repository

Issue #621, under epic #364. Follows #586 (the history tombstone).

A tombstone (`reach-around counts history-redact`, see
[social-history.md](social-history.md)) removes a value from the working tree
only. Every earlier commit still holds the old bytes, and so do the other
branches, the tags, the pull request refs, every clone, every fork and
GitHub's cached views. This runbook is the separate, owner-approved operation
that removes the value from git history itself. It rewrites history, force
pushes it, and cannot be fully undone for anyone who already copied the old
data. Read the whole page before the first command.

It uses placeholders only. Never paste a real repository URL, account name,
hostname or the value being removed into an issue, a pull request or a log
excerpt of the public engine repository. The change record lives in the private
tracker (see "What to record").

Separate work, not repeated here: the instance workflow accepting a labeled
tombstone-only rewrite is tracked in the issue titled "History tombstones: let
the instance workflow accept a labeled tombstone-only rewrite" (#619, design in
`docs/superpowers/specs/2026-11-23-labeled-tombstone-rewrite-design.md`). That
covers the tombstone in the current tree. This page covers the old commits.

How to read the marks:

- **VERIFIED (rehearsal)**: run on 2026-11-26 against a throwaway local
  repository (git 2.47.3, a local bare repository standing in for the remote,
  synthetic values only). The output shown is what that run printed, trimmed.
  The rewrite itself was rehearsed with `git filter-branch` (see step 4).
- **VERIFIED (docs)**: read on the vendor page named, 2026-11-26. The page
  shows no date, so treat it as the state on that day.
- **INFERRED**: reasoned from the above and not run. Rehearse it on a scratch
  copy before the real run.

## 1. When to use it

Use it only when the old bytes themselves must go:

- a platform's terms or a deletion request require destruction, not only a
  tombstone, and the deadline would be missed while git still holds the record;
- a record was committed by mistake and must not stay reachable (a lead, a
  private person's data, or a value that should never have been stored);
- a secret was committed. Rotate or revoke it first (step 9). Rotation is
  usually enough; rewrite only if the value is also personal data.

Do not use it for an ordinary correction. A tombstone plus a normal pull request
keeps the audit trail and is enough when the old commits may stay.

Cost: every collaborator must re-clone or reset, open pull requests break,
commit SHAs change, signed commits and tags lose their signatures, and the old
data may survive in places you do not control (step 8). Weigh that first.

## 2. Names used below

| Placeholder | Meaning |
|---|---|
| `REPO_URL` | the clone URL of the private instance repository |
| `BRANCH` | one branch name, for example the default branch |
| `PATTERNS` | a file holding the literal strings to remove, one per line |
| `WORK` | a scratch directory outside the repository, on a disk you control |

Put the strings to remove only in `PATTERNS`, with mode `0600`, and pass them
with `-f`, never as command arguments (arguments land in shell history and the
process table). Delete `PATTERNS` and `WORK` when the change is closed.

## 3. Before you start

1. **Approval.** The owner approves the rewrite for this one record, in writing,
   in the change record.
2. **Freeze writes.** Pause the counts refresh workflow, close or note any open
   `counts/refresh` pull request, and tell every collaborator not to push.
   Anything pushed during the rewrite is lost or rejected by the lease in
   step 6. INFERRED (repository settings, not rehearsed).
3. **Rotate first** anything that was exposed, if it can be rotated (step 9).
4. **Verify the working tree** holds no copy of the value, so the rewrite is not
   undone by the next commit. In a clone of the current default branch:

   ```bash
   git grep -n -F -f "$PATTERNS" -- . ; echo "exit=$?"
   ```

   Expected: no match lines, `exit=1`. A match means the tree still holds the
   value: stop and fix it with a normal commit before rewriting. VERIFIED
   (rehearsal). (`git grep` exits 1 when nothing matches. That is the pass
   result here and in the checks below.)
5. **Take a backup you can restore from.** It holds the old data, so keep it
   encrypted, access limited, and delete it when the retention in the change
   record ends:

   ```bash
   git clone --mirror "$REPO_URL" "$WORK/backup.git"
   ```

   VERIFIED (rehearsal). A mirror clone of GitHub also fetches `refs/pull/*`; a
   backup may keep them, but never push them back (step 10).
6. **Record the old tip of every ref** (used as the lease in step 6):

   ```bash
   git clone --bare "$REPO_URL" "$WORK/rewrite.git"
   cd "$WORK/rewrite.git"
   git for-each-ref --format='%(refname) %(objectname)' refs/heads refs/tags | tee "$WORK/old-refs.txt"
   ```

   Expected (shape): one line per branch and tag, for example
   `refs/heads/main 72fd762de57a4e37c9f83e0281d6df6cbe308345`. VERIFIED
   (rehearsal).

## 4. Make the rewrite (on a fresh clone, never the working clone)

Always work in the fresh clone from step 3. The rewrite touches every commit
that holds the value, on every branch and tag.

Recommended tool: `git filter-repo` at version 2.47 or later (VERIFIED (docs):
the GitHub page "Removing sensitive data from a repository" names it and says
the `--sensitive-data-removal` flag needs at least 2.47). Replace the strings
in every file of every commit, from `PATTERNS` rewritten as `expressions.txt`
lines of the form `literal:VALUE==>REDACTED`:

```bash
git filter-repo --sensitive-data-removal --replace-text "$WORK/expressions.txt"
```

To drop the whole file from history instead (for example every version of
`history/social.jsonl`, then re-add the current tombstoned file in a normal
commit):

```bash
git filter-repo --sensitive-data-removal --invert-paths --path history/social.jsonl
```

INFERRED: not run in the rehearsal (the tool was not available there). The two
commands follow the vendor page; check `git filter-repo --help` for your
version, run on a scratch copy first, and read the "First Changed Commit(s)" it
prints, which GitHub Support asks for (step 8).

Verified fallback, built into git (deprecated by the git project, slower, and
it only replaces text, so use it only if filter-repo is not available):

```bash
export FILTER_BRANCH_SQUELCH_WARNING=1
git clone "$REPO_URL" "$WORK/rw" && cd "$WORK/rw"
# create a local branch for every remote branch you need to keep, then:
git filter-branch --force \
  --tree-filter 'test -f history/social.jsonl && sed -i "s/VALUE/REDACTED/g" history/social.jsonl || true' \
  --tag-name-filter cat -- --branches --tags
```

(`VALUE` stands for the string; the sed form puts it in the process table, so
use it only on an offline scratch machine, or read it from `PATTERNS` in a
small script.) Rehearsal output, trimmed:

```text
Ref 'refs/heads/counts/refresh' was rewritten
Ref 'refs/heads/main' was rewritten
Ref 'refs/tags/v1' was rewritten
```

VERIFIED (rehearsal). Note what the rewrite does to the hash chain: the commits
that held the old line now hold `REDACTED` inside a record, so
`counts history-verify` fails on those OLD commits. It must still pass on the
tip (step 7, check 6). If you used the tombstone from
[social-history.md](social-history.md), the tip already holds the tombstone and
the chain is valid there.

## 5. Remove the data from every ref and the object store of the rewrite clone

The old commits stay reachable through `refs/original/*` (filter-branch),
remote-tracking refs and the reflog until you remove them. Before cleanup, the
rehearsal found the value in 2 commits; after cleanup, 0.

```bash
git for-each-ref --format='delete %(refname)' refs/original refs/remotes | git update-ref --stdin
git reflog expire --expire=now --all
git gc --prune=now --aggressive
```

Each command exited 0. VERIFIED (rehearsal). filter-repo does not leave
`refs/original`; the reflog and gc steps still apply. INFERRED for that tool.

## 6. Push with the safe lease form

Push each rewritten ref with a lease that names the old tip you recorded in
step 3. The push is refused if anyone pushed in between, so you never overwrite
work you did not see. Never use a bare `--force`, and do not use
`--force-with-lease` without the `ref:old-sha` form here: after the cleanup in
step 5 the clone has no remote-tracking refs to lease against.

Temporarily allow force pushes on protected branches (INFERRED: a branch
protection or ruleset change in the repository settings; read the current
vendor page first, and restore the protection in step 7). Then:

```bash
OLD=72fd762de57a4e37c9f83e0281d6df6cbe308345   # from old-refs.txt, one per ref
git push --force-with-lease="refs/heads/BRANCH:$OLD" origin "refs/heads/BRANCH:refs/heads/BRANCH"
```

Repeat for every branch in `old-refs.txt`, and for every tag with
`refs/tags/NAME` (the tag form is the same). Rehearsal, a push after someone
else had pushed:

```text
 ! [rejected]        main -> main (stale info)
error: failed to push some refs to '...'
```

and the real push:

```text
 + 72fd762...1322f32 main -> main (forced update)
 + 2f956a0...154e852 counts/refresh -> counts/refresh (forced update)
 + 3631fc3...e9d9267 v1 -> v1 (forced update)
```

VERIFIED (rehearsal, local bare remote). On GitHub the vendor page uses
`git push --force --mirror origin` and says it fails for `refs/pull/*` because
GitHub marks those read-only (VERIFIED (docs)); the per-ref lease form above
avoids touching them at all. Do not use `--mirror` here: INFERRED, it also
deletes refs that are absent from your clone.

If a ref is refused for protection reasons rather than a stale lease, fix the
protection, do not skip the ref. A ref left behind keeps the old data public.

## 7. Verify (every branch, every tag, the working tree)

Run these in a brand-new clone, not the clone you rewrote, so you test what
the server serves:

```bash
git clone --bare "$REPO_URL" "$WORK/verify.git" && cd "$WORK/verify.git"
```

| # | Command | Expected |
|---|---|---|
| 1 | `git for-each-ref --format='%(refname)' refs/heads refs/tags` | every branch and tag you meant to keep, none you did not |
| 2 | `git rev-list --all \| while read c; do git grep -q -F -f "$PATTERNS" "$c" -- && echo "$c"; done \| wc -l` | `0` |
| 3 | `git log --all --format='%H%n%B' \| grep -c -F -f "$PATTERNS"` | `0` (commit messages; grep exits 1) |
| 4 | `git fsck --unreachable --no-reflogs \| wc -l` | `0` (nothing left unreachable in the verify clone) |
| 5 | `git cat-file --batch-all-objects --batch \| grep -a -c -F -f "$PATTERNS"` | `0` (grep exits 1: no object holds the bytes) |
| 6 | in a normal clone: `git grep -n -F -f "$PATTERNS" -- . ; echo "exit=$?"` | no lines, `exit=1` |
| 7 | in a normal clone: `reach-around counts history-verify --config site.yaml` | exit 0 |

Rehearsal results: check 2 and check 5 printed `0` on the rewritten
repository and `2` on the backup (the control that proves the check can see
the value); check 4 printed `0` (run in the rewritten clone after cleanup); check 3 printed `0`; check 6 printed nothing
with `exit=1`. VERIFIED (rehearsal). Always run the same check against the
backup once, so a typo in `PATTERNS` cannot give a false pass.

Checks 2 to 5 only prove the clone you made. They do not prove the server has
forgotten the objects, or that pull request refs, forks and cached views are
clean (step 8). The rehearsal showed this: after the push, the bare "remote"
still held 2 unreachable objects with the value until it was pruned by hand
(`git gc --prune=now` on it). A host you do not run, such as GitHub, prunes on
its own schedule; only Support can force it. VERIFIED (rehearsal) for the
local remote; INFERRED for GitHub.

After the checks, restore the branch protection you relaxed in step 6.

## 8. Consequences and the places the old data can remain

| Place | What happens | Action |
|---|---|---|
| Collaborator clones | Their branches diverge from yours; a pull merges old and new history and brings the value back. | Tell everyone before the push (below). |
| Open pull requests | They are built on old commits and break; their refs (`refs/pull/*/head`) still point to the old commits and users cannot edit them. | Close them, re-open from the new history. Needs Support to remove the refs. |
| Forks | A fork that holds the old commits keeps serving them. | Ask each fork owner to delete the fork or rewrite it. GitHub does not give out their contact details. |
| Mirrors and CI caches | Any mirror, backup, build cache or artifact made from a checkout still holds the old bytes. | List them in the change record, then purge or expire each one. |
| Cached views on GitHub | A commit can stay reachable by its SHA after the force push. | Ask Support (below). |
| Releases, packages, artifacts | A release asset or workflow artifact built from the old tree still holds the bytes. | Delete them. |
| Your own machines | Old clones, editor local history, the reflog and any local backup. | Re-clone or run the collaborator steps. |

Vendor statements (VERIFIED (docs), the page named in step 4): commits remain
reachable through cached views and pull request refs after a force push;
Support can remove cached views and sensitive data in pull requests; Support
may delete or "dereference" pull requests, and dereferencing keeps comments, so
data quoted in a retained comment or body is not removed; Support "will only
assist in the removal of sensitive data" where rotating credentials does not
mitigate the risk; forks keep the commits until their owners act.

**Ask GitHub Support to purge** when the value is personal data or cannot be
rotated. Open a private request through GitHub Support (not a public issue) and
give: the repository name, the count of affected pull requests (the vendor page
gives `grep -c '^refs/pull/.*/head$' .git/filter-repo/changed-refs` after a
filter-repo run), and the "First Changed Commit(s)" filter-repo printed. State
that the data is personal data and that a force push has been done. Do not put
the value itself in the request. INFERRED beyond the vendor page: the exact
form and the response time.

**Tell collaborators before the push**, with this exact guidance, to run in
their clone (it deletes only the reflog and unreachable objects of that clone;
anything local and unpushed on a branch is lost, so copy it elsewhere first):

```bash
git fetch --prune --force --tags origin
git checkout BRANCH
git reset --hard origin/BRANCH
git reflog expire --expire=now --all
git gc --prune=now
```

or simply delete the clone and clone again. Rehearsal: a stale clone showed the
value in 2 commits and a branch `[ahead 3, behind 3]`; after these commands it
showed 0 and sat on `1322f32`. VERIFIED (rehearsal). A plain `git pull` on the
stale clone stopped with "Need to specify how to reconcile divergent branches",
which is the safe outcome; anyone who merges instead of resetting brings the old
commits back and the push must be repeated.

## 9. Rotate what was exposed

Rewriting does not un-leak anything that was fetched. If the commits held a
token, a key, a password or a webhook, revoke or rotate it first and record the
time; a rotated secret makes the old copy useless, and GitHub's own guidance
says so too (VERIFIED (docs)). Put the new secret in the secret manager, never in
the repository. If the commits held personal data, list who it concerned in the
change record (counts only in public text) and follow the privacy duties in
[privacy.md](privacy.md).

## 10. Rollback, and what cannot be rolled back

Before anyone else pushes, you can restore the old history from the backup
taken in step 3, with the lease on the NEW tips:

```bash
cd "$WORK/backup.git"
git push --force-with-lease="refs/heads/BRANCH:NEWSHA" "$REPO_URL" "refs/heads/BRANCH:refs/heads/BRANCH"
```

Repeat per branch and tag; never push `refs/pull/*`. Rehearsal: the three refs
went back to `72fd762`, `2f956a0` and `3631fc3`, and check 2 printed `2`
again. VERIFIED (rehearsal, local remote).

What cannot be rolled back, whatever you do:

- the rewrite is not reversible for anyone who already fetched the old commits
  or the new ones: some clones now hold one history and some the other;
- the old data is gone from GitHub only after Support removes cached views and
  pull request refs, and never from forks, mirrors and copies you do not hold;
- a restored backup brings the value back: it is a rollback of the rewrite, not
  of the leak, so use it only if the rewrite itself was wrong (for example the
  wrong record was removed), and then redo the whole runbook;
- SHAs, signatures and tag signatures of the rewritten commits are gone.

## 11. What to record in the change record (private tracker, not this repository)

- who approved it and when, and the reason class (deletion request, mistake,
  secret), with no value and no person's name in public text;
- the count of records and commits changed, and the date and time (UTC) of the
  freeze, the push and the unfreeze;
- the tool and its exact version, and the exact command with the pattern file
  named by its path, not its contents;
- the old and new tip of every ref (hashes are not sensitive);
- the verification table from step 7 with each result, the control run on the
  backup, and the date the verification was repeated against a fresh clone;
- the list of places in step 8 with an owner, an action and a done date each:
  Support request id, forks contacted, mirrors and caches purged, releases and
  artifacts deleted;
- rotated credentials by name and rotation time;
- the backup location, who can read it, and the date it is destroyed;
- collaborators told, and the time each confirmed a re-clone or reset;
- the date the freeze was lifted and the branch protection restored.
