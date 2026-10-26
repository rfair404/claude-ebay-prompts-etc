---
version: 3.5.0
updated: 2026-10-26
---

# RUN — v4 headless runbook

The single entry point. To run the pipeline on a shoot, read THIS file
plus [`prompts/_shared.md`](prompts/_shared.md), then load each phase
prompt on demand as you reach it. You do not need to read all phase
prompts up front.

**Goal:** carry a shoot from photos to a review-ready artifact with no
human babysitting — stopping only at the HARD gates below. In `list`/`full`
mode the run carries through to a **REVIEW gate**: it presents a decision
card and stops for one explicit human approval, which is what publishes the
listing LIVE.

---

## Invocation

The user points at a photo directory and (optionally) names a workflow
mode. That directory is the shoot directory; all outputs land there.

**Resolving a bare name — always look in `inventory/` first.** When the user
says just "run `<name>`" (e.g. "run sand-dollars"), the shoot directory is
**`inventory/<name>/`** — start there before anywhere else. `inventory/` is the
default content store ("our" data: photos + per-item phase outputs; gitignored,
never version-controlled). Only treat `<name>` as a different location if it
isn't found under `inventory/`, or the user gives an explicit path.

**There is one `inventory/`, and it is the main checkout's.** Pipeline output
always goes there, even from a session in a worktree. A worktree's
`inventory/` is a junction to `<main>/inventory/`, created by
`tools/worktree_link.py` when the session starts. So write through the
worktree path (`<worktree>/inventory/<name>/draft.md`). The desktop app's
worktree write guard refuses the spelled-out `<main>/inventory/...` path and
every other base-checkout path. `reports/` is linked the same way, so a
report written from a worktree lands in main's `reports/`, which both offsite
backups copy. See CLAUDE.md, "Pipeline data".

    plan  <photos-dir>   → IDENTIFY → PRICE → CURATE                 (pre-buy: buy list)
    list  <photos-dir>   → PREP(gate) → INVESTIGATE → DRAFT → REVIEW(gate)→publish
    full  <photos-dir>   → all in order, ending at the REVIEW gate

