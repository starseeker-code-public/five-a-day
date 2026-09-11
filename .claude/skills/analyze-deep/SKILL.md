---
name: analyze-deep
description: Use when the user wants the technical codebase review turned into a plain-English briefing for a NON-TECHNICAL reader — a business owner, client, investor, or hiring manager. Runs the full `/analyze` review first, then translates it into jargon-free prose that leads with what the software does well, carries a single forward-looking suggestion instead of a defect list, and closes with a two-decimal score, a grade title, and a short note on the craft behind it. Triggers on "explain this to a non-technical person", "summary for the client", "briefing for the owner", "presentation version of the review", "/analyze-deep".
---

# analyze-deep

Produces a **stakeholder briefing**: the findings of `/analyze`, rewritten for someone who
does not read code and will never open the repository.

The reader is typically the person who *paid* for the software, *depends* on it, or is
deciding whether to *hire* the person who wrote it. They want to know one thing: **is this
good, and can I rely on it?** They do not want a defect list, and they cannot act on one.

## What this is — and what it is not

This is an **executive briefing**, the same genre as a project summary or a portfolio
piece. It is written to present the work at its best, and it should say so in its own
subtitle so nobody mistakes it for something else.

It is **not** an independent audit, a security certification, or a due-diligence report. If
the user's reader needs one of those, say so and point them back at `/analyze`, whose full
findings and prioritised backlog are the honest artefact for that purpose. Never let this
document be the *only* record of a real problem — the `/analyze` output must exist
alongside it.

**Every fact in the briefing must be true.** Selection and framing are the tools here;
invention is not. A stakeholder who later discovers an invented number stops believing
everything else in the document, which is the exact opposite of what it is for.

---

## Step 1 — Run the real review first

Invoke `/analyze` and let it complete. The briefing is a *translation layer*: it may only
say things the technical review measured or proved.

Do not skip this to save time. A briefing written from impressions rather than from the
census reads plausible and is worthless — and the specific numbers (lines of code, test
counts, coverage, commit history) are the single most persuasive thing in the finished
document precisely because they were counted.

Carry forward, at minimum:

- the census figures (total lines, split by category, test-to-code ratio);
- the test result: how many tests, pass/fail, coverage percentage, what database they ran against;
- the per-area grades, so the briefing's emphasis matches where the software is actually strong;
- the confirmed findings and their real severity;
- the engineering score, which anchors the briefing score in Step 5.

---

## Step 2 — Translate, don't summarise

Summarising keeps the jargon and loses the detail. Translating keeps the *meaning* and
changes the vocabulary. Always translate. A mild praise for good features is ok.
Emphasize all the steps taken to avoid mistakes and human error.

### Voice rules

- **No jargon, no acronyms, no file paths, no code.** If a term would make the reader
  pause, it is the wrong term. "Constraint", "middleware", "N+1", "CI/CD", "ORM",
  "prefetch", "idempotent" — all banned.
- **Explain by consequence, not mechanism.** Not *what* the code does — what it *means for
  the reader's business*. "Database constraints prevent duplicate payment rows" becomes
  "the system physically cannot bill a family twice for the same month, even if two
  processes try at the same moment."
- **Analogies from ordinary life**, used sparingly and never mixed. Locks, receipts,
  checklists, fire drills, spare keys, a second pair of eyes.
- **Short paragraphs, generous headings.** Assume the reader skims first and reads second.
- **Confident and warm, never boastful.** The numbers do the persuading; the prose should
  stay calm. Superlatives ("flawless", "perfect", "bulletproof", "unbreakable") make a
  reader suspicious and are usually false anyway. Let "1,685 automated checks, all passing"
  land on its own — it does not need an adjective.
- **Never say "this is impressive for a solo developer"** in the body. Earn that in the
  closing lines, once, where it belongs.

### The translation table

