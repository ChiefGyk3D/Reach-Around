# Changing the page

You do not need to run anything on your computer. Edit `site.yaml` on GitHub,
open a pull request, read the result, merge. Your change goes live after a
reviewer approves the deploy.

## The safe loop

1. **Edit** `site.yaml` (steps below).
2. **Propose**: GitHub offers "Create a new branch and start a pull request".
   Choose that, not "commit directly to main".
3. **Read the result.** In a minute the `Preview` check finishes. Open it,
   then **Summary**:
   - "The site builds": the change is valid. Download the `site-preview`
     artifact to look at the pages.
   - "The site did not build": the box says which setting is wrong, for
     example `theme.mode: Input should be 'auto', 'light' or 'dark'`. Edit
     the file again on the same branch; the check re-runs.
4. **Merge** the pull request. The `Deploy` workflow starts and waits for the
   reviewer to approve the `production` environment. After approval the page
   is live.

A mistake never reaches the site: a broken config cannot build, and nothing
deploys without the approval click.

## Editing on GitHub (computer)

Open the repository, click `site.yaml`, click the pencil. Keep the spaces at
the start of each line exactly as they are: YAML uses indentation to show what
belongs to what.

To add a link, find the tab and block list and copy a whole existing link,
which looks like this, then change the text:

```yaml
      - id: newsletter
        title: Newsletter
        subtitle: One email a month
        url: https://news.example.com/
```

- `id` is a short name with no spaces (letters, digits, dashes). It must be
  different from every other `id` in the file.
- `title` is what visitors read. `url` is where it goes.
- To remove a link, delete all its lines. To reorder, move the block.

Text with a colon or a leading symbol needs quotes: `title: "Gear: 2027"`.

## Editing on a phone

The GitHub mobile app and the mobile site both edit files: open `site.yaml`,
tap the pencil (or the three dots, then Edit file), change a line, then
"Commit changes" and pick the new-branch option. Typing indentation on a phone
is error-prone, so on a phone prefer small edits to text you can already see
(a title, a URL, a date) and copy-paste for new links. The preview check will
catch a slip before anything goes live.

## Help from your editor

The first line of `site.yaml` names a JSON Schema:

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/ChiefGyk3D/Reach-Around/main/docs/site.schema.json
```

In VS Code with the YAML extension, a schema gives autocomplete and
underlines mistakes as you type. (The browser editor opened by pressing `.` on
the repository page may do the same if the YAML extension is available there;
that has not been checked.) The schema file ships with
the engine's `reach-around schema` command, which is **not on `main` yet**:
it arrives with engine pull request #109 (issue #103, phase 1). Until that
merges the line is just a comment and the plain github.com editor never reads
it either way. The pull-request check is the safety net, not the schema.

## Fast commands (after engine PR #109 lands)

Engine PR #109 adds commands that edit `site.yaml` safely, keep your comments
and formatting, validate the result and write a `.bak` first. **They do not
exist in the pinned engine yet**: `engine.lock` pins v0.1.0, and these commands
arrive in a later release. When you have updated `engine.lock` to a release
that includes them, run them through the container the same way
`scripts/validate.sh` does, or install the package locally. The shape, from
the pull request description (check `reach-around --help` for the real names
before relying on them):

```
reach-around add-link    ...   # add a link to a tab
reach-around remove-link ...
reach-around move-link   ...
reach-around set theme.accent ...
```

Each takes `--dry-run` to show the change without writing it.

## Pictures

Put images in `media/` (use "Add file" then "Upload files") and refer to them
from `site.yaml` as `media/name.png`. Remove location and camera data from
photos first. See `media/README.md`.

## When something goes wrong

- The check says the site did not build: read the box, fix that line.
- You merged and nothing changed: the deploy is waiting for approval. Open
  Actions, then the `Deploy` run, then Review deployments.
- You need to undo: revert the pull request on GitHub (Revert button on the
  merged pull request), merge the revert, approve the deploy.
