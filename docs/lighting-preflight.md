# Lighting pre-flight (the gaffer card)

Run this before every show and every photo shoot, after the start commands
(OBS up, the D5 feed running, the TG-7 on its Wi-Fi). It takes about two
minutes. Russell does the hands-on part, then one test frame per camera is
**graded by an agent against fixed numbers**, so nobody has to eyeball it.
Any failed gate comes back as a single physical fix.

Three pictures are checked:

| Picture | Camera | Test frame |
|---|---|---|
| Russell's face | face webcam, OBS source `Webcam` | OBS source screenshot |
| The item, live | Nikon D5100 + 40mm macro, OBS source `Nikon D5100` | OBS source screenshot (no shutter used) |
| The item, full-size still | OM TG-7 over Wi-Fi | one `tg7cam shoot` still |

The light that falls on the podium is shared by the show and by listing
photos (`.claude/skills/photo-shoot/SKILL.md`), so one pre-flight covers
both.

## 1. The operator card

This is the card the operator agent hands Russell, as it stands.

```
OPERATOR CARD - lighting pre-flight
Do:
1. Close the blinds and switch off every room light that isn't a work lamp.
2. Aim the podium lamp at the podium from one side and above, not from behind the D5.
3. Lay a plain white sheet of paper flat on the podium, filling about a third of the D5 picture.
4. Point the TG-7 at the sheet from its usual spot.
5. Sit where you sit for the show, facing the webcam.
6. Paste the block below. When it prints HOLD THE SHEET, hold the sheet beside your face until it prints the folder.
Run:
cd /home/russ/EBAYBIZ && d=inventory/_live/preflight/$(date +%Y%m%d-%H%M) && mkdir -p "$d" \
 && python tools/live/obsctl.py shot "Nikon D5100" -o "$d/d5.jpg" --width 960 \
 && python tools/live/tg7cam shoot --to "$d" \
 && python tools/live/obsctl.py shot "Webcam" -o "$d/face.jpg" --width 960 \
 && echo "HOLD THE SHEET beside your face" && sleep 5 \
 && python tools/live/obsctl.py shot "Webcam" -o "$d/face-white.jpg" --width 960 \
 && echo "$d"
```

Claude grades the folder (section 3). If everything passes, the answer is
"Lights pass." If not, Russell gets one more card, built from the fix table
in section 4, with one line per failed gate in the order he'd do them at the
desk. Once a fix is done, the block is run again.

## 2. Where the lights go

Direction for Russell's hands. The angles are a starting point, not a law:
the gates in section 3 decide.

1. **One light color on the set.** Every lamp lighting the podium or Russell's
   face should be the same kind (all daylight-colored or all warm). Daylight
   from a window and a warm lamp together give two whites, and no white
   balance setting fixes both at once. At night the window doesn't matter;
   by day, close the blinds or let the window be the only light.
2. **Podium lamp: to one side and above the item.** About 45 degrees to the
   side and above is the usual starting point for product light (a
   convention). Keep it out from behind the D5: a lamp next to the lens
   lights the item flat, and on anything glossy it bounces straight back
   into the lens.
3. **The glare test, by eye.** A shiny surface reflects light at the same
   angle it came in. Put your eye next to the D5's lens: if you can see the
   lamp reflected in the item, the camera sees glare too. Move the lamp
   sideways until the reflection leaves the item.
4. **Feather, don't blast.** If the lamp is close and the item has a hot spot,
   aim the center of the beam past the item, so the item sits in the softer
   edge of the beam. Or point the lamp at a white wall or a white board and
   let the bounce light the item. **Never lay paper or cloth on a lamp**:
   bounce it, don't cover it.
5. **Face light: in front and to one side.** The light should be on Russell's
   face, not on the brick wall behind him. A little falloff toward the far
   side of the face is fine and looks natural. A white board held on the dark
   side lifts it (a fill) without another lamp.
6. **TG-7 lens LED** (`tg7cam light on|off`): turn it on for matte close-ups
   (stamps, marks, labels) when the ISO gate fails at close range. Leave it
   off for glossy items, since it sits right by the lens and gives exactly
   the glare in step 3.

### Set by the item