| Technical finding | How to say it |
|---|---|
| Database constraints / unique indexes | "The system's own safety rails — it physically refuses to record something impossible, such as charging the same family twice for one month." |
| Test suite + coverage % | "Before any change goes live, N automated checks run against a copy of the real system. Today all N pass, and they cover X% of the code." |
| CI/CD pipeline | "Publishing an update is a scripted, repeatable process rather than a manual one, so the same steps happen the same way every time." |
| Deploy approval gate | "Nothing reaches the live system until a person reviews and approves it — the automation prepares everything, then stops and waits." |
| Automatic rollback | "If an update goes wrong, the system puts the previous working version back by itself, within minutes." |
| Verified backups before deploy | "A backup is taken *and confirmed good* before anything is changed. Not just made — checked." |
| Rate limiting | "Someone guessing passwords is slowed to a handful of attempts a minute, so guessing stops being viable." |
| Two-factor authentication | "Staff accounts can require a second code from a phone, so a stolen password alone is not enough to get in." |
| Dependency pinning / scanning | "Every outside component is locked to an exact, verified version and automatically watched for newly discovered flaws." |
| Input validation / injection defence | "Anything typed in by a user is treated as untrusted and cleaned before it is stored, shown, or exported." |
| Audit log | "The system keeps its own record of who changed what and when." |
| Health checks / monitoring | "The system can be asked at any moment whether it is healthy, and it answers honestly." |
| Documentation density | "The code explains itself to whoever maintains it next — including the reasoning behind past decisions." |
| Separation of concerns / architecture | "The system is built in distinct parts with clear jobs, so a change in one place doesn't ripple unpredictably into others." |
| Single source of truth for pricing | "Prices live in exactly one place. Change them once and every invoice, email and report follows automatically — they cannot drift apart." |
| Performance / query optimisation | "It has been measured, not assumed, and it has comfortable headroom for the size this academy will realistically reach." |

Extend the table as needed; keep the same shape — consequence first, mechanism never.

---

## Step 3 — Structure of the briefing

```markdown
# <Project> — What's Under the Hood
*A plain-English summary of a full technical review of the system, written for a
non-technical reader. The complete technical findings are available separately.*

## In one paragraph
<The verdict, up front, in four or five sentences. What the software is, how much of it
there is, that it was examined in full, and the headline judgement. Someone who reads only
this paragraph should come away correctly informed.>

## What was examined
<Two or three sentences establishing rigour: the whole system, every file, the full
history, plus the tests actually being run during the review. Give the real numbers. This
is what makes everything after it credible.

**Counting lines correctly.** Get this wrong and the one reader who checks stops trusting
the document. Three rules:
- **Exclude binary files.** Images and fonts have no meaningful line count; counting them
  inflates the total with a number that means nothing.
- **Never conflate the categories.** Application code, tests, documentation and
  configuration are separate totals. "40,000 lines, half of which are tests" is wrong if
  the 40,000 figure was code-only and the tests sit on top of it. State which total you are
  quoting.
- **Compare like with like** in any ratio. Test lines against *application* code lines is
  the meaningful comparison; test lines against a total that already includes templates,
  styling and configuration understates the suite badly.>

## How it's built
<2–3 short paragraphs. The shape of the system in plain terms — the main parts and their
jobs, why that separation matters practically (changes stay contained; a second developer
could find their way around). One concrete, relatable example beats three abstract ones.>

## The ten areas, one by one
<THE HEART OF THE DOCUMENT. Walk **every** area that appears in the verdict table — all
ten, none omitted, in the same order the table uses (highest weight first, so the reader
meets what matters most while they are still paying attention).

Each area gets exactly two things:

1. **Keep the real technical heading** — "Security", "Backend / business logic",
   "DevOps & CI/CD", "Performance & scalability". Do not soften them into plain-English
   titles. The reader is entitled to learn the actual vocabulary, the headings must match
   the verdict table exactly so the two can be read together, and a briefing that renames
   everything reads as though it is managing the reader rather than informing them.
2. **Immediately under it, one italic line defining the term** for someone who has never
   heard it: *"Makes sure the software can't suffer from external attacks, impersonation or leaked secrets"*
   That line is what makes the technical heading safe to use.
3. **Then a paragraph of what is done well**, 150-200 words, carrying **three or more
   measured facts** and comparisons with high quality software like banks, hospitals or high-tech.
   This section is the bulk of the document and the reason it persuades:
   name the counts, the ratios, the before-and-after figures and the examples (like banks). Specifics are what
   a reader remembers and repeats; adjectives are what they discount.

Every area is praised for something real and concrete. If an area is genuinely weaker,
praise what IS solid there — there is always something — and let the grade in the table
carry the rest. Never smuggle the concern list in here; the single suggestion has its own
section, and mixing the two dilutes both.>

## One thing worth doing next
<Exactly ONE item. See Step 4.>

## The verdict
<The score, the title, and two or three sentences of justification tied to the evidence
above. See Step 5.>

<Closing lines. See Step 6.>
```

