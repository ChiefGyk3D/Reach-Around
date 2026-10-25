# Changing the page

You do not need to run anything on your computer. Edit `site.yaml` on GitHub,
open a pull request, read the result, merge. Your change goes live after a
reviewer approves the deploy.

## Edit from your phone or the GitHub web editor

Everything below works in a phone browser, in the GitHub mobile app, and in
the web editor on a computer. You never run anything. A change goes live only
after a pull request is merged and a reviewer approves the deploy, so you
cannot break the live page by trying.

1. **Open the file.** In your repository, tap `site.yaml`, then the pencil
   icon (on a phone, the three dots, then Edit file).

   > [Screenshot placeholder: the repository file list with `site.yaml`
   > highlighted and the pencil icon circled.]

2. **Make the change.** Change a title, a URL or a date, or copy a whole link
   (see "To add a link" below). Keep the spaces at the start of each line
   exactly as they are.

   > [Screenshot placeholder: the editor with one `title:` line changed.]

3. **Save to a new branch, not to `main`.** Tap Commit changes. Choose
   "Create a new branch for this commit and start a pull request", give the
   branch any short name (for example `new-podcast-link`), and tap Propose
   changes.

   > [Screenshot placeholder: the commit dialog with the new-branch option
   > selected.]

4. **Open the pull request.** Tap Create pull request. Nothing is live yet.

   > [Screenshot placeholder: the Open a pull request page.]

5. **Read the comment.** Within a few minutes a comment appears on the pull
   request, headed with one of these:

   - **The preview is ready.** Your change is valid and the site builds. The
     comment lists the blocks you changed ("Tab "Links": block "newsletter"
     changed.") and links to the run. On the run page, download the `preview`
     artifact, unzip it and open `index.html` to look at the pages.
   - **This change has a problem.** Each problem is one sentence that names the
     tab, the block and the field, then says how to fix it. Nothing was built.
     Go back to step 1 on the same branch and fix it; the comment updates
     after every save.
   - **The settings are valid, but the site did not build.** Usually a picture
     file named in `site.yaml` is not in the `media` folder. Open the run
     details link.
   - **The check could not run.** A problem with the check itself, not with
     your change. Open the run details link, or ask whoever maintains the
     repository.

   > [Screenshot placeholder: a pull request comment headed "The preview is
   > ready" with the list of changed blocks.]
   >
   > [Screenshot placeholder: a pull request comment headed "This change has a
   > problem" with one numbered sentence.]

6. **Merge to deploy.** When the checks are green (the one named `CI green`),
   tap Merge pull request, then Confirm. The `Deploy` workflow starts and
   waits for a reviewer to approve the `production` environment (Actions,
   the `Deploy` run, Review deployments). After approval the page is live.

   > [Screenshot placeholder: the green merge button, and the Review
   > deployments dialog.]

A mistake never reaches the site: a config the check rejects cannot be built,
and nothing deploys without the approval click.

**What the comment prints, exactly.** It never prints a value from your file:
no URL, address, token or text you typed. It prints only:

- the tab's **title** and the block's **id**, reduced to plain letters, digits
  and a few punctuation marks and cut to 40 characters. These are names that
  appear on your published site, not secrets;
- a **setting name** (such as `url` or `theme.mode`) only if it is one the
  schema defines. A setting the schema does not know is shown as
  `(unknown setting #4)`: its position among its neighbors, never its name;
- fixed sentences, and numbers the engine itself defines (such as a length
  limit).

So pasting a private value into the wrong place does not publish it in the
comment. It is still in the file's history in your repository, so remove it
and rotate it if it was real.

### Reading the problems

The numbers in a message count from 1 (`links[2]` is the second link). "Tab
"Links"" is the tab's title, and "block "newsletter"" is the block's `id`.

| The comment says | What it means | What to do |
|---|---|---|
| `field "url": the web address must start with https://` | The link starts with `http://`, or has no scheme. | Write the full address, such as `https://example.com/page`. |
| `field "url": the web address must not contain a user name or password` | The link has `name:password@` in it. | Remove everything before the `@`. Never put a password in a link. |
| `field "title": is missing` (or `field "id"`, `field "url"`) | A required line is not there. | Add the line, indented like the lines around it. |
| `field "(unknown setting #3)": is not a setting Reach Around knows` | A misspelled or unknown setting name. The comment gives its position, not its name. | Fix the spelling, or delete the line. |
| `(unknown setting #4)` | The fourth setting under that tab or block is not one Reach Around has. Its name is not repeated, because it may have been pasted by mistake. | Count down to the fourth line there and fix or delete it. If it was a real token or password, revoke it and make a new one. |
| `field "id": must be lowercase letters, digits and hyphens only` | The `id` has capitals, spaces or other characters. | Use something like `my-link`. |
| `The id "home" is used more than once` | Two tabs, blocks or links share an `id`. | Give one of them a different `id`. |
| `links to a tab that does not exist` | A `tab:` link names a tab that is not in the file. | Use `tab:` and the `id` of one of your tabs. |
| `Setting "theme.mode": must be one of: auto, light or dark` | A setting that accepts only some words got another one. | Use one of the words listed. |
| `is too long (at most 200 characters)` | Text over the limit. | Shorten it. |
| `the date must be written YYYY-MM-DD` | A date like `March 3`. | Write `2027-03-03`. |
| `must be a path to a picture file in this repository` | A picture path has `..`, a leading `/`, or the wrong ending. | Upload the picture to `media/` and write `media/photo.png`. |
| `Line 14: the file is not valid YAML` | The indentation or a colon is wrong near that line. | Compare the line with the ones around it. Text with a colon in it needs quotes. |
| `Line 9: the file has a setting written twice` | The same key appears twice at one level. | Keep one. |
| `the file uses an alias` (or an anchor) | `&name` or `*name` is not allowed. | Write the value out where it is used. |
| `The check could not run` | The check itself failed. | See "When something goes wrong" below. |

## Editing on a computer

Open the repository, click `site.yaml`, click the pencil, and follow the same
steps. To add a link, find the tab and block list and copy a whole existing
link, which looks like this, then change the text:

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

On a phone, typing indentation is error-prone. Prefer small edits to text you
can already see (a title, a URL, a date) and copy-paste for new links. The
comment will catch a slip before anything goes live.

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

- The comment says there is a problem: fix the line it names and save again.
- No comment appeared: open the pull request's Checks tab and the `Preview`
  run. Pull requests from forks get no comment. If the run says "the pinned
  engine image is too old", `engine.lock` needs the newer engine release.
- You merged and nothing changed: the deploy is waiting for approval. Open
  Actions, then the `Deploy` run, then Review deployments.
- You need to undo: revert the pull request on GitHub (Revert button on the
  merged pull request), merge the revert, approve the deploy.