| Item | What changes |
|---|---|
| **Glossy** (magazines, photos, coated cards, glass, chrome) | Lamp further to the side (step 3). If a hot spot stays, bounce the lamp instead (step 4). This morning's magazine is the example: see the calibration below. |
| **Dark item on the black cloth** | The edge disappears into the cloth. Raise the lamp and move it a little behind the item, so a thin line of light outlines the top and edges. For listing frames, the light backdrop is the other choice. Check: in the look copy, the item's whole outline is visible against the cloth. |
| **White-background listing shot** | The backdrop must stay the brightest thing in the frame, brighter than the item's own highlights (the rule in `docs/prep-white-backgrounds.md`). Light the backdrop evenly, keep the lamp off-axis so glossy whites don't reach backdrop brightness, and keep the soft contact shadow under the item: PREP needs it so the item doesn't look pasted on. PREP lifts the white later, so capture leaves headroom (gate G5). |

## 3. The gates

An agent grades the folder the card's block writes. All brightness values are
8-bit luma (0 to 255, `0.299R + 0.587G + 0.114B`) on the frame scaled to 960
px on its long side. "Clipped" means luma 250 or more.

| # | Picture | Gate | Pass |
|---|---|---|---|
| G1 | TG-7 still | shutter (EXIF `ExposureTime`) | 1/30 s or faster |
| G2 | TG-7 still | ISO (EXIF `ISOSpeedRatings`) | 200 or lower |
| G3 | TG-7 still | focus | `tg7cam shoot` printed `focus ok` |
| G4 | D5, TG-7 | glare: split the frame into a 4x6 grid (6x4 for landscape) and take the cell with the most clipped pixels | 5% or less clipped in that cell |
| G5 | D5, TG-7 | white exposure: the frame's 98th-percentile luma (the white sheet) | 200 to 245 |
| G6 | D5, TG-7, webcam (`face-white.jpg`) | white balance of the sheet: mean R/B and G/((R+B)/2) of the pixels between the 95th-percentile luma and clipping | R/B 0.95 to 1.08, G 0.96 to 1.04, and the three cameras' R/B within 0.06 of each other |
| G7 | webcam (`face.jpg`) | face exposure, in a box from forehead to chin and ear to ear | mean luma 110 to 200, 3% or less clipped |
| G8 | webcam (`face.jpg`) | face against the room: face mean minus the whole frame's median luma | +30 or more |
| A1 | webcam (`face.jpg`) | advisory, never a fail: bright half of the face over the dark half | 2.0 or less |

### Why these numbers

- **G1 and G2, the TG-7.** The TG-7's 5.4 mm lens is a 30 mm equivalent (EXIF
  `FocalLengthIn35mmFilm` 30). The handheld rule of thumb, "shutter at least
  1 / the 35 mm-equivalent focal length", is a photographers' convention, not
  a measured limit for this camera. It puts the floor at 1/30 s, and a TG-7
  on a mount with a still item is safe there. In P mode this morning the TG-7
  held 1/30 s and only moved ISO: 400 before Russell added light, 200 after.
  ISO is the setting that actually answers to light, so it carries the gate.
  The cost of ISO 400 was measured at 100% on the same patch of handwriting:
  edge sharpness (variance of the Laplacian) went from 1161 at ISO 400 to
  1714 at ISO 200, which is the "slightly soft" look in the first frame. The
  camera exposes no shutter setting over Wi-Fi (`tg7cam props` offers
  `takemode` iAuto/P/A/Scene2/supermacro/movie, `focalvalue` 2.3/3.2/9.0,
  `expcomp`, `isospeedvalue` Auto to 12800, `wbvalue`), so a faster
  shutter for handheld use needs a test (Handoffs).
- **G4, glare.** Dense clipping in one small part of the frame is a hot
  spot: a reflection that hides the surface under it. Condition beats a
  pretty frame, so this is a fail even when the shot looks bright and
  lively. The grid finds a local hot spot that a whole-frame average would
  dilute. When a cell fails, the agent looks at that cell before reporting
  it. A genuinely white object can clip without glare, and that is an
  override the agent states in the result.
- **G5, white exposure.** Below 200 the sheet reads grey: not enough light. Above
  245 there's no texture left in the whites and no headroom for PREP's white
  lift. D5 live-view exposure is automatic (setup doc section 6: ISO and
  shutter only change stills), so on the D5 this gate catches contrast, not
  lamp power: a bright sheet on black cloth fools the camera into lifting
  everything.
- **G6, white balance.** A white sheet should read white. The window is
  lopsided on purpose: a touch warm (up to 1.08) suits the vintage toy-shop
  register, cool never does. Cameras more than 0.06 apart means a viewer sees
  the item change color when the page changes, which is the mixed-light
  symptom.