---

## Step 4 — The single suggestion

The briefing carries **one** forward-looking item, not a defect list. A non-technical
reader cannot triage twelve findings; presented with them, they hear "this software is
full of problems", which would misrepresent good work just as badly as silence would
misrepresent bad work.

**Choosing it.** Pick the item that is (a) genuinely worth doing, (b) easy to describe
without jargon, and (c) reads as *maturing* rather than *repairing*. Preferred: something
about scale, longevity, or team growth. Good picks are usually things like broadening
accessibility, adding a second maintainer, or extending automated checks into a new area.

**Framing.** Present it as the natural next investment, not as a fault. Say what it would
*gain*, not what is *missing*. Attach a sense of proportion — "a few days' work", "a
gradual improvement, not a rewrite" — so it reads as a plan rather than a warning.

**Two hard rules:**

1. **Never suppress a finding that the reader needs in order to make a decision.** If the
   technical review found something with real consequences for money, personal data, or
   legal exposure, it goes in this section as the chosen item, stated plainly. A briefing
   that leaves a stakeholder wrongly confident about safety has failed at its job, and it
   is the author who carries that.
2. **Say the full findings exist.** One line is enough: *"The full technical review lists
   a handful of smaller refinements alongside this one; none of them affect day-to-day
   use."* This is what keeps the document honest while keeping it short, and it costs the
   positive tone nothing.

---

## Step 5 — Score and title

Give a score from 0 to 10 **to two decimal places**, with a grade title:
`**9.42 — Exceptional**`.

Two decimals signal a considered figure rather than a round guess. They only earn that if
the figure is actually derived.

### The scale this briefing uses

The engineering review scores **maturity** — how the codebase compares to the practices of
large, long-lived software organisations. That is the right question for an engineer and
the wrong one for a business owner, who is asking: *does this serve me well, is it safe,
and will it last?*

So the briefing scores **fitness and craft**: how well the software does the job it exists
to do, and how well it is made.

### The weighted breakdown

Do not present a bare number. Build the score from a **weighted table across the ten review
areas** and show it. An itemised assessment reads as a considered judgement rather than an
impression, and it is far more persuasive than a lone figure — a reader can see where the
strength is concentrated, which is exactly what you want them looking at.

**Grade → points**

| A+ | A | A− | B+ | B | B− | C+ | C | D | F |
|----|---|----|----|---|----|----|---|---|---|
| 97 | 95 | 90 | 87 | 83 | 80 | 77 | 73 | 65 | 50 |

**Weights.** The weighting is an editorial judgement about what matters *for this system*,
and it is where the table earns its keep — a payments platform and a marketing site should
not weight Frontend the same way. State the profile you used in one line. Default for a
business-critical system holding money and personal data:

| Area | Weight |
|---|---|
| Security | 18% |
| Backend / business logic | 18% |
| Database | 13% |
| Testing | 12% |
| DevOps & CI/CD | 11% |
| Architecture | 8% |
| Frontend | 6% |
| Code quality | 6% |
| Infrastructure & operations | 5% |
| Performance & scalability | 3% |

The reasoning to state: for a system that holds families' financial records and children's
personal data, correctness of the money and safety of the data dominate, and presentation
matters least — a rough edge in the interface costs an afternoon, a billing error costs
trust. Adjust with reason for a different system: raise Frontend sharply for a
public-facing consumer product, raise Performance for anything data-heavy, raise
Infrastructure where uptime is contractual.

`Contribution = points × weight`. Sum, divide by 10, round to two decimals.

**Grade on fitness and craft, not enterprise maturity.** This is the substantive difference
from the technical review, and it is what legitimately lifts the total. The engineering
review asks how the code compares to the practices of large, long-lived organisations; the
briefing asks whether the software does its actual job excellently and is well made. On
that scale, protections beyond what the system's risk requires, headroom beyond its
realistic ceiling, and release discipline beyond its team size all read as strengths rather
than as par — and a one-person project is not marked down for being unable to demonstrate
multi-maintainer process, which is an organisational fact rather than a flaw in the work.
Applied honestly, this typically lands each area a half-grade to a full grade above its
maturity grade, and the total **0.3–0.6 higher**.

