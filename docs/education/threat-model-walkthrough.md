# Threat model walkthrough

This page explains how the Reach Around threat model works and why it is written the way it is. The full, ranked version is in [`docs/security/threat-model.md`](../security/threat-model.md). Read this one first if you are learning.

All names, addresses and numbers here are made up.

## What a threat model is

A threat model answers four plain questions:

1. What are we protecting?
2. Who might try to hurt it, and how?
3. What stops them?
4. How do we know the thing that stops them actually works?

Question 4 is the one people skip. A control with no test is a hope. In this project, every risk has a named test that fails when the control is missing.

Three marks run through the full model, and you should learn to read them first. **Shipped** means the code is on the main branch and a test exists. **Planned** means it is not built yet and names where it will be. **Open** means the maintainer has to make a choice before anyone can plan it. A control with none of the three marks is a mistake in the document. The model was revised once after an independent review; this page was updated with it.

A threat model is not an audit. It does not say "this is secure." It says "here is what we thought of, here is what we checked, and here is what we did not look at."

## The pieces

**Assets** are things worth taking or breaking. Here that is a list of partner emails, a media kit with private rates, a secret that signs unlock tokens, an API token that can change the website, and the reputation of the domain that sends email.

**Actors** are the people and programs that can touch the system. A random visitor. A bot. A partner who forwards a link. A person who sends a pull request. A software package that turned out to be malicious.

**Trust boundaries** are the lines where data goes from "we do not trust this" to "we act on this." A web request arriving at the Worker is one. A configuration file read by the site builder is another. A reply from a platform API arriving at a collector is a third. Most security bugs live at these lines, so the model walks them one at a time.

**Abuse cases** are one-sentence stories: "an attacker does X to get Y." We rank them by how bad Y is and how hard it is to start X. A terrible outcome that needs a stolen password ranks below a moderate outcome that anyone with a web browser can cause.

## How the ranking works

Two questions per case:

- **Impact.** If it works, how bad is it? A leaked stream key is worse than a few fake clicks.
- **Prerequisites.** What does the attacker need first? An HTTP client is nothing. A merged pull request is a lot.

That is why the email form is ranked first, even though a leaked deploy token would be worse. The form is public from day one and needs nothing to attack. The token needs someone to get inside.

## Worked example: the email-gate spam relay

### The feature

A partner visits the media kit page. They type their email into a box. The kit opens right away. If they want a copy, they press "email me a link," and the site sends them a message.

### The story an attacker tells

"The site sends an email to any address I type. I do not need to own that address. I can type a thousand addresses and make the site send a thousand emails, and every one of them will look like it came from the creator."

Why that matters:

- The people who get the mail did not ask for it. That is spam with the creator's name on it.
- The email provider notices lots of complaints and may suspend the account. Then real partners get nothing.
- The provider has a daily limit. If the attacker uses it all up, real partners are locked out. They did not need to break in; they only needed to be loud.

This is called a **relay**: the attacker is using our server to deliver their messages.

### The controls, one at a time

Each control closes one part of the story. Here is what each does, in plain words.

1. **A challenge on the form (Turnstile).** A bot has to pass a check that is easy for a person. The check is verified on our server, not in the browser, because the browser can be lied to. A check passes once; replaying the same pass fails.
2. **One address, checked strictly.** The form accepts exactly one email address. A newline, a comma-separated list, or odd characters are refused. This stops a trick called header injection, where hidden characters in a field add extra recipients.
3. **A fixed message.** The email text is written by the creator, ahead of time. Nothing a visitor types goes into it. An attacker cannot use the site to deliver their own message.
4. **One message per address per day.** Even if someone types the same victim's address a hundred times, the victim gets one.
5. **A daily budget.** The whole site can send only so many emails per day. The counter is updated in one step so two requests at the same moment cannot both slip past the limit.
6. **Fail gracefully.** When the budget is spent, the kit still opens. The partner just does not get the email. The site also never says whether an email was sent to an address, so an attacker cannot use it to check who exists.

### The tests that prove it

A control without a test is a hope. These are the tests the plan must write, in the order of the controls above:

- `missing_or_failed_turnstile_sends_nothing`: post the form with no challenge result; expect no outgoing email.
- `reused_turnstile_token_sends_nothing`: reuse a good pass; expect it to fail the second time.
- `newline_in_address_is_rejected` and `address_list_is_rejected`: hostile addresses send nothing.
- `message_body_has_no_visitor_text`: send a form with a rude name in it and check the outgoing message is byte for byte the template.
- `one_message_per_address_per_day`: post twice; exactly one outgoing email.
- `concurrent_requests_cannot_exceed_daily_budget`: fire fifty requests at once against a budget of five; exactly five emails.
- `budget_spent_still_opens_kit_and_same_response_shape`: after the budget is gone, the kit still opens and the reply does not give anything away.

Then comes the step that makes the tests honest: remove one control, run its test, watch it go red, put the control back. If the test stays green with the control gone, the test is decoration.

### What is left over

Real attackers use services where people solve the challenge for money. So a determined person can still get through some of the time. The daily budget turns that from "unlimited abuse" into "a few messages a day." The creator accepts that leftover risk on purpose, and the model says what would reopen the question: a warning from the email provider, a rise in complaints, or the sending domain landing on a blocklist.

That last part matters. **Residual risk** is the honest list of what is still possible after the controls, who accepted it, and what signal means "look again."

## Other risks in the model, in one paragraph each

- **Secrets.** The most valuable secrets (the token that deploys the site, the key that signs unlock tokens) never appear in the code repository, never appear in logs, and are only available to the job that needs them. A build step that installs lots of third-party code does not get them.
- **Gate bypass.** The private part of the media kit is never put in the public website files. It is stored where only the Worker can read it, and the Worker checks the visitor's pass first. A test builds the site with a marker string in the private content and searches every public file for it.
- **Tokens and forwarded links.** The unlock pass is a signed cookie, not a link, so copying the address of the kit page gives the recipient nothing. The link in the email works once, and a test uses it twice and expects the second use to land on the gate. Some company mail systems open links automatically to scan them, so the link opens a page with a button, and the scanner does not use up the one-time link. What this does not do is stop a forwarded email: whoever opens it first gets in. The next section is about that gap.
- **Fake data.** Bots can add junk. The model does not pretend this can be stopped completely; it limits it and labels counts as approximate.
- **The Kick stream key.** One platform's API response includes a secret that lets anyone broadcast on the channel. The collector keeps a list of fields it wants and drops everything else, so even a new field cannot sneak through. The test uses a made-up key and checks it is nowhere afterward: not in logs, errors, the database, or exports.
- **Hostile configuration.** A configuration file can be crafted to eat memory, hide duplicate settings, or point at files outside the project. The loader refuses these. Tests feed it bad files.
- **Supply chain.** Every package and every automation step is pinned to an exact version or hash, so an update cannot silently change what runs.
- **Platform terms.** Some platforms forbid showing certain numbers in public. The site builder has a table of those rules, and it refuses to build a page that breaks them.
- **Personal data.** Collect the least, keep it the shortest time, and be able to delete it. Deleting has to reach every copy we control, and the privacy page says honestly about the copies we do not.

## What the review found, and what changed

An independent reviewer read the first version and found six gaps. Each is a lesson in a different way a model can be too optimistic.

### 1. A promise bigger than the mechanism

The design said an unlocked kit is "not forwardable." Look at what actually stops forwarding. A one-time link stops a second use. It does not stop the first use by the wrong person. If a partner forwards the email to a colleague and the colleague clicks first, the colleague gets in and the partner gets the gate. A cookie tied to the partner's record does not help either, because it protects against forging a pass, not against someone who simply holds the browser that has one.

When a promise is bigger than the control behind it, shrink the promise or strengthen the control. The model does both as a choice for the maintainer. It rewrites the claim to what is enforced ("the link works once, and no pass is ever in a web address"), and it lists stronger options with their costs: ask the visitor to retype the address, tie the link to the browser that asked for it, make the visitor type a code from the email, or limit how many devices one lead can unlock. Each option stops something and costs something, such as friction for a partner on a phone, or more email, which makes the spam-relay problem bigger. It does not pick one, because the trade is the owner's to make. A test named `forwarded_copy_redeemed_first_wins` exists to state the limit out loud, so nobody forgets it.

### 2. The attacker who is just loud