If no mode is given: a single-item or grouped shoot of items the user
already owns → `list`; a wide field/estate scene → `plan`. State the
inferred mode in your first line and proceed (it's a SOFT call).

**Single phase vs. the sequence.** "run" defaults to *run the steps in
sequence* — a bare name or a mode keyword (`plan`/`list`/`full`) runs the
pipeline. But a **phase keyword runs ONLY that one prompt and stops** (it does
NOT continue to the next phase):

    identify    <name>   → only IDENTIFY     ([prompts/identify.md](prompts/identify.md))    → identify.txt
    prep        <name>   → only PREP         ([prompts/prep.md](prompts/prep.md))            → listing/ (HARD gate)
    price       <name>   → only PRICE        ([prompts/price.md](prompts/price.md))          → price.txt (+ comps.csv)
    curate      <name>   → only CURATE       ([prompts/curate.md](prompts/curate.md))        → review.md
    investigate <name>   → only INVESTIGATE  ([prompts/investigate.md](prompts/investigate.md)) → investigate.txt
    draft       <name>   → only DRAFT        ([prompts/draft.md](prompts/draft.md))          → draft.md (+ --record)
    review      <name>   → only REVIEW       ([prompts/review.md](prompts/review.md))        → review_card.md + review_card.html (HARD gate)
    report               → only REPORT       ([prompts/report.md](prompts/report.md))        → performance numbers (+ docs/performance-<date>.md)

**REPORT takes no shoot name** — it is account-wide, not per-item. "report",
"how are we doing", "what did we actually make", "what sold this week" all land
here. It is the only phase that reads eBay's *outcomes* rather than producing a
listing, and it never publishes or edits anything.

So "run identify sand-dollars" (or just "identify sand-dollars") runs IDENTIFY
alone on `inventory/sand-dollars/` and stops — it does not roll on to PRICE. A
single phase reads its normal inputs (the prior phases' files already in the
shoot dir) and writes only its own output; use it to re-run or fix one step
without redoing the whole pipeline. The same `<name>` → `inventory/<name>/`
resolution applies.

Run phases in order. Each phase reads the prior phase's file from the
shoot directory, so phases compose without re-deriving.

---

## Item states and handoffs

Every item is in exactly one state, and every state has exactly one owner.
The owner moves the item to the next state and hands it on. Everyone else
who works on it (inventory-buyer, price researcher, copywriter,
photo-editor, art director, historian, presenter) is a helper: a helper
returns its output to the owner and never moves the item itself. Most names
are `client_ledger.csv` states; REVIEW and LIVE map onto ledger values as
noted under the table. The table says who owns each state and what "done"
means.

| State | Owner | Done when (the evidence) | Hands to |
|---|---|---|---|
| **INTAKE** | inventory-manager, through `/intake` (`.claude/skills/intake/SKILL.md`); the inventory-buyer helps and keeps the cost | The item has a row in its bin's `catalog.csv`, plus an item folder with `intake.txt` (owned) or a `client_ledger.csv` row (consigned). For an owned bin, the inventory-buyer makes sure the bin's `context.txt` has `spend:` and `acquired:`; it never moves an item. | Russell (shoot), via a shot list on his card |
| **SHOT** | Russell, with the digital-tech | One item per folder, frames per the shot list, marks and measurements read aloud and in frame. | inventory-manager |
| **DRAFTED** | inventory-manager | `identify.txt`, `price.txt`, `investigate.txt` and `draft.md` exist. | listing-manager |
| **REVIEW** | listing-manager | `list_edit.py --record` has stamped the SKU (the item number) **and the ledger row exists** (`--record` exits 0 and `grep <sku> listings_ledger*.csv` in the main checkout finds the row), PREP approved under the art director's sign-off, draft repointed to `listing/`, `list_edit.py --review` passes, the review card is built, and the presenter has shown it to Russell. | Russell (publish yes/no) |
| **LIVE** | listing-manager until the live read-back matches the approved card, then the inventory-manager | Ledger `PUBLISHED` (client ledger `LISTED`), listing URL reported, live price, Best Offer floors, shipping, photos and promotion match the card. | inventory-manager (aging), listing-manager for any approved revision |
| **SOLD** | shipping-assistant | Label bought, item packed, tracking uploaded within handling time. | accountant |
| **ENDED** (unsold) | inventory-manager | A next step chosen: relist, lot, junk store, live-show slot or return. A relist goes back through the listing manager. | listing-manager or Russell's card |
| **CLEARED / SETTLED** | accountant | Fees, ad fee and postage booked. A consignment row is cleared by `consign sync --apply` and paid by a settlement. | (closed) |
| **RETURNED / DISPOSED / EXCLUDED** | typed by hand on Russell's word | `state_note` says why (EXCLUDED also needs `buyout_price`). | (closed) |

In `listings_ledger*.csv`, `DRAFTED` and `SYNCED` (an eBay draft offer, not
live) are REVIEW, `PUBLISHED` is LIVE, `SOLD`/`SHIPPED` is SOLD and `ENDED`
is ENDED. On `client_ledger.csv`, `DRAFTED` means `draft.md` exists, `LISTED`
is LIVE (on eBay, or on the mall floor once `placed_at` is typed), and a hand-entered `PREP` is an on-hand label that `consign sync`
keeps until the item is LISTED. A hand-entered `HOLD` (its `state_note` says
why) is an on-hand hold that `sync` keeps past an ended listing, flags if a
live listing matches, and gives up only on a sale.

**Waiting is not a state.** An item waiting on Russell's card, a reshoot or
another agent stays in its state with the same owner. A `NEEDS_REVIEW.md`
line never blocks a state change; only a missing piece of evidence above
does.

**Done means the evidence exists.** A worker is done with an item when every
item in its row's "Done when" column can be shown: the file is there, the
ledger row reads right, the exit code was 0. "I ran the phase" is not done.
If one piece can't be finished, the worker stops and reports **BLOCKED**
with the one missing thing and who can supply it. It doesn't move the item,
and it doesn't start the next owner's work to get round the gap.

**Every handoff carries the same five lines**, whoever sends it:

    item:      <item id or shoot dir>   (and SKU once it has one)
    state:     <the state it is now in>
    evidence:  <the files / ledger row / URL that prove it>
    open:      <NEEDS_REVIEW.md lines, or "none">
    next:      <the next owner, and the one thing they do first>

The receiver checks the evidence before taking the item (`python -m lib.cli
status <shoot-dir>` shows phase files, PREP state, ledger row and next
action in one call). If the evidence is short, the item goes back to the
sender, not forward.

---

## Concurrency and delegation (#61/#62)

**Trigger: a multi-item batch** — "run everything in `<folder>`", a plan-mode
estate scene with several saleable items, a backlog sweep. A single item, or
an interactive back-and-forth about one piece, stays in the main thread; fan
out only earns its overhead across several items. Measured cost of *not*
doing this: mean 291,787 tokens of accumulated context per turn, 1.00 tool
calls per assistant turn, one "run all" session that took 725 turns for a
single folder (#61).

**Main thread = conductor.** It holds only: the batch manifest (which items,
what phase each is in), the **gates**, and any write that touches shared
state. It never holds a raw photo, a comp JSON, forum matches, or a worker's
full reasoning — those are what make the window huge.

**One worker (`Agent` tool) per item**, covering IDENTIFY→PRICE→
INVESTIGATE→DRAFT, **capped at 4 concurrent** (PRICE hits the logged-in
browser and eBay Browse; more trips rate limits). Each worker starts on a
fresh context, reads
[`prompts/_shared.md`](prompts/_shared.md) plus the phase prompt it needs —
not optional, the honesty rules and in-hand voice live only in the prompt
files — writes its outputs to the shoot dir exactly as a normal run would,
and returns a **compact verdict only**: item name, proposed title, price +
tier, the ⚠ list, and the path to `draft.md`. The filesystem is the handoff;
the conductor never needs a worker's reasoning, only its verdict.

**IDENTIFY runs on Opus 5.5** (Russell, 2026-10-26). Spawn every item worker
with `model: "opus"` on the `Agent` call, whatever model the session itself
runs. A lone `identify <name>` goes to one such worker too, unless the session
is already on Opus. The lighter agents (listing manager and
presenter) run on Sonnet through their own `model:` line, so the conductor
never picks a model for them.

**The ledger is the one hard constraint.** `listings_ledger.csv` is a single
unlocked CSV — `list_edit.py --record` / `--sync` / `--list --confirm` all
read-modify-write it, and two callers at once lose a row. Every ledger-
touching call runs in the conductor, serialized, never inside a worker —
which is also where it already belongs, since it follows the gate. In the
agent team the conductor's ledger seat is the listing manager, one job at a
time (see "Item states and handoffs").

**Gates become a queue, not a barrier.** A finished item's review card joins
a queue instead of stopping the run; the user answers gates whenever they're
next at the machine, and other workers keep going the whole time. The gates
themselves do not weaken — REVIEW still publishes on one explicit
per-item approval, never inferred, never batched into "approve all" — they
just stop being a global stall. (Measured: 229h blocked across 229 asks,
mean 1h each; colour alone was 81h against a confidence gate `prep.md`
already specifies — see PREP's `--auto`/`--approve-auto` above.)

**Background anything over ~30s.** `prep --auto` renders images and nothing
else in the run depends on it finishing — kick it off with
`run_in_background`, run PRICE Stage A/B while it renders, collect it when
it lands. The same applies to any other foreground call in that range.

**Always start these backgrounded — by command prefix, not judgment call**
(measured across 122 sessions, #74; prefixes match the real invocation
forms used throughout `prompts/`, not bare names):
`python -m lib.photo_prep.prep`, `python -m lib.cli prep`,
`python -m lib.cli single-pass`, `python -m lib.cli observe`,
`python -m pytest`,
`python tests/run_all.py`, `tools/ci_test.sh`, `tools/green_test.sh`,
`python tools/ledger_reconcile.py`,
`python tools/prep_sheet_html.py`, `python tools/review_card_html.py`,
the comp-hunt call
(`python lib/ebay_sold_browse.py` — Apify is retired, see
[`docs/archive/pricing-backend-issues.md`](docs/archive/pricing-backend-issues.md)),
`python lib/lens_id.py`, `python lib/list_edit.py` (the EPS photo upload
alone runs a minute). Each one routinely runs long enough that
foregrounding it is a wasted turn waiting on nothing the next step needs
yet.

**This one is enforced, not advised (#121).** The rule above was
documentation from #74's audit until a 7-day observer read measured what
that was worth: **54 shell calls over 30s totalling 9.8h of blocked
wall-clock, with 2.2% of 947 calls backgrounded.** A `PreToolUse` hook
([`tools/bg_guard.py`](tools/bg_guard.py), wired up in
`.claude/settings.json`) now **denies** a foreground call whose command
runs one of those prefixes — matched per shell segment, so the usual
`cd "<dir>" && python -m …` form is caught — and the deny message says to
re-issue the same call with `run_in_background`. The prefix list lives in
`bg_guard.py`; `tests/test_bg_guard.py` fails if this section stops naming
one of them. A genuinely instant invocation of a listed tool (`--status`,
`--check`) opts out with a leading `# fg-ok: <reason>` comment — stated on
the command line, never silent.

**Run the full suite in CI, not here.** For a commit not yet on a PR, commit,
then run `tools/ci_test.sh [branch]` (backgrounded; the current branch by
default). A PR already runs the suite on every push. The script force-pushes
the branch's commit to `ci/<branch>`, a throwaway ref it owns and deletes on
exit (never `main`, which it refuses). `.github/workflows/tests.yml` runs on
every push to `ci/**`. The script watches the new run of that exact commit and
exits non-zero with the failed jobs' log tail if it fails. It refuses
uncommitted changes, because CI only tests the commit. `ci/` is reserved for
`ci_test.sh`; never name a working branch `ci/…`. A full local run peaks near
900 MB, and sessions running it side by side beside the app, Firefox and OBS
got the desktop app OOM-killed (2026-10-24). CI runs on GitHub, so the on-air hold
below does not stop `ci_test.sh`. Local `python -m pytest <one test file>`
stays. Green is print-only now: `tools/green_test.sh` is deprecated, use
`tools/ci_test.sh`.

### Green: the print server

Green is a print server and nothing else (Russell, 2026-10-24): its CUPS
spools the Brother QL-1100 for the LAN. It runs no desktop, no browser, no
tests and no shipping screen. Each box's size and weight form is on its pick
sheet, and heavy jobs are going to a cloud service rather than to
green. The eBay API keys and refresh tokens, the R2 keys, `config.yaml` and
the ledgers stay on the laptop, and green holds no login. Every connection
opens from the laptop over `ssh green` (host `green.lan`, key
`~/.ssh/green`, in `~/.ssh/config`), so green has no way back in. Green is
optional: if it is down, label jobs wait in the laptop's queue, and
`--laptop usb` puts the printer back on the laptop.

    python tools/green_setup.py --dry-run         # read green, print every step; change nothing
    python tools/green_setup.py                   # apply: sudo here, then sudo on green
    python tools/green_setup.py --close-others    # also switch ufw on over green's other listeners
    python tools/green_setup.py --laptop skip     # green only; the laptop's queue stays as it is
    python tools/green_setup.py --laptop usb      # cable back on the laptop
    python tools/green_setup.py --remove-desktop  # purge the desktop an earlier run installed

It reads green first: OS, the printer on USB, the listening ports and ufw.
Then it does only what is missing:

- **Driver on the laptop.** The laptop's queue keeps its name `QL-1100` and
  the Brother PPD (`ql1100pdrv` 2.1.4), so browser print dialogs and
  `tools/print_label.sh` see `103x164` and the other label sizes. With the
  cable on green it forwards the filtered job to
  `ipp://green.lan:631/printers/QL-1100`, and jobs retry while green is
  down. After the repoint the tool checks that `lpoptions -p QL-1100 -l`
  lists `103x164` and fails loud if it doesn't. A raw forward (the first
  version) had no PPD, so dialogs showed no label sizes. An `-m everywhere`
  queue was not used: it needs green up to be created, and its size names
  are not the PPD's `103x164` that `print_label.sh` asks for.
- **Laptop, first.** With the cable on green, the laptop's queue points at
  nothing until it is repointed, so this step runs before green's. The
  repoint is refused while the printer is still on the laptop's USB.
  `print_label.sh` also checks the far queue and says if green's is paused.
- **Green's queue.** CUPS and ufw only, then a raw `QL-1100` queue on
  green's USB, shared. CUPS 2.4 warns that raw queues are deprecated; they
  still work, and green needs no driver.
- **Sharing, LAN only.** `cupsctl --share-printers --no-remote-any
  --no-remote-admin` limits CUPS to the local subnet. ufw opens 631/tcp and
  SSH to green's own /24 and nothing else. ufw is not switched on over other
  listening ports unless `--close-others` says so.

Nothing is applied while `inventory/_live/ON_AIR` exists.

**The desktop is opt-in, off for now.** `--desktop` (with `--autologin
yes|no`) still adds XFCE with LightDM (LXQt under 2 GB of RAM) and Firefox,
last, after the printer. `--remove-desktop` undoes a
desktop run. It reads green's apt history for the desktop installs this
script ran (`apt-get install -y -q` with only desktop packages named) and
purges those of `xfce4`, `xfce4-terminal`, `lightdm`, `lightdm-gtk-greeter`,
`xorg` and `firefox`/`firefox-esr` that are still installed, plus what only
they pulled in (`apt-get purge --autoremove`). It also removes
`50-pops-autologin.conf` and the Ship launcher an older run wrote, and sets
`multi-user.target`. If apt would remove a package the script didn't
install, the cleanup marks it manual and keeps it, or stops before changing
anything. The dry run shows the list.

**Next on green: MicroK8s off, then the firewall.** Green also runs
MicroK8s (snap) and LXD. Russell's user has no MicroK8s access, and it
listens to the LAN on 10250, 10257, 10259, 16443 and 25000 with ufw off. A
print server needs none of it. `sudo snap disable microk8s` stops it and
keeps its data (`snap enable microk8s` brings it back). Then
`green_setup.py --laptop skip --close-others` switches ufw on, so only SSH
and CUPS answer from the /24. LXD's bridge (`lxdbr0`) loses DHCP under
ufw's default deny, which only matters if a container is in use there.

**While a live show is on air, batch jobs wait, backgrounded or not.**
`show_flow.py golive` writes `inventory/_live/ON_AIR` and `stop` removes
it; while it is there the same hook denies pytest, `show_music.py build`,
PREP, `prep_sheet_html.py`, `single-pass` and listing runs (the 2026-10-21
show skipped 99.4% of its frames beside them). A flag over 8 h old is
ignored. A leftover one clears when `show_flow.py status` reads eBay and
Twitch both off; never stop the stream to get past it. The opt-out is `# on-air-ok: <reason>`.

**Never poll a backgrounded job with `sleep`.** A `sleep N` loop burns a turn
per poll for no signal a proper wait doesn't already give you — a
`run_in_background` Bash call's output is available on demand without
waiting for it to finish, and its result also arrives on its own with the
next tool use once it's done. Use one of those instead of guessing an
interval and sleeping through it.

**`ebz status <shoot>`** (`python -m lib.cli status <shoot-dir>`) replaces
the `ls`/`cat`/`grep` sequence for "where is this item": phase files
present, PREP's approval state, frame count, ledger row, and the next
action — one call instead of several.

---

## The gate contract (the whole point of headless)

Reclassify every "ask the user" moment as HARD or SOFT.

### HARD gate — the only thing that stops a run

1. **The REVIEW gate (publish).** In `list`/`full`, after DRAFT, run
   REVIEW: present the decision card ([prompts/review.md](prompts/review.md))
   and STOP. Publishing the listing LIVE happens ONLY on the user's
   explicit approval at this card. No automatic publish, no publish from a
   "just list it" said before the card, no publish inferred from
   "ok"/"looks good"/silence. (This replaced the old absolute no-publish
   firewall — publishing is now gated here, not forbidden.)

2. **The PREP photo gate — three stages, three approvals.** After IDENTIFY,
   PREP walks the operator through **orientation, then crop, then colour**, in
   that order, one sheet per stage, each sheet showing every option for that
   stage side by side as thumbnails. You do not move to the next stage until the
   user says the current one is right, and the code enforces it: a stage will not
   open until its predecessor is approved, approving a stage clears every later
   sign-off, and `--apply` refuses to write `listing/` until all three are in.
   A `--apply` that could not render every frame exits NON-ZERO, leaves
   `listing/` untouched and names the frames in `apply_run.failed` — check the
   exit code before building a review card, especially on a backgrounded
   `--jobs N` run, which is where a rendered-nothing apply used to read as a
   success (#138).
   Photos go live ONLY after the user approves. Unlike the maker-mark gate this does NOT degrade in a headless
   run — it halts, because a bad photo is the one error buyers see first and 66
   sideways ones shipped while the rules said otherwise. It is enforced in code
   too: `upload_photos_to_eps` refuses photos that are not prepped and approved,
   and an approval goes stale the moment a source or output file changes.
   Flip it to soft only when the user says so.

3. **The IDENTIFY maker-mark gate (interactive only).** In a gate category —
   jewelry, precious metals, glass, pottery/ceramics (editable list in
   [prompts/identify.md](prompts/identify.md)) — when a maker's mark is plausibly
   present but undecisive from the photos, IDENTIFY STOPS and asks the user to
   read the inside/underside marking before spending on searches/Lens or settling
   Brand. A clear no-mark-likely item (plain modern glass, generic terracotta)
   takes a logged exception and proceeds. This gate exists only when a human is
   at the keyboard; in a headless run (cron, `full`/`list` with no one to answer)
   it degrades to the SOFT path — `needs_followup_photo` + a `NEEDS_REVIEW.md`
   line — and the run proceeds.

**PREP and REVIEW both stop a headless run** — PREP because photos are the first
thing a buyer judges and the rules alone did not hold, REVIEW because it is what
authorises publishing. The maker-mark gate above is interactive-only and
degrades. PRICE's Stage B comp call used to
be a second HARD gate (when it ran through Apify, a paid per-run API); it
no longer is — Stage B now runs automatically through the operator's own
logged-in eBay session (`lib/ebay_sold_browse.py`; Apify was retired
2026-08-15, see [`docs/archive/pricing-backend-issues.md`](docs/archive/pricing-backend-issues.md)),
which has no per-run cost to confirm. See PRICE for the Stage-A/B/C
ordering and the currency-leak validator.

### SOFT gates — proceed with the default, log it

Never block on these. Pick the documented default, append ONE line to
`<shoot-dir>/NEEDS_REVIEW.md`, continue.

| Situation | Default to take | Logged so the user can… |
|---|---|---|
| Photos suggest a pair/set/lot the user didn't name | keep `single` | confirm the grouping later |
| unit_type genuinely ambiguous (set vs lot) | choose the LOWER claim (`lot`; `single` over `pair`) | upgrade if warranted |
| INVESTIGATE open question (a feature not photographed) | commit to most-likely call without it | answer / re-shoot to firm up |
| No user-approved working price yet | adopt PRICE's **Recommended** tier as *provisional* working price | confirm/refine at publish |
| `lookup_only` value not canonical (e.g. "USA") | substitute closest canonical ("United States") | verify |
| Required field has no source data | leave empty, flag | fill it |
| Item below profit floor | route to CURATE SKIPPED with the math | override per category |
| Item ship-risky (>25 lb or >24 in/side, or fragile) | keep `SHIP`; suggest local pickup, log it | confirm local-pickup (attended: DRAFT asks before assuming) |

**Working price is NOT a HARD gate.** PRICE still records "final price
deferred to publish time", but headless flow auto-adopts the Recommended
tier as the working price so DRAFT can complete. The final published price
remains the user's call — it is confirmed (or changed) at the REVIEW gate
before anything goes live. Nothing publishes before that gate.

### NEEDS_REVIEW.md format

Append (don't overwrite) one line per deferred decision:

    [PHASE] <shoot-item> — <decision taken> · <what the user could change>

Example:

    [IDENTIFY] items 2-3 — kept as 2 singles (default); possible brass-candlestick pair
    [IDENTIFY] silver-pot — Brand left Unknown; mark present but illegible, needs base macro (maker-mark gate degraded, headless)
    [PRICE] iron — adopted Recommended $48 as working price; no exact comp, era-peer anchor
    [INVESTIGATE] iron — committed "Size 5 sad iron"; maker stamp not photographed

At the end of a run, if `NEEDS_REVIEW.md` has entries, surface the count
in your closing line ("3 items need review"). That's the async review
queue — the user reads it when convenient instead of being interrupted.

---

## Phase pointers

Load each prompt when you reach its phase.

## Ops commands — one entry point

Every account/ops tool runs through the `ebz` dispatcher (V4_PLAN Phase 3):

    python -m lib.cli                      # list the commands
    python -m lib.cli <command> [args...]
    python -m lib.cli --store junk <command> [args...]   # one named store
    python -m lib.cli --all-stores <command> [args...]   # once per store

Commands tagged `[store]` in the listing touch an eBay account or its
per-store files and take `--store NAME`; without it they use `$EBAYBIZ_STORE`,
then `ebay.active_store`, then the default store. See
[docs/STORE-CONNECTION-README.md](docs/STORE-CONNECTION-README.md) Part 3.

`reconcile` (ledger vs Sell API — eBay wins), `live-audit` (local files vs
live state, `--apply` to heal), `pick-list` (orders awaiting shipment),
`policy-sweep`, `price-audit` (asks above their own comp evidence),
`sales-report`, `promote`, `voice` (in-hand linter, `--audit` for a tree),
`listing` (the LIST/EDIT CLI), `prep`, `single-pass` (gate-check
IDENTIFY→PREP→PRICE→INVESTIGATE→DRAFT for a routine item; see
[prompts/single_pass.md](prompts/single_pass.md)), `observe` (session
transcripts -> friction report; read-only, `--report` to print it,
`--economics` for the #61 run-cost table — context/turn, tool-call
batching, image Read payload, gate wall-clock by name — the standing
version of the one-off `.scratch/analyze*.py` audit, `--propose` for
#36's ranked, evidence-cited proposed fixes — add `--file` to actually
create/comment the house `Idea:` issue via `gh`; without `--file` it's a
dry run that only prints what it would do), `status` (one-shot shoot
state: phase files, PREP gate, frames, ledger row, next action —
#61/#62). Argv passes through untouched, so every flag documented for a
tool works identically under `ebz`. The direct `python tools/<x>.py` /
`python lib/<x>.py` invocations keep working.

**Single-pass mode (routine items, V4_PLAN Phase 4, #30).** For an item
that never needs a mid-pipeline question — a single-item shoot, non-gate
category, ordinary condition — run the five stages in one sitting instead
of five conversational turns, and stop for exactly one review card at the
end. `python -m lib.cli single-pass <shoot-dir>` is the read-only gate check
across whatever each stage has already written: clean → the same card
REVIEW would build; anything a stage's own interactive HARD stop caught
(PREP's confidence gate, IDENTIFY's maker-mark/poll gate, the marble crop
gate) → the specific exception, by name, never guessed. Full protocol:
[prompts/single_pass.md](prompts/single_pass.md).

**Consignment (`consign`).** Each client has one folder,
`inventory/CONSIGN-<client_code>/` (the code only, e.g. `CONSIGN-K01`), with
one `client_ledger.csv` covering every intake: one row per sellable unit (one
listing, return, disposal or buyout), with the date it came in in `intake`.
The older layout, one `inventory/CONSIGN-YYYYMMDD/` folder per intake whose
`context.txt` says `client: K01`, still works and can sit beside the new one
until it is renamed by hand. A folder, and every lot in it, belongs to one
client. Names and contacts live only in `inventory/_consign/clients.csv`
(`client_code,name,contact,split,terms,opened`; `split` 0.5 = 50/50 of net).
Its optional columns are `agreement_signed` (date), `w9_on_file` (yes/no, a
flag only, never the TIN), `payee_type`, `payout_method` (last 4 digits at
most), `payout_cadence`, `floor_main`, `floor_junk`, `loss_rule`,
`abandon_election`, `handling_fee` (blank = no fee; set it, normally to 2.00,
only when the client's signed agreement names the fee), `min_payout` (blank =
none; 500 for K01), `listing_days` (blank = 180) and `pickup_days` (blank =
30). `loss_rule` is `split` (the default: the client carries their split of a
loss), `client_never_owes` (a loss pays the client $0) or `client_bears_loss`
(the client carries the whole loss, not split; K01's agreement).

    python -m lib.cli consign import CONSIGN-K01 roster.csv --client K01 --intake 2026-11-05
    python -m lib.cli consign import CONSIGN-K01 -          # paste a CSV/TSV on stdin
    python lib/sync_actuals.py --all-stores --apply        # finance sync first
    python -m lib.cli consign sync                         # dry run: what would change
    python -m lib.cli consign sync --apply                 # write (backup first)
    python -m lib.cli consign report --client K01 --csv    # -> reports/consign/K01/K01-<date>.csv
    python -m lib.cli consign settle K01 --through 2026-11-30          # dry run
    python -m lib.cli consign settle K01 --through 2026-11-30 --apply  # write it

`import` needs a `description` (or `item`) column, gives each new row the
client's next id (`K01-C048`, numbered across all of that client's ledgers),
stamps `intake` (`--intake`, else `acquired:` in `context.txt`, the folder's
date, or today) and skips a row with the same intake, bin and description.
It also guards against the same object coming in twice (a bin frame, then its
own shoot; a second import). Each new row is checked against every row in the
client's ledgers (`lib/dupe_guard.py`). When exactly one row has the same item
folder (not a lot-mate's shared folder), or a frame filed in that row's own
item folder and on no other row, the new row is that object: its frames go
onto the row with `re-run IDENTIFY` in its `why`, and no row is made. Anything
else is added as before. If a row reads alike (shared description words, plus
0.1 for the same bin and 0.25 for a triage `possible duplicate of` note naming
it, 0.35 or more), the added row gets a `POSSIBLE_DUPE` line in
`dupe_queue.csv` in the intake folder for the inventory manager. A shared bin
walk-through frame is never proof: every item in the bin shares it.
`--new "<reason>"` never attaches and writes the reason in each row's `why`. New catalog rows from camera triage
and `/intake` go through the same check (`python tools/tg7_triage.py add`).
Run `consign sync` from the main checkout; from a worktree it refuses. `sync`
reads the listings and sales ledgers only (no eBay calls) and moves each row
through SHOT, DRAFTED, LISTED, SOLD, CLEARED and SETTLED. A row clears once
it is sold, its ad fee and postage are known, and the later of 40 days after
the sale and 30 days after `delivered_at` has passed (eBay's 30-day return
window runs from delivery). Nothing supplies `delivered_at` yet, so type it
by hand from the order; without it a row clears 40 days after the sale and
is flagged "no delivery date". SETTLED means a paid settlement covers the
row. Typed by hand:

- PREP: in hand and being prepped. It stays PREP through SHOT and DRAFTED
  and gives way to LISTED and later.
- RETURNED, or DISPOSED with a `state_note`. Terminal: these win.
- EXCLUDED: Russell buys the item from the client. Terminal, and it needs a
  `buyout_price` (0.00 is allowed, blank is not). That price is the client
  share, with no split, and is owed until paid. A 0.00 buyout is closed with
  nothing owed and needs no payment.

**Lots.** Rows sold as one listing share a `lot_id` and one
`inventory_folder`. The lead row (the lowest id) carries the sale, the money
and the client share. Its lot-mates follow its state with blank money fields
and the note "in lot L1 with K01-C001", and the report counts the lot once.

**Splits.** The client's `split` is copied into `split_locked` the first time
a row sells, and that split is used from then on. A later change in
`clients.csv` applies only to rows that sell after it.

**Handling.** Each sale pays a flat handling fee for packing materials (box,
fill, tape, label): `net = net_before_postage - ad_fee - actual_postage -
handling - other_costs`, then the split. It comes off before the split, as
postage does, so a $2.00 fee costs the client $1.00 at 50/50. The fee is the
client's `handling_fee` (blank = no fee), stamped on the row's `handling`
column when it sells; a later change in `clients.csv` doesn't move it. Type a
larger figure into `handling` for an oversize box. A lot pays one fee, on its
lead row. Rows paid before the fee existed are left alone. The report shows
"handling kept", and settlements and statements carry a `handling` column.

**Other channels and the mall.** A blank `channel` is eBay. Any other
`channel` (one word: `dealer`, `etsy`) is a sale typed by hand into the row:
`sold_at`, `item_price`, `ebay_fee` (that channel's fee, 0.00 if none),
`actual_postage` (0.00 if none) and `order_id` if it has one. `sync` never
overwrites those or calls the sale vanished, and flags an eBay sale that
matches the same item. Type `funds_received` when the channel's money reaches
us: the sale then clears at the end of the month it sold (agreement §5.1(b)),
so it is paid on the 15th after, and it is flagged until that date is typed. `channel` `mall` is
the antique-mall booth, where every consigned antique sells: type `sold_at`,
`item_price` and `card_paid` (the part paid by card, 0.00 for cash), and sync
works out the mall's fee, 10% of `item_price` plus 3% of `card_paid`
(agreement §4.2(b); `MALL_COMMISSION` and `MALL_CARD_RATE`). A mall sale has
no postage. Handling applies on every channel.

**On the mall floor.** When an item goes into the booth, type the mall's own
SKU (the GAM SKU from SimpleConsign) in `listing_id` and the date it went out
in `placed_at`. `sync` then keeps the row `LISTED`, the report counts it
under listed at its `ask` (the booth tag), and the listing-window flag counts
from `placed_at`. Off eBay, `listing_id` is never looked up in the eBay
ledgers. `sync` refuses `placed_at` on an eBay row, or without a
`listing_id`. A row with no `placed_at` stays on hand (`PREP`).

**Floors and time limits.** These only raise flags. A row's `floor` (from
Exhibit A; a lot's floor is the total of its rows) is checked against the ask
while listed and against the price once sold. A listing live longer than the
client's `listing_days`, counted from its first `published_at`, is flagged to
end and tell the client. Once `return_notice_sent` is typed, an item still
unsold after `pickup_days` is flagged for the abandonment election.

**Settlements.** `settle` gathers what is owed through `--through`: CLEARED
rows not yet paid, unpaid buyouts above 0.00, and any adjustment to a row
already paid. It adds one row to `inventory/_consign/K01/settlements.csv`
(`settlement_id` like `K01-S001`) and writes the statement to
`reports/consign/K01/K01-S001.csv`, item by item, with no buyer data. If the
total due is under the client's `min_payout`, the sales and adjustments
carry forward; a buyout is still paid in that run (agreement §7.2), and with
no buyout nothing is written. When the agreement ends, `settle K01 --through <date>
--final` pays the full balance, a negative one included. A
statement is written once and never overwritten. `settle` does not record a
payment. When Russell pays, the accountant sets `paid_date` and `amount_paid`
on that settlements row, and the next `sync --apply` copies the date and
`settlement_id` onto the rows. The report's paid-this-year total comes from
`settlements.csv`.

Never copy a file over `client_ledger.csv`. Bring hand-built rows in with
`python -m lib.cli consign import <intake> <file.csv> --client <code>`; import
merges into the current format and keeps lots, splits and paid state.

There is no `set` command: edit the CSV, and `sync` refuses an unknown state,
a DISPOSED row without a note, an EXCLUDED row without a buyout price, a bad
amount or date, or two active rows on one `inventory_folder` unless they share
a `lot_id` (each sellable unit needs its own folder). It also refuses a
lot-mate that already has an order, a share, a settlement or a paid date
(make the sold row the lot's lowest id, or take it out of the lot), and one
`order_id` on two active rows. Across ledgers it also
refuses a folder claimed from two ledgers (unless it is one client's lot), a
lot on two folders, and one id in two of a client's ledgers. A listing that
ended (the row goes back to its on-hand state, `DRAFTED`, `SHOT` or `PREP`,
flagged `listing <id> ended <date>: relist, return or dispose`), a sale that
left the sales ledger (the finance sync drops refunded and cancelled orders)
and an auction-format listing (consigned items stay fixed-price for now) are
flagged in `state_note`, not given a state. Net
is eBay's net before postage minus ad fee, postage and other costs; a blank ad
fee or postage is unknown, not zero, so the row can't clear. Once
`paid_to_client_date` is set, the row's `client_share` is frozen at what was
paid; a later change is flagged with the adjustment, and the next `settle`
carries it.

An older ledger loads as it is. The first `sync --apply` adds the new columns
(`client_code`, `intake`, `lot_id`, `split_locked`, `delivered_at`,
`handling`) and
rewrites bare `C001` ids as `K01-C001`; nothing has to be converted by hand.

Paperwork for a new client: [docs/client-onboarding/checklist.md](docs/client-onboarding/checklist.md).

## Background dispatch (optional, #59)

PRICE, IDENTIFY's non-photo research (maker/pattern lookups, forum
cross-reference), DRAFT, and REVIEW's card-build touch no local state —
each reads only the prior phase's file already written to the shoot dir.
On a batch of shoots, dispatch these four as background agents (`Agent`
tool, `run_in_background: true`) instead of blocking the foreground
conversation, e.g. running item 2's PRICE/DRAFT while the operator is
still in item 1's PREP. PREP and the REVIEW gate stay foreground: PREP
reads/writes photo files and needs the operator looking at their own
images; REVIEW is the human approval that authorizes publish (see the
gate contract above — untouched by dispatch).

If a dispatched stage turns out to need local state mid-run (e.g. IDENTIFY
needs another photo angle), it surfaces the question back to the operator
instead of failing — same as a foreground run would. Same prompts, same
`_shared.md` rules, same gates; dispatch only changes where a stage runs.

## Sample photos — when there is no shoot to point at (#97)

`inventory/` is gitignored, and so is every `*.jpg` in the tree. A **cloud
agent, a fresh clone or a CI run therefore has no photos at all** — which used
to mean no way to run or verify anything in `lib/photo_prep/` without the
operator hand-feeding a shoot directory.

`tests/fixtures/photos/` is the tracked carve-out: ten frames of one item
(a 14k ring, ~1.3 MB for the whole set), chosen so eight crop cleanly and two
trip a `center_crop` guard. **Point photo tools there by default when no shoot
was named.**

    python -m lib.photo_prep.center_crop tests/fixtures/photos --check
    python tests/test_sample_photos.py

Read [`tests/fixtures/photos/README.md`](tests/fixtures/photos/README.md)
first — it says what each frame is for and what a correct result looks like,
so a verdict can be checked without asking the operator. Two rules: never run
`--apply` against the directory (use `--out` into a temp dir), and if a frame
is ever added or replaced, update the README and `tests/test_sample_photos.py`
in the same commit — the test asserts the documented numbers.

## Locking a format

Every artefact passed between phases carries a version stamp — `template_version`
on the draft, `MANIFEST_VERSION` on `.prep/prep.json`, a fixed column order on
`listings_ledger.csv`, a fixed section list on the REVIEW card. A stamp on its
own does nothing; `tests/test_formats.py` is what makes it mean something. It
holds the exact field set of each format and fails the moment one changes.

**To change a format:**

1. make the change;
2. run `python tests/test_formats.py` — it will fail, and that is the point;
3. decide which kind of change it is:
   - **additive and safe** → add the field to the lock in the same commit, so
     the next reader can see when it appeared;
   - **breaking for readers** → bump the version stamp, teach the readers both
     shapes, then update the lock.

Never relax an assertion so it stops noticing. That converts a format change
from a decision into an accident.

The lock also carries two debts, as counts that may only go DOWN: 31 drafts
stamp `v1` with no `_field_constraints` block at all, and 21 carry a partial
one. Those are under-enforced — the validator checks fewer fields than it should
— but none of them disagrees with the template, and a rule that DISAGREES fails
outright with no tolerance.

| Phase | Prompt | Reads | Writes |
|---|---|---|---|
| IDENTIFY | [prompts/identify.md](prompts/identify.md) | photos | `identify.txt` |
| PREP | [prompts/prep.md](prompts/prep.md) | photos (+`identify.txt`) | `listing/` + `.prep/prep.json` (HARD gate: orientation → crop → colour, each approved) |
| PRICE | [prompts/price.md](prompts/price.md) | `identify.txt` | `price.txt` |
| CURATE | [prompts/curate.md](prompts/curate.md) | `identify.txt`+`price.txt`+profile | `review.md` |
| INVESTIGATE | [prompts/investigate.md](prompts/investigate.md) | photos (+`identify.txt`) | `investigate.txt` |
| DRAFT | [prompts/draft.md](prompts/draft.md) | `identify.txt`+`investigate.txt`+`price.txt`+template | `draft.md` + `--record` → SKU stamped + ledger row (DRAFTED) |
| REVIEW | [prompts/review.md](prompts/review.md) | `draft.md`+`price.txt`+`NEEDS_REVIEW.md` | `--review` → `review_card.md` (records+preflights) + `review_card.html` (the page the decision is made on) → (on approval) LIVE listing |
| REPORT | [prompts/report.md](prompts/report.md) | `sales_ledger.csv`+`listings_ledger.csv`+drafts | printed numbers (+ `docs/performance-<date>.md`) |

**REPORT closes the loop.** PRICE decides what to ask; REPORT measures what we
actually got, and feeds the gap back. Its two commands:

    python lib/sync_actuals.py --apply     # refresh actuals from the Fulfillment API
    python lib/report.py --performance     # fees, ask-vs-actual, speed, categories

`sync_actuals` exists because two local records are structurally incomplete:
the listings ledger only knows the ASK (an accepted Best Offer never writes
back), and the Inventory API is blind to anything listed by hand on eBay.com.
Orders are the only source that sees every sale and the price actually paid.
Refunded and cancelled orders are left out of `sales_ledger*.csv` and written
to `unwound_orders*.csv` instead (`reason` refunded or cancelled). An order is
never in both. For 1099-K reconciliation, the 1099-K does not subtract
refunds, and sales_ledger `gross` is already net of partial refunds, so add the
refunds back: Σ sales_ledger (`gross` + `refunded`) + Σ unwound_orders
`original_gross` where `reason` is refunded. Leave cancelled orders out.

**The publish step (reached via the REVIEW gate, on explicit approval):**

| Path | How | Reads | Effect |
|---|---|---|---|
| LIST/EDIT (**primary**) | `python lib/list_edit.py --list <shoot-dir> --confirm` | `draft.md` | sync + **publish LIVE** via Sell API |
| Sync-only (no publish) | `python lib/list_edit.py --sync <shoot-dir>` | `draft.md` | eBay UNPUBLISHED offer only |
| LIST/EDIT (fallback) | [prompts/list_edit_chrome.md](prompts/list_edit_chrome.md) | `draft.md`+`price.txt` | eBay **DRAFT** via Chrome UI |

REVIEW (Function 5.5) is the gate that authorizes Function 6. The publish
command (`--list … --confirm`) runs ONLY after the user explicitly approves
the review card, one item at a time (batch only on "approve all"). The
firewall still holds against *automatic* publishing: the pipeline never
publishes, `--sync` only ever creates an UNPUBLISHED offer, and every
publish path keeps the `--confirm` code guard — the human's approval at the
gate is what authorizes passing it. A dry run is available any time
(`--list`/`--publish` without `--confirm`).

- **Primary — Sell API (`lib/list_edit.py`).** Headless, full-res photo
  upload (EPS), one-payload description (no missing-fields bug), idempotent
  re-sync. Verified end-to-end on sandbox. One-time setup:
  [lib/SETUP_EBAY_API.md](lib/SETUP_EBAY_API.md). Use this by
  default. `--validate <dir>` needs no credentials.
- **Fallback — Chrome stand-in.** Only when the API isn't set up. Subject to
  read-tier/sandbox limits and the debounce/trusted-input pitfalls
  documented in its prompt.

**Managing live listings (Function 6, on user request).** Beyond
publishing, `lib/list_edit.py` queries and manages any offer/SKU on the
account. Like publish, every mutation is **dry-run unless `--confirm`** and
runs ONLY when the user asks (never as part of the automated pipeline):

| Op | Command | Effect |
|---|---|---|
| Query | `--offers` | list every offer (status, offerId, listingId, price, sku) — read-only |
| Withdraw | `--withdraw-offer <id> --confirm` | end a LIVE listing; offer kept (UNPUBLISHED), re-publishable |
| Delete offer | `--delete-offer <id> --confirm` | delete an offer (permanent; ends listing if live; SKU kept) |
| Delete item | `--delete-item <sku> --confirm` | delete the inventory item + ALL its offers (permanent) |

Withdraw/delete are destructive and **HARD-gated like publish**: confirm the
exact target with the user (run the dry run first, show what it hits), and
only pass `--confirm` on an explicit yes. Use `--offers` to find IDs.

Cross-cutting depth rules:
- Condition analysis in IDENTIFY and INVESTIGATE uses
  [prompts/condition-rubric.md](prompts/condition-rubric.md).
- IDENTIFY loads a category **specialization** (expert field guide) when an
  item matches one — registry + modules in
  [specializations/README.md](specializations/README.md) (e.g. marbles).
- DRAFT and PREP can load an optional **style guide** — a studied seller's
  presentation technique (title slots, body skeleton, photo conventions) —
  registry + modules in [styleguides/README.md](styleguides/README.md). **Off
  by default**; a run turns one on by name. It is a stylistic overlay: house
  rules (honesty bar, wear phrasing, PII, stage contract) always win.
- PRICE runs the autonomous exact-match hunt (Stage A WebSearch → Stage B
  eBay sold via the logged-in browser → optional Stage C Chrome when
  confidence is low) before any era-peer fallback; see its prompt.

Shared rules (style, confidence, firewall, unit_type, char limits,
persistence) live in [prompts/_shared.md](prompts/_shared.md).

Python infrastructure (config, eBay client, logged-in-browser comp
fetch, photo prep) is shared from `lib/` — no phase duplicates it.

---

## Headless run sequence (full mode)

1. Resolve shoot dir + mode (state inferred mode in one line).
2. IDENTIFY → write `identify.txt`. Log any grouping questions to
   NEEDS_REVIEW; do not stop.
2b. PREP → `--auto` FIRST (one pass, no questions: every frame turned and every
   crop planned, conservatively — margin left around the item — and nothing
   approved). Show the widget of that best attempt, then one card
   (`python tools/prep_card.py <shoot>`) of the revised frames, and ask once:
   approve (`--approve-auto`, orientation + crop together) or override (open the
   staged flow below). Name every frame whose orientation was GUESSED.
   Override / anything the auto pass got wrong → `--check` (ORIENTATION ONLY — the crop and the colour reading are not
   measured until orientation is approved, so no crop box describes a rotation
   that could still change), then `python tools/prep_sheet_html.py <shoot>` and publish
   `.prep/review.html` as an artifact — that page IS the review surface for all
   three stages, not a JPEG and not a prose description. Card per frame, every
   option side by side, an override and a free-text box on every card, and an
   Accept button per stage. Then walk the three staged reviews in
   order, STOPPING for the user at each one (HARD gate). Never batch them into
   one question:
   `--stage orientation` (fix with `--set-rotate NAME=DEG`, the idempotent form) → `--approve-stage
   orientation` → `--stage crop` (`--crop NAME=off|on|padF`) → `--approve-stage
   crop` → `--apply` → `--stage color` (`--pick studio|punch`) →
   `--approve-stage color` → `--approve`. Photos land in `listing/`;
   INVESTIGATE still reads the originals.
3. PRICE each saleable item → run the exact-match hunt (Stage A WebSearch
   → Stage B eBay sold via the logged-in browser → Stage C Chrome only if
   confidence is low); adopt Recommended tier as provisional working
   price; write `price.txt`. Never stops.
4. CURATE (plan mode) → write `review.md`.
5. INVESTIGATE (list mode), per item → commit to the confident
   assessment; log open questions; write `investigate.txt`.
6. DRAFT (list mode) → point `photos:` at the prepped files
   (`prep --repoint-draft --apply-repoint`), render template, run the
   pre-write validation pass, write `draft.md`, then `python lib/list_edit.py --record <shoot-dir>`
   to stamp the item's SKU into the draft and create its lifecycle ledger
   record (status DRAFTED). Do this for EVERY draft, and again after any edit.
7. REVIEW (list mode) → `python lib/list_edit.py --review <shoot-dir>` — one
   command that records (if needed) + preflights + builds `review_card.md` —
   then `python tools/review_card_html.py <shoot-dir>` and deliver the
   `review:` link it prints (the local app's `/review/<shoot>`; start it with
   `python -m lib.cli serve`). That page IS the review surface: the listing as a buyer
   meets it, the hero picker, and every ⚠ line — not the text card alone and
   never a prose summary. Present it with the card and STOP (HARD gate).
   Surface title + price + the ⚠ count.
8. On explicit approval at the card → `python lib/list_edit.py --list
   <shoot-dir> --confirm` (sync + publish LIVE); report the listing URL.
   On a change request → re-run the owning phase, re-render `draft.md`,
   re-run `--review`. Anything other than explicit approval = no publish.