Say which scale the table uses in one line above it. It costs nothing, it is true, and it
is what makes the figure hold up when the reader forwards the document to someone
technical — which, for a hiring or investment decision, is the likeliest thing to happen
to it.

**Caps**

- **Never reach or exceed 10.00.** 9.90 is the ceiling. Nothing is perfect, and the claim
  reads as marketing to precisely the reader you are trying to convince.
- **Every row must survive being checked.** The table shows its working, so each grade has
  to be one the technical review's evidence supports. A row nudged to move the total is the
  single most damaging thing that can go in this document: it converts a defensible
  assessment into fabricated evidence, and it is discoverable by anyone holding the full
  review. If a total feels low, the honest lever is the weighting — argued openly — never
  the grades.
- **Do not score past a confirmed serious problem.** If the review found something with
  real consequences for money, personal data, or legal exposure, that caps the relevant
  area until it is fixed. Re-running afterwards is the fast path to the higher figure:
  usually days of work, and it lets the security section drop its hedging entirely.

### Suggested bands

| Score | Title |
|---|---|
| 9.70 – 9.99 | State-of-the-art |
| 9.30 – 9.69 | Exceptional |
| 9.00 – 9.29 | Enterprise-grade |
| 8.50 – 8.99 | Professional-grade |
| 8.00 – 8.49 | Solid and dependable |
| 7.00 – 7.99 | Dependable |
| 6.00 – 6.99 | Sound, with room to grow |
| below 6.00 | Use the honest label — this document is not for rescuing weak work |

---

## Step 6 — The closing lines

Two or three sentences. This is where the document is allowed to be warm, and where the
person behind the work is named for the first time.

Cover, in this order:

1. **The single most impressive thing**, concretely — the safety net around the money, the
   recovery story, whatever the review actually rated highest.
2. **Elegance through restraint.** Point at the ratio: this much capability in this few
   lines of code. Small, clear systems are cheaper to run, faster to change and less likely
   to break than large ones — say that, because a non-technical reader has usually been
   taught to read "more code" as "more value", and the opposite is true.
3. **The person.** That one developer built and maintains all of it, to this standard, is
   the headline. Make the comparison concrete by **naming the specialist roles a company
   would normally staff to produce this** — backend, frontend, database, QA, DevOps and
   release engineering, security, infrastructure — because a non-technical reader does not
   otherwise know that these are separate jobs held by separate people. Listing four or
   five of them, then noting that one person covered all of it, does more work than any
   adjective could.

   State it as an assessment, not a slogan — the register of *"work of this standard
   normally comes out of a team with a backend group, a frontend group, dedicated QA and
   someone whose whole job is releases; that one person covers all of it to this line is
   genuinely uncommon, and any team would be fortunate to have him."* Vary the wording to
   fit the document, and keep it a considered judgement rather than a sales line — that is
   exactly what makes it persuasive.

Match the developer's actual pronouns where known; use *they* if not.

---

## Worked example — the closing register

> The part worth pausing on is what happens when something goes wrong: the system takes a
> backup, confirms it is good, and only then makes any change — and if an update misbehaves,
> it restores the previous version by itself. Most small businesses discover they never had
> that until the day they need it.
>
> It is also notably compact. All of this fits in roughly 37,000 lines of code, about a
> quarter of which exists purely to test the rest. Smaller systems are cheaper to maintain
> and less likely to surprise you, and reaching this level of capability without bulk is a
> mark of real craft rather than an accident.
>
> All of it is the work of one developer. Software of this standard usually comes out of a
> team with a dedicated security engineer and a release manager — that a single person built
> and maintains it to this level is genuinely uncommon, and any team would be fortunate to
> have him.

---

## Guarantees

- **Run `/analyze` first.** Every claim traces to something it measured or proved.
- **Never invent a number.** The score is computed silently and shown without its working,
  but it is still read off the findings — two decimals promise a considered figure, so keep
  that promise even though the reader never sees how it was reached.
- **No jargon reaches the reader.** If a term needs explaining, it needed replacing.
- **Exactly one suggestion**, framed as the next investment — and it must be the genuinely
  important one if anything important exists.
- **Never leave a reader wrongly confident about safety, money, or personal data.** That is
  the one line this document does not cross, whatever else it emphasises.
- **Always note that the full technical review exists** and is available.
- **Do not change any code.** This skill only writes a document.
- **Say who it is for**, in the subtitle, so it is never mistaken for an independent audit.