The first version ranked abuse of the click counter low, because the worst result was bad numbers. That missed the real cost. The click counter, the gate and the media kit all run on one small account with one daily allowance of requests and one small database. Someone who sends a flood of cheap, fake clicks can use up the allowance and take the gate down for real partners. They need no password and no skill.

A limit per visitor does not help, because a flood does not come from one visitor. So the model adds limits that do not care who is asking:

- Refuse oversized requests before reading them. The click route never reads a request body, and a test is proposed to pin that so nobody adds it by accident.
- A total budget for each route, spent by anyone, that returns the same blank answer once it is gone.
- A cap on how much the database can grow, either by refusing new rows or by storing counts per hour instead of one row per click.
- Logs that record a fixed short code, not the text the visitor sent, so an attacker cannot fill the logs or plant text in them.
- A kill switch: one setting the owner can flip, with no new release, that makes the route do nothing. The link page keeps working with the Worker switched off, because the click report is a bonus that rides along with the link.

The model also lists what already exists (the per-visitor limit, the blank answer, the daily cleanup) and what does not (everything else on that list). It is honest that most of it is only a plan.

### 3. The private PDF is a pipeline, not a file

The owner's full PDF is made on the owner's own computer from the same data as the website. That sounds safe because it never goes online. Follow the data, though: from the store, to a program that draws pages, to files on disk, to a person who receives the PDF. Each step has its own risks:

- The page-drawing program may be asked to fetch a picture from the internet. That tells a stranger when you made the PDF, and a hostile address can aim the request at your own network. The fix is to draw with no network at all, and to treat any attempt to reach out as an error.
- The program may leave scratch files behind in a place others can read. The fix is a private folder that is deleted afterward, even after a crash.
- The PDF itself carries hidden fields: author, tool, sometimes folder names, and photos with their location tags. The fix is a fixed, boring set of fields and stripping photo tags.
- The partner list or lead emails could be pulled in by accident. The fix is that the drawing program is only handed a list of named fields, with no lead field at all, and a test hides a made-up email in the store and checks it never appears in the file.
- If the drawing program itself is tricked by hostile text, it should run in a box with no secrets in its environment.

### 4. "We keep leads 12 months" is not a control

A promise to delete is only as good as the list of places the data lives. Lead emails can sit in the database, in a local copy, in an export someone saved to a folder, in a backup, in the email provider's logs, and in an alert message. The model makes that list (the "data-copy inventory") and gives every copy a ceiling and a way to be deleted. It adds a short life for emails nobody has confirmed, since those might be a stranger's address typed by someone else. It sets a hard maximum that the configuration refuses to exceed. It makes the delete command read the list of saved exports and remove the address from each. And it handles an awkward case: if you restore a backup, the deleted people come back, so the delete command keeps a list of what it removed (as one-way hashes, not the addresses) and applies it again after any restore. Whether that list is acceptable is itself one of the open questions. Copies held by other companies cannot be deleted by us. They are listed, with how long they last, and shown honestly on the privacy page.

### 5. A warning is not a lock

The site can refuse to show certain figures because a platform's rules forbid it. The first design let the owner override a rule and said the build would "log a warning." A warning stops nothing, and a configuration file copied from someone else could carry the override without the new owner noticing. The model replaces it with an approval: a named person, a reason, an end date, and a token that goes stale if the rule changes. Some things have no override at all, such as lead emails and secret keys.

The deeper point is where the rule is applied. If a template is handed every figure and told not to print some of them, one bad template prints them all. So the rule must remove forbidden data before any template, export, PDF or asset sees it. A template cannot leak what it was never given. Hiding something with a style sheet does not count, since the data is still in the page.

The same check also has to cover every place figures can go: the site, the members-only part, copied images, exports and PDFs. The model asks for a list of those places and a test that fails when a new one is added without the check. It also notices a scheduling problem: the roadmap builds the members-only part and the PDFs before it builds the rule enforcement, so the order may need to change.

### 6. When a key leaks, the work is in the recovery

The first version said how to store keys and how to rotate one safely. It did not say what to do on the bad day. A good recovery plan needs, written down ahead of time:

- A list of every key: what it opens, who owns it, how often it is changed, and exactly how to cancel it.
- The smallest permissions each key can have, with a test that fails if a program asks for more.
- A runbook in order: stop the damage, cancel the key at the provider, make a new one, prove the old one is dead, read the provider's log for what happened in between, and only then clean up.
- A special rule for the "refresh tokens" that platforms hand out. Some providers cancel a whole family of tokens if an old one is used twice. So if the collector suddenly cannot refresh, that might be a thief, not an expiry, and it gets a different alert.
- Outside contributors' code never sees secrets. Anything that holds a key only runs from the main branch in a protected environment.

## What has already shipped, and what the reviews found

Some of the code now exists, so the model could check it instead of guessing. Two parts matter here: the way the builder copies the owner's pictures and fonts into the site, and the way an owner can replace page templates or add a style sheet.

For pictures and fonts, the config names the only files that are published. Each one passes the same checks. The file extension must be on an allowed list. The path must stay inside the instance folder, and the builder walks it one step at a time and refuses any shortcut (symlink) on the way. Odd file types, like a named pipe that would hang the build, are refused. The size is counted as bytes are read, not as claimed. The start and end of the file must match the format, so a text file renamed to `.png` fails. SVG pictures, which are really small programs, are held to an allow list of shapes and attributes, with limits on how many elements and how deep they can go. For templates, the builder runs them in a sandbox that cannot reach the computer. For style sheets, it refuses anything that makes a visitor's browser contact another site.

Four rounds of security review attacked this code. Here are the kinds of mistakes they found. These are the same families that will show up in the next feature you build, so they are worth knowing. (The model groups them from the project's history and tests; the review reports themselves were not available to the author, so treat the grouping as a summary, not a transcript.)

1. **Text that is really code.** A template can run instructions. Escaping the output is not enough; the template itself needs a sandbox.
2. **Shortcuts in file paths.** Checking a path and then opening it leaves a gap in which the file can be swapped for a link to somewhere else. The fix is to check and open as one step.
3. **Two doors to the same room.** One kind of picture had its own, weaker check, so anything could be published through it. The fix is to put the check at the one place everything goes through.
4. **Block lists lose.** A list of bad things always misses one. An allow list of good things does not, so the SVG check became one.
5. **Two readers disagree.** The checker and the browser read a style sheet differently, so a forbidden word could hide where the checker did not look. When in doubt, refuse what you cannot read the same way the browser does.
6. **Leaks that are not scripts.** A strict security policy blocks stolen scripts, but a style sheet can still make every visitor's browser fetch a picture from a stranger, who then learns visitor addresses. Think about what makes the visitor's browser call out, as well as what runs.
7. **Limits you can trust.** A size limit must count what was really read. A complexity limit must stop early.
8. **Tests that pass for the wrong reason.** Several tests stayed green when the control was removed, and one could hang the whole run. A test that cannot fail is decoration.
9. **Your own tools are servers too.** The preview server on a developer laptop could be reached from a web page in another tab. It now only answers local, same-site requests.

Two things are still open there. Photos are published exactly as supplied, including any location tag a phone put inside them. And a style sheet can be checked, but a screenshot of a forbidden figure saved as a picture is a real picture and passes. Code cannot read the screenshot. That one is the owner's responsibility, and the model says so.

## Try it yourself

Take any feature from a project you maintain and fill in this table. Use made-up names.

| Question | Your answer |
|---|---|
| What is the most valuable thing this feature touches? | |
| Who can reach it with the least effort? | |
| Write one sentence: "an attacker does X to get Y." | |
| What control stops it? | |
| Name the test that goes red if the control is removed. | |
| What can still happen? Who accepts that? | |

If you cannot name the test, the control is a hope. Write the test first.

## How to read the full model

1. Read the legend at the top of the full model (shipped, planned, open) and sections 2 to 4 (assets, actors, boundaries) to see the shape.
2. Read the register in section 6 for the ranking, then pick a risk and read its mitigation and verification. The risks added in the second revision are T-16 to T-21, and T-07 was re-ranked.
3. Section 7a lists the decisions the maintainer still has to make. Those are the places where the model gave options and deliberately did not choose.
4. Section 8 is a numbered list of tests that plan authors copy into tasks.
5. Section 9 says what was not covered. Read it, because it is the part that stops anyone from treating the model as a guarantee.