- **G7 and G8, the face.** A clipped forehead or cheek reads as a white patch
  on a phone screen. Russell's face should be the brightest thing in the strip,
  not the wall. A1 is the usual portrait guide for a gentle falloff (a
  convention, so it's advisory and never a fail).

These are **starting values**, calibrated on four frames from one morning.
Retune them after three shows, from the logged results and the photo-editor's
keeper and reshoot calls.

### Calibration: 2026-10-22, about 06:00

A magazine spread on black cloth, before and after Russell added light at
06:02 (`inventory/_live/focus-check/`, plus the OBS renders of the item page
from the same minutes).

| Gate | Before | After |
|---|---|---|
| G1 TG-7 shutter | 1/30 s pass | 1/30 s pass |
| G2 TG-7 ISO | 400 **fail** | 200 pass |
| G4 glare, D5 | 10.1% **fail** (bottom right, glossy photo prints) | 6.0% **fail** (top right of the page: the new light is too direct at macro distance) |
| G4 glare, TG-7 | 11.2% **fail** | 2.6% pass |
| G5 white, D5 | 247 **fail** | 249 **fail** |
| G5 white, TG-7 | 236 pass | 241 pass |
| G6 R/B, D5 / TG-7 | 0.98 / 1.04, 0.060 apart: **fail**, by a hair | 1.01 / 1.03, 0.02 apart: pass |
| G7 face | 182, 19.4% clipped **fail** | 202, 19.5% clipped **fail** |
| G8 face vs room | +110 pass | +111 pass |
| A1 face sides | 1.59 | 1.27 |

These are what the grading recipe below prints for those frames (the
webcam half is cut from the OBS renders' strip; `face-white.jpg` didn't exist
yet, so there's no webcam white-balance reading). What it says: added light fixed
the TG-7, but it left a hot spot on the D5 and the face is too hot on its bright side. The fixes
are to feather the podium lamp (G4, G5) and to pull the face light back (G7).

### Grading recipe (until the `preflight` command exists)

Run from the main checkout. `FACE` is the face box as fractions of
`face.jpg` (left, top, right, bottom); the agent reads the picture and fills
it in. The face box needs eyes, which is why it isn't automatic yet.

```bash
cd /home/russ/EBAYBIZ && DIR=inventory/_live/preflight/<folder> FACE=0.40,0.15,0.60,0.60 python - <<'EOF'
import glob, os
import numpy as np
from PIL import Image
d = os.environ["DIR"]
def load(p):
    im = Image.open(p); ex = im.getexif().get_ifd(0x8769)
    im = im.convert("RGB"); im.thumbnail((960, 960))
    return np.asarray(im).astype(float), ex
lum = lambda a: a @ [0.299, 0.587, 0.114]
def glare(a):
    L = lum(a); h, w = L.shape; r, c = (6, 4) if h >= w else (4, 6)
    return max((L[i*h//r:(i+1)*h//r, j*w//c:(j+1)*w//c] >= 250).mean() * 100 for i in range(r) for j in range(c))
def white(a):
    L = lum(a); m = (L >= np.percentile(L, 95)) & (L < 250)
    R, G, B = (a[..., k][m].mean() for k in range(3))
    return np.percentile(L, 98), R / B, G / ((R + B) / 2)
ok = lambda c: "pass" if c else "FAIL"
rb = {}
tg = sorted(glob.glob(f"{d}/TG7_*.jpg"))
for name, p in (("d5", f"{d}/d5.jpg"), ("tg7", tg[-1] if tg else "")):
    if not os.path.exists(p): print(name, "missing"); continue
    a, ex = load(p); g = glare(a); p98, r, gg = white(a); rb[name] = r
    if name == "tg7":
        t, iso = ex[0x829A], ex[0x8827]
        print(f"G1 tg7 shutter 1/{1/t:.0f} {ok(t <= 1/30 + 1e-4)}  G2 ISO {iso} {ok(iso <= 200)}")
    print(f"G4 {name} glare {g:.1f}% {ok(g <= 5)}  G5 white {p98:.0f} {ok(200 <= p98 <= 245)}  "
          f"G6 R/B {r:.2f} G {gg:.2f} {ok(0.95 <= r <= 1.08 and 0.96 <= gg <= 1.04)}")
if os.path.exists(f"{d}/face-white.jpg"):
    _, r, gg = white(load(f"{d}/face-white.jpg")[0]); rb["webcam"] = r
    print(f"G6 webcam R/B {r:.2f} G {gg:.2f} {ok(0.95 <= r <= 1.08 and 0.96 <= gg <= 1.04)}")
if len(rb) > 1:
    s = max(rb.values()) - min(rb.values()); print(f"G6 cameras apart {s:.2f} {ok(s <= 0.06)}")
a, _ = load(f"{d}/face.jpg"); L = lum(a); h, w = L.shape
x0, y0, x1, y1 = (float(v) for v in os.environ["FACE"].split(","))
F = L[int(y0*h):int(y1*h), int(x0*w):int(x1*w)]; m, clip = F.mean(), (F >= 250).mean() * 100
half = F.shape[1] // 2; lo, hi = sorted((F[:, :half].mean(), F[:, half:].mean()))
print(f"G7 face {m:.0f}, {clip:.1f}% clipped {ok(110 <= m <= 200 and clip <= 3)}  "
      f"G8 vs room {m - np.median(L):+.0f} {ok(m - np.median(L) >= 30)}  A1 sides {hi/lo:.2f}")
EOF
```

`G3` comes from the block's own output (`focus ok`). After grading, the
agent also reads the D5 and TG-7 shots itself. A number never signs off a
picture nobody has looked at.

## 4. When a gate fails: one fix each

The agent turns each failed gate into one line on Russell's fix card,
worded as below.

| Gate | Fix (Russell's hands) |
|---|---|
| G1 TG-7 slower than 1/30 s | Move the podium lamp closer to the item. |
| G2 TG-7 ISO over 200 | Move the podium lamp closer to the item. For a matte close-up, the agent can also turn on the TG-7 LED (`tg7cam light on`), with no hands needed. |
| G3 TG-7 focus didn't lock | Move the TG-7 a little further from the item and center it. |
| G4 glare | Slide the podium lamp sideways until you can't see it reflected in the item from the D5's lens. |
| G5 white over 245 | Aim the podium lamp past the item, so the item sits in the edge of the beam. |
| G5 white under 200 | Move the podium lamp closer to the item. |
| G6 white balance off, or cameras disagree | Switch off every light that isn't the podium lamp or the face light, and close the blinds. (If the lights are already all one kind, the agent sets the D5 preset with `show_flow.py wb`, with no hands needed.) |
| G7 face clipped, or over 200 | Pull the face light back, or turn it so the beam passes just in front of your face. |
| G7 face under 110 | Move the face light closer, or turn it on. |
| G8 face not brighter than the room | Turn the face light off the wall and onto your face. |
| A1 sides over 2.0 (only if you want it even) | Hold a white board on the dark side of your face, just out of frame. |

## 5. The look

What the light should do for the shop, on stream and in listings alike.

- **One white everywhere.** The same white sheet reads the same on the face
  cam, the D5 and the TG-7, warm-neutral, never blue. A buyer who watched the
  lot live should see the same color in its listing photos.
- **Soft light from one side, with a real shadow.** The item should look like
  an object sitting on a table, not a cutout: one main light, a soft falloff
  and a contact shadow. Flat on-axis light and hard hot spots are both off-brand.
- **Condition first.** Light shows the surface: scratches, wear, print. If a
  reflection hides part of the item, the frame fails, however lively it looks.
  Direction changes the light, never what the item looks like.
- **No colored light on the goods.** No RGB or gel color on the item or Russell's face.
  Color on stream comes from the overlays and print palette
  (`brand/pops-games/README.md`), not from the lamps.
- **Russell's face is the brightest thing in the strip.** It should be lit, natural and
  warm-neutral, with a gentle falloff on one side. A clipped forehead is
  a fail.

## Gear

**TODO (gear list):** the lamps Russell uses on the set aren't recorded
anywhere in the repo or the team's notes. That includes which light he added
at 06:02 on 2026-10-22, its color (daylight or warm) and whether it dims. The
one light on record is the TG-7's lens LED (`tg7cam light`). The live-show
cart design (2026-10-14) chose no LED lights on the cart. Once the list
exists, this doc names each lamp's place (podium, face) instead of "the
podium lamp".

| Light | Used for | Color | Dims | Source |
|---|---|---|---|---|
| TG-7 lens LED | matte close-ups | TODO | no control in `tg7cam` beyond on/off | `tools/live/tg7cam` |
| TODO: podium lamp(s) | podium | TODO | TODO | TODO |
| TODO: face light | face | TODO | TODO | TODO |
