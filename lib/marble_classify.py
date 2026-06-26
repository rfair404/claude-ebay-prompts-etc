#!/usr/bin/env python3
"""Calibrated hierarchical marble classifier over the CLIP indexes.

A real classifier on top of the labeled MCSA reference embeddings — not raw
nearest-neighbour. Three heads are ensembled:

  1. prototype  — per-label centroid of the MCSA CLIP embeddings (visual)
  2. text       — CLIP zero-shot: per-label natural-language prompts (semantic;
                  adds the named-type vocabulary the maker-centroids lack)
  3. knn        — distance-weighted vote of the nearest MCSA reference photos

Each head -> a probability vector (softmax over cosine); the ensemble is a
weighted blend. Then TWO things make it trustworthy instead of overconfident:

  * Hierarchy — labels roll up to a FAMILY (handmade / transitional / machine /
    non-glass). The family call is far more separable than fine maker, so it's
    reported with its own (high) confidence even when the maker is uncertain.
  * Abstention — if the top label's probability or its margin over #2 is low,
    the maker call is withheld ("candidates + needs photo") rather than guessed.

Honesty: this narrows and ranks; it does NOT authenticate. method != origin !=
era still holds (specializations/marbles.md). Use it to triage fast, then judge
the named/value-moving calls on the real photo.

  classify  IMG [IMG ...]   full report for one marble (multiple angles ok)
  eval                       leave-one-out accuracy on the 315 MCSA labels
  families                   print the label->family / value-tier map

Speed: prototypes + text vectors are tiny and built once; a query is one CLIP
image embed (~50-100 ms CPU) + a few matmuls. Warm the process for batch ID.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vindex import VIndex, embed_images, embed_texts, download_pil  # noqa: E402

MCSA = VIndex("marblecollecting")

# --- label hierarchy: MCSA page label -> family ------------------------------
FAMILY = {
    # handmade (antique/contemporary cane-cut + single-gather art)
    "Lutz": "handmade", "End of Day": "handmade", "Core Swirl": "handmade",
    "Swirl": "handmade", "Clambroth/Indian": "handmade", "Sulphide": "handmade",
    "Handmade": "handmade", "Handmade (other)": "handmade",
    # transitional (single pontil + machine character)
    "Transitional": "transitional", "M.F. Christensen": "transitional",
    # machine-made (American golden-age + WV swirl + modern import)
    "Akro Agate": "machine", "Peltier": "machine", "Vitro Agate": "machine",
    "Marble King": "machine", "Master Marble": "machine", "Alley Agate": "machine",
    "Heaton/Bogard/JABO": "machine", "Christensen Agate": "machine",
    "Other U.S.": "machine", "Foreign": "machine", "Machine-made": "machine",
    # non-glass (clay / stone / mineral / steel)
    "Earthenware": "nonglass", "Stoneware": "nonglass", "China": "nonglass",
    "Agate And Other Mineral": "nonglass", "Other Non Glass Materials": "nonglass",
    "Non Glass Marbles": "nonglass",
}

# coarse value posture per label: (tier, pull?) — pull = inspect for upside
TIER = {
    "Christensen Agate": ("$$$$ (Guineas trophy)", True),
    "Lutz": ("$$$$", True), "Sulphide": ("$$$", True), "End of Day": ("$$$", True),
    "Clambroth/Indian": ("$$$", True), "Transitional": ("$$$", True),
    "M.F. Christensen": ("$$$", True), "Core Swirl": ("$$", True),
    "Swirl": ("$$", True), "Handmade": ("$$", True), "Handmade (other)": ("$$", True),
    "Akro Agate": ("$$ (named $$$$+)", True), "Peltier": ("$$ (NLR/comic $$$$+)", True),
    "Master Marble": ("$$", True), "Other U.S.": ("$$", True),
    "Vitro Agate": ("$ (named higher)", False), "Marble King": ("$ filler", False),
    "Alley Agate": ("$ filler", False), "Heaton/Bogard/JABO": ("$ filler (modern)", False),
    "Foreign": ("$ filler (Vacor)", False), "Machine-made": ("$ filler", False),
    "Earthenware": ("$$ (antique clay)", False), "Stoneware": ("$$", False),
    "China": ("$$ (painted china)", False), "Agate And Other Mineral": ("$ (not a toy marble)", False),
    "Other Non Glass Materials": ("$ filler", False), "Non Glass Marbles": ("$ filler", False),
}

# per-label CLIP text prompts (grounded in specializations/marbles.md tells)
PROMPTS = {
    "Akro Agate": ["a vintage Akro Agate corkscrew marble", "an Akro Agate Popeye corkscrew with white filaments", "an Akro Agate patch marble with oxblood"],
    "Peltier": ["a Peltier National Line Rainbo marble with thin ribbons and two seams", "a Peltier comic picture marble", "a Peltier Rainbo patch marble"],
    "Christensen Agate": ["a Christensen Agate Guinea marble with peppered colored flecks", "a Christensen Agate marble with sharp non-bleeding bright colors", "a Christensen Agate flame marble"],
    "Vitro Agate": ["a Vitro Agate patch marble", "a Vitro Conqueror blue and white swirl marble", "a Vitro Agate cat's-eye marble"],
    "Marble King": ["a Marble King Rainbow patch marble white base with one colored patch", "a Marble King bumblebee black and yellow patch marble"],
    "Master Marble": ["a Master Marble sunburst marble", "a Master Glass swirl marble"],
    "Alley Agate": ["a West Virginia Alley Agate multicolor swirl marble"],
    "Heaton/Bogard/JABO": ["a modern JABO machine-made swirl marble with bright colors", "a Heaton swirl marble"],
    "M.F. Christensen": ["an M.F. Christensen slag marble with a creased pontil", "an early hand-gathered American slag marble"],
    "Other U.S.": ["a Ravenswood or Champion or Cairo West Virginia swirl marble"],
    "Foreign": ["a modern Vacor de Mexico machine-made marble with orange-peel surface", "a foreign machine-made cat's-eye marble"],
    "Machine-made": ["a machine-made glass marble with seams and no pontils"],
    "Lutz": ["a handmade Lutz marble with glittering gold copper bands"],
    "End of Day": ["a handmade end of day onionskin marble with stretched colored flecks", "a handmade end of day cloud marble with suspended flecks"],
    "Core Swirl": ["a handmade core swirl marble with a twisted colored core in clear glass", "a handmade latticinio core swirl marble"],
    "Swirl": ["a German handmade swirl marble with colored bands", "a handmade banded swirl marble"],
    "Clambroth/Indian": ["a handmade clambroth marble opaque base with many thin colored lines", "a handmade Indian marble dark black base with colored surface bands"],
    "Sulphide": ["a handmade sulphide marble clear glass with an embedded white figure"],
    "Handmade": ["a handmade glass marble with two rough pontils"],
    "Handmade (other)": ["a handmade glass marble with a mica or specialty pattern"],
    "Transitional": ["a transitional marble with a single pontil and machine-made character"],
    "Earthenware": ["an antique earthenware clay marble", "a brown dug clay marble"],
    "Stoneware": ["a stoneware crockery marble", "a Bennington glazed clay marble"],
    "China": ["a glazed china marble with hand-painted lines"],
    "Agate And Other Mineral": ["a polished natural agate stone sphere", "a carnelian or quartz mineral marble"],
    "Other Non Glass Materials": ["a steel or stone or clay non-glass marble"],
    "Non Glass Marbles": ["a non-glass marble made of clay stone or steel"],
}

FAMILY_DESC = {
    "handmade": "Handmade (cane-cut / single-gather) — where the high-end lives; pontils prove METHOD only",
    "transitional": "Transitional / hand-gathered — single pontil + machine character",
    "machine": "Machine-made — seams, no pontils; 90% of any pile, value via named type",
    "nonglass": "Non-glass — clay / stone / steel / mineral",
}


def _softmax(x, tau):
    import numpy as np
    z = (x - x.max()) / tau
    e = np.exp(z)
    return e / e.sum()


def topk_floor(pairs, *, abs_floor=0.03, rel_frac=1 / 6, min_show=2, max_show=5):
    """Trim a ranked [(label, prob)] down to the *meaningful* candidates.

    Keeps a candidate only if it clears BOTH an absolute floor and a fraction of
    the leader's probability — so a genuine close 2nd/3rd is shown, but the
    ultra-low-probability tail ("0.0085% chance of X") is dropped. Always keeps
    at least `min_show` so a real runner-up is never hidden behind the floor.
    Returns (shown, n_hidden, hidden_mass).
    """
    if not pairs:
        return [], 0, 0.0
    p1 = pairs[0][1]
    cut = max(abs_floor, rel_frac * p1)
    shown = []
    for i, (lab, p) in enumerate(pairs[:max_show]):
        if i < min_show or p >= cut:
            shown.append((lab, p))
        else:
            break
    hidden = pairs[len(shown):]
    return shown, len(hidden), float(sum(p for _, p in hidden))


class Classifier:
    def __init__(self, *, w_proto=1.0, w_text=0.0, w_knn=1.0, tau=0.04,
                 knn_k=3, abstain_p=0.45, abstain_margin=0.10):
        import numpy as np
        self.np = np
        self.w = (w_proto, w_text, w_knn)
        self.tau, self.knn_k = tau, knn_k
        self.abstain_p, self.abstain_margin = abstain_p, abstain_margin
        emb, meta = MCSA.load()
        self.emb = emb.astype("float32")
        self.y = [m["maker"] for m in meta]
        self.labels = sorted(set(self.y))
        self.li = {l: i for i, l in enumerate(self.labels)}
        self.yi = np.array([self.li[v] for v in self.y])
        # per-class centroid (prototype head)
        C, D = len(self.labels), self.emb.shape[1]
        self.csum = np.zeros((C, D), "float32")
        self.ccnt = np.zeros(C, "float32")
        for i, c in enumerate(self.yi):
            self.csum[c] += self.emb[i]
            self.ccnt[c] += 1
        self.proto = self._norm(self.csum / self.ccnt[:, None])
        # balanced family head: one centroid per family (4), so the family call
        # isn't dragged to "machine" just because it owns the most label slots.
        self.fam_names = ["handmade", "transitional", "machine", "nonglass"]
        self.fi = {f: i for i, f in enumerate(self.fam_names)}
        self.fyi = np.array([self.fi[FAMILY[v]] for v in self.y])
        self.fsum = np.zeros((4, D), "float32"); self.fcnt = np.zeros(4, "float32")
        for i, f in enumerate(self.fyi):
            self.fsum[f] += self.emb[i]; self.fcnt[f] += 1
        self.fproto = self._norm(self.fsum / self.fcnt[:, None])
        # text head (built once; needs the model). Off by default — CLIP zero-shot
        # scored 7% top-1 solo on these fine types; kept for stronger-model trials.
        self.tproto = self._build_text() if w_text > 0 else None

    def _norm(self, M):
        n = self.np.linalg.norm(M, axis=-1, keepdims=True)
        return M / self.np.clip(n, 1e-9, None)

    def _build_text(self):
        import numpy as np
        vecs = []
        for l in self.labels:
            t = embed_texts(PROMPTS.get(l, [l]))
            vecs.append(self._norm(t.mean(0, keepdims=True))[0])
        return np.vstack(vecs).astype("float32")

    def _knn_scores(self, qsim, exclude=None):
        """Per-class mean of the top-k member cosine sims. qsim: [N]."""
        import numpy as np
        out = np.full(len(self.labels), -1.0, "float32")
        for c in range(len(self.labels)):
            idx = np.where(self.yi == c)[0]
            if exclude is not None:
                idx = idx[idx != exclude]
            if len(idx) == 0:
                continue
            s = np.sort(qsim[idx])[::-1][: self.knn_k]
            out[c] = s.mean()
        return out

    def _proto_loo(self, i):
        """Prototypes with sample i removed from its own class (for LOO)."""
        c = self.yi[i]
        cs = self.csum.copy(); cc = self.ccnt.copy()
        cs[c] -= self.emb[i]; cc[c] -= 1
        cc = self.np.clip(cc, 1e-9, None)
        return self._norm(cs / cc[:, None])

    def _blend(self, q, proto, knn_exclude=None):
        import numpy as np
        wp, wt, wk = self.w
        qsim = self.emb @ q
        ks = self._knn_scores(qsim, exclude=knn_exclude)
        prob = wp * _softmax(proto @ q, self.tau) + wk * _softmax(ks, self.tau)
        if self.tproto is not None and wt > 0:
            prob = prob + wt * _softmax(self.tproto @ q, self.tau)
        return prob / prob.sum()

    def _family(self, q, exclude=None):
        """Balanced 4-way family call from the family centroids. -> (name, prob)."""
        import numpy as np
        fp = self.fproto
        if exclude is not None:                      # LOO: drop self from its family
            f = self.fyi[exclude]
            fs = self.fsum.copy(); fc = self.fcnt.copy()
            fs[f] -= self.emb[exclude]; fc[f] -= 1
            fp = self._norm(fs / np.clip(fc, 1e-9, None)[:, None])
        prob = _softmax(fp @ q, self.tau)
        i = int(np.argmax(prob))
        return self.fam_names[i], float(prob[i]), prob

    def predict(self, q, age=None, prior_strength=1.0):
        """q: [D] normalized. -> dict with label probs, family, abstain.

        Carries the FULL ranked family vector and a `family_soft` flag: the
        machine/non-glass families own most of the label slots and the corpus
        base rate, so the head can drift to "machine" with a handmade/
        transitional reading sitting right behind it. We surface that runner-up
        family instead of silently reporting only the winner — leaning generic
        with a live PULL second is exactly what "going soft" looks like.

        `age` (None = raw CLIP likelihood, the measurement path used by eval /
        gold_eval). When set (float or a marble_prior preset name), the family
        vector is combined Bayesian-style with the estate "value lean" prior —
        `families_adj` + an `era_lean` are added and family_soft is judged on the
        prior-adjusted vector. The prior moves the LEAN, not the honesty clamps.
        """
        import numpy as np
        prob = self._blend(q, self.proto)
        order = np.argsort(-prob)
        top = [(self.labels[i], float(prob[i])) for i in order[:8]]
        fname, fprob, fvec = self._family(q)         # balanced family head
        forder = np.argsort(-fvec)
        families = [(self.fam_names[i], float(fvec[i])) for i in forder]
        p1 = top[0][1]; p2 = top[1][1] if len(top) > 1 else 0.0
        abstain = (p1 < self.abstain_p) or (p1 - p2 < self.abstain_margin)
        out = {"top": top, "family": (fname, fprob), "families": families,
               "abstain": abstain}
        # apply the operating prior (estate value-lean) when an age is supplied
        if age is not None:
            from marble_prior import apply_prior, vintage_fraction, resolve_age
            lik = {f: p for f, p in families}
            post = apply_prior(lik, age, strength=prior_strength)
            fam_judge = sorted(post.items(), key=lambda kv: -kv[1])
            out["families_adj"] = fam_judge
            out["age"] = resolve_age(age)
            out["era_lean"] = vintage_fraction(age)
        else:
            fam_judge = families                     # judge softness on raw
        # softness gate: leader family is machine/non-glass, but a PULL family
        # (handmade/transitional) is a live runner-up -> don't quietly jar it.
        lead_fam, lead_fp = fam_judge[0]
        family_soft = False
        if lead_fam in ("machine", "nonglass"):
            for fam, fp in fam_judge[1:]:
                if fam in ("handmade", "transitional") and (
                        fp >= lead_fp * 0.6 or lead_fp - fp <= 0.15):
                    family_soft = True
                    break
        out["family_soft"] = family_soft
        return out

    # ---- evaluation ----
    def eval_loo(self, verbose=True):
        import numpy as np
        N = len(self.y)
        t1 = t3 = fam_ok = 0
        per_fam = {}
        conf = []        # (top1_prob, ok1) for the risk-coverage curve
        for i in range(N):
            q = self.emb[i]
            prob = self._blend(q, self._proto_loo(i), knn_exclude=i)
            order = np.argsort(-prob)
            truth = self.yi[i]
            ok1 = bool(order[0] == truth); ok3 = bool(truth in order[:3])
            t1 += ok1; t3 += ok3
            ftruth = FAMILY[self.labels[truth]]
            fam_pred, _, _ = self._family(q, exclude=i)        # balanced family head
            okf = (fam_pred == ftruth); fam_ok += okf
            d = per_fam.setdefault(ftruth, [0, 0]); d[1] += 1; d[0] += okf
            conf.append((float(prob[order[0]]), ok1))
        if verbose:
            print(f"\nLeave-one-out over {N} MCSA labels "
                  f"(w_proto={self.w[0]} w_text={self.w[1]} w_knn={self.w[2]} "
                  f"tau={self.tau} k={self.knn_k}):")
            print(f"  maker/type top-1 : {t1/N:5.1%}")
            print(f"  maker/type top-3 : {t3/N:5.1%}")
            print(f"  FAMILY (balanced): {fam_ok/N:5.1%}")
            for f, (ok, n) in sorted(per_fam.items()):
                print(f"      {f:12} {ok/n:5.1%}  (n={n})")
            # risk-coverage: sort by confidence desc, accuracy at each coverage
            conf.sort(key=lambda x: -x[0])
            oks = np.array([c[1] for c in conf], float)
            print("  risk-coverage (rank by confidence, take the most-confident X%):")
            for cov in (0.10, 0.20, 0.30, 0.50, 0.75, 1.00):
                k = max(1, int(round(cov * N)))
                acc = oks[:k].mean()
                thr = conf[k - 1][0]
                print(f"      top {cov:4.0%}  ({k:>3} marbles, p>={thr:.2f})  "
                      f"-> top-1 {acc:5.1%}")
        return {"top1": t1/N, "top3": t3/N, "family": fam_ok/N}


def _report(clf, result, refs):
    print(f"\n=== marble ID  ({', '.join(Path(r).name for r in refs)}) ===")
    # FAMILY — show the runner-up family when it's a live call (≥10% and ≥1/3 of
    # the leader), not just the winner. A close 2nd family is the headline tell.
    fams = result["families"]
    shown_f, _, _ = topk_floor(fams, abs_floor=0.10, rel_frac=1 / 3,
                               min_show=1, max_show=3)
    fam_str = "  ·  ".join(f"{f} {p:.0%}" for f, p in shown_f)
    print(f"FAMILY (CLIP): {fam_str}")
    # operating prior (estate value-lean) — the call we actually act on
    flead = fams[0][0]
    if result.get("families_adj"):
        from marble_prior import value_posture, MODERN_FLOOR
        adj = result["families_adj"]
        flead = adj[0][0]
        shown_a, _, _ = topk_floor(adj, abs_floor=0.10, rel_frac=1 / 3,
                                   min_show=1, max_show=3)
        astr = "  ·  ".join(f"{f} {p:.0%}" for f, p in shown_a)
        vp = value_posture(result["age"])
        print(f"FAMILY (estate lean, age={result['age']:.2f}): {astr}   — "
              f"{FAMILY_DESC[flead]}")
        print(f"  era lean (if machine): ~{result['era_lean']:.0%} vintage / "
              f"{1 - result['era_lean']:.0%} modern  → default {vp['tier']}; "
              f"{vp['action']}  (modern floor {MODERN_FLOOR:.0%} until UV/raking)")
    else:
        print(f"  — {FAMILY_DESC[flead]}")
    if result["family_soft"]:
        print("   ⚠ SOFT family call — a handmade/transitional reading is a live "
              "runner-up; do NOT jar as filler on this alone (a pontil/cane/Lutz "
              "tell is REQUIRED to claim handmade).")
    if result["abstain"]:
        print("MAKER/TYPE: ⚠ uncertain — candidates only (judge on the photo / get the shot):")
    else:
        print("MAKER/TYPE (calibrated):")
    # top-5 with a noise floor — show the real contenders, drop the long tail
    shown, n_hidden, _ = topk_floor(result["top"])
    for rank, (lab, p) in enumerate(shown, 1):
        tier, pull = TIER.get(lab, ("?", False))
        flag = "PULL" if pull else "filler"
        mark = "→" if rank == 1 and not result["abstain"] else " "
        print(f"  {mark} {p:5.1%}  {lab:22} [{flag}]  {tier}")
    if n_hidden:
        print(f"     (+{n_hidden} lower-probability candidate(s) below the noise floor)")
    lab0 = result["top"][0][0]
    _, pull0 = TIER.get(lab0, ("", False))
    if pull0 or FAMILY[lab0] in ("handmade", "transitional") or result["family_soft"]:
        print("  ↳ resolution: PULL — backlit + pole-macro; confirm named type before pricing "
              "(method≠origin≠era; an unconfirmed rare type stays [BEST-CASE]).")
    elif result.get("families_adj"):
        from marble_prior import value_posture
        vp = value_posture(result["age"])
        print(f"  ↳ resolution: {vp['action']} (default {vp['tier']}); a named-pattern "
              "or pontil tell upgrades it — method≠origin≠era.")
    else:
        print("  ↳ resolution: likely filler — jar/lot it unless size/UV/oxblood says otherwise.")


def cmd_classify(args):
    clf = Classifier()
    age = None if args.no_prior else args.age
    if age is not None:
        from marble_prior import describe
        print(describe(age))
    if args.detect:
        # auto-crop every marble out of each photo, then classify each one
        from marble_crop import detect_and_crop
        marbles = []
        for r in args.images:
            for k, (c, im) in enumerate(detect_and_crop(r), 1):
                marbles.append((f"{Path(r).name}#m{k}", im))
        if not marbles:
            raise SystemExit("No marbles detected — try --no-mask shots or space them out.")
        print(f"detected {len(marbles)} marble(s) across {len(args.images)} photo(s)")
        for tag, im in marbles:
            q = embed_images([im])[0]
            _report(clf, clf.predict(q, age=age, prior_strength=args.prior_strength), [tag])
        return
    pil = []
    for r in args.images:
        p = Path(r)
        try:
            from PIL import Image
            pil.append(Image.open(p).convert("RGB") if p.exists() else download_pil(r))
        except Exception as e:
            print(f"! {r}: {e}", file=sys.stderr)
    if not pil:
        raise SystemExit("No usable image.")
    q = embed_images(pil)
    qn = clf._norm(q.mean(0, keepdims=True))[0] if len(pil) > 1 else q[0]
    _report(clf, clf.predict(qn, age=age, prior_strength=args.prior_strength), args.images)


def cmd_eval(args):
    clf = Classifier(w_proto=args.w_proto, w_text=args.w_text, w_knn=args.w_knn,
                     tau=args.tau, knn_k=args.k,
                     abstain_p=args.abstain_p, abstain_margin=args.margin)
    clf.eval_loo()


def cmd_families(args):
    by = {}
    for lab, fam in FAMILY.items():
        by.setdefault(fam, []).append(lab)
    for fam in ("handmade", "transitional", "machine", "nonglass"):
        print(f"\n{fam.upper()} — {FAMILY_DESC[fam]}")
        for lab in sorted(by[fam]):
            tier, pull = TIER.get(lab, ("?", False))
            print(f"   {'PULL  ' if pull else 'filler'} {lab:22} {tier}")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("classify", help="full ID report for a marble (1+ angle photos)")
    p.add_argument("images", nargs="+")
    p.add_argument("--detect", action="store_true",
                   help="auto-crop each marble from the photo(s) first (lib/marble_crop)")
    p.add_argument("--age", default="estate-typical",
                   help="operating prior age-confidence: 0..1 or a preset "
                        "(dollar-store/no-provenance/estate-typical/old-collection/"
                        "known-antique). Default estate-typical. See lib/marble_prior.py")
    p.add_argument("--no-prior", action="store_true",
                   help="raw CLIP likelihood only — no estate value-lean")
    p.add_argument("--prior-strength", type=float, default=1.0,
                   help="how hard the prior bites (0=ignore, 1=full Bayes, >1=stronger)")
    p.set_defaults(func=cmd_classify)
    p = sub.add_parser("eval", help="leave-one-out accuracy on the MCSA labels")
    p.add_argument("--w-proto", type=float, default=1.0)
    p.add_argument("--w-text", type=float, default=0.0)
    p.add_argument("--w-knn", type=float, default=1.0)
    p.add_argument("--tau", type=float, default=0.04)
    p.add_argument("--k", type=int, default=3)
    p.add_argument("--abstain-p", type=float, default=0.45)
    p.add_argument("--margin", type=float, default=0.10)
    p.set_defaults(func=cmd_eval)
    p = sub.add_parser("families", help="print label->family / value-tier map")
    p.set_defaults(func=cmd_families)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
