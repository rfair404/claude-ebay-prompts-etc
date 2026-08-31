# Sample photos — the shoot an agent can always reach

Ten frames of one item, tracked in the repo on purpose. `/inventory/` is
gitignored and `*.jpg` is excluded repo-wide, so a cloud agent, a fresh clone
or a CI run has **no photos at all** unless something like this is committed.
`!tests/fixtures/**` in `.gitignore` is the carve-out that lets these through.

Use this directory as the default thing to point a photo tool at:

```
python -m lib.photo_prep.center_crop tests/fixtures/photos --check
```

**Do not** point `--apply` at it. Anything that rewrites frames in place must be
given `--out` into a temp directory; `tests/test_sample_photos.py` fails if a
byte here changes.

## Where they came from

One real shoot: a 14k gold ring on dark felt and a black jeweller's glove,
Nikon D5100, nine frames, shot by us. Downscaled to a 1200 px long edge and
re-encoded to sit under 150 KB each (~1.3 MB for the set) — these exist to
drive code paths, not to be looked at. The Nikon MakerNote was dropped; only
Make, Model, DateTime and Orientation survive. No GPS, no people, no paper, no
label, nothing that names a buyer, a consignor or a live listing.

`ring-10` is the one derived frame: `ring-01`'s pixels stored rotated 90° CCW
with **EXIF Orientation = 6**. The shoot itself has none — all nine originals
are Orientation 1 — and the orientation path needs a frame that carries a real
tag, so one was made rather than faked with a synthetic image.

## The frames

`subj%` = subject box as a fraction of the frame, `off%` = how far the subject
sits from centre, `border%` = how much of the frame border reads as subject.
Verdict is `python -m lib.photo_prep.center_crop … --check` at the default
`--aspect 1:1 --pad 0.12`. All ten numbers are asserted in
`tests/test_sample_photos.py`.

| Frame | subj% | off% | border% | Verdict | What it exercises |
|---|---:|---:|---:|---|---|
| `ring-01-full-plain-ground.jpg` | 9.6 | 2.1 | 0.0 | crop, centered | The happy path: whole item, clean ground, safe crop. |
| `ring-02-macro-bezel.jpg` | 3.7 | 3.7 | 0.0 | crop, centered | Small subject in a dark, cluttered macro — detector must still find it. |
| `ring-03-macro-bezel-tight.jpg` | 9.8 | 7.2 | 0.0 | crop, OFF-CENTER | Same subject nearer; crosses the 6% off-centre flag. |
| `ring-04-on-finger-small.jpg` | 17.0 | 7.5 | 29.8 | **SKIP** — detection unreliable (87% outside the box) | The guard that matters: a gloved hand runs off every edge, so there is no clean background to crop against. Must pass the original through. |
| `ring-05-on-finger.jpg` | 46.1 | 6.9 | 21.8 | **SKIP** — detection unreliable (53% outside the box) | Second failure mode: subject nearly half the frame, detection can't be trusted. |
| `ring-06-macro-shank-offcentre.jpg` | 13.5 | 16.7 | 0.0 | crop, OFF-CENTER | The most off-centre frame in the set — the case centring is *for*. |
| `ring-07-macro-shank-near-dup.jpg` | 13.9 | 13.8 | 0.0 | crop, OFF-CENTER | Near-duplicate of `ring-06` (16×16 ahash distance 47/256, the closest pair here; next closest is 74). For near-duplicate hold-back. |
| `ring-08-profile-cool-cast.jpg` | 7.0 | 10.3 | 0.0 | crop, OFF-CENTER | Blue cast (mean B−R **+18.6**, against +1.5 on `ring-01`) — colour/tone work. |
| `ring-09-full-cool-cast.jpg` | 4.5 | 7.2 | 0.0 | crop, OFF-CENTER | The strongest cast in the set (B−R **+23.3**) on a full view. |
| `ring-10-exif-rotated.jpg` | 9.6 | 2.1 | 0.0 | crop, centered | `ring-01` stored sideways with EXIF Orientation 6. Same scene, same numbers — so an orientation bug shows up as a *difference* from `ring-01`, not as a judgement call. |

Eight of ten crop, two skip. That split is the point: a set where everything
passes proves nothing about the guards.

## What one shoot cannot cover

Two rows of the [#97](https://github.com/rfair404/claude-ebay-prompts-etc/issues/97)
proposal are **not** here, and would need a different shoot rather than another
crop of this one:

- **skewed printed matter** — a magazine or catalogue cover for `unskew` and
  `--category printed` / `subject=paper`;
- **a multi-item overview frame** — 2–4 small objects for
  `marble_triage --crops-only --expect N`.

Add them as `paper-*` and `lot-*` here when a suitable shoot exists; the naming
and the size budget above should hold.
