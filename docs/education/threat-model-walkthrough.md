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
- **Tokens and forwarded links.** The unlock pass is a signed cookie, not a link, so forwarding a URL gives the recipient nothing. The link in the email works once. A test uses the link twice and expects the second use to land on the gate. Another subtle point: some company mail systems open links automatically to scan them. The design has the link open a page with a button, so the scanner does not use up the one-time link.
- **Fake data.** Bots can add junk. The model does not pretend this can be stopped completely; it limits it and labels counts as approximate.
- **The Kick stream key.** One platform's API response includes a secret that lets anyone broadcast on the channel. The collector keeps a list of fields it wants and drops everything else, so even a new field cannot sneak through. The test uses a made-up key and checks it is nowhere afterward: not in logs, errors, the database, or exports.
- **Hostile configuration.** A configuration file can be crafted to eat memory, hide duplicate settings, or point at files outside the project. The loader refuses these. Tests feed it bad files.
- **Supply chain.** Every package and every automation step is pinned to an exact version or hash, so an update cannot silently change what runs.
- **Platform terms.** Some platforms forbid showing certain numbers in public. The site builder has a table of those rules, and it refuses to build a page that breaks them.
- **Personal data.** Collect the least, keep it the shortest time, and be able to delete it. Deleting has to reach every copy we control, and the privacy page says honestly about the copies we do not.

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

1. Read sections 2 to 4 (assets, actors, boundaries) to see the shape.
2. Read the register in section 6 for the ranking, then pick a risk and read its mitigation and verification.
3. Section 8 is a numbered list of tests that plan authors copy into tasks.
4. Section 9 says what was not covered. Read it, because it is the part that stops anyone from treating the model as a guarantee.
