#!/usr/bin/env python3
"""Gold-set regression for the marble classifier — "am I right, or going soft?"

The worry: since the CLIP switch the family call drifts toward generic /
non-vintage machine-made on nearly everything. That drift is *expected* from the
pipeline (CLIP can't see fine tells; the corpus mode is "common machine-made";
the family head fails on cluttered phone photos), so the only way to know if a
"generic machine-made" call is RIGHT or just SOFT is to measure it against
known answers — and the forum index already is one.

Ground truth (no re-crawl): `kb/index/marbleconnection/meta.jsonl` carries the
parsed expert answer per image (`answer`, `answer_by`, `answer_rep`), already
reputation-ranked by lib/forum_replies. We map that answer text -> canonical
maker -> FAMILY, run today's classifier on the SAME (real, towel/caliper) photo,
and compare.

Headline metric = **softness rate**: of marbles the experts call
handmade/transitional, what fraction does the classifier bury as
machine/non-glass (i.e. "generic")? High softness = the model floated to the
corpus mode instead of reading the marble. The reverse rate (machine called
handmade) is reported too, so this isn't mistaken for trigger-happy upgrading.

This is a SAMPLE over a noisily-labelled corpus, not an oracle:
  - labels come from free-text expert replies (extract_maker); rows whose answer
    names no maker are dropped, not guessed.
  - the forum over-represents "interesting" marbles people hoped were good.
Both caveats are printed with the result — no silent truncation.

  python tools/gold_eval.py                 # stratified sample, family report
  python tools/gold_eval.py --n 200 --show 25
  python tools/gold_eval.py --min-rep 8 --seed 1 --misses-only
  python tools/gold_eval.py --detect        # auto-crop each photo first (slower)
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from vindex import download_pils, embed_images  # noqa: E402
from marble_classify import Classifier, topk_floor, FAMILY  # noqa: E402
from forum_replies import extract_maker  # noqa: E402

FORUM_META = ROOT / "kb" / "index" / "marbleconnection" / "meta.jsonl"

# canonical maker (forum_replies.MAKER_CANON) -> classifier FAMILY.
# nonglass never appears in the forum answer vocabulary, so gold is one of three.
CANON_FAMILY = {
    "Peltier": "machine", "Akro": "machine", "Vitro": "machine",
    "Marble King": "machine", "Master": "machine", "Alley": "machine",
    "JABO": "machine", "Christensen Agate": "machine",
    "Ravenswood/Champion/Cairo/Heaton": "machine", "Vacor/modern": "machine",
    "M.F. Christensen": "transitional", "handmade": "handmade",
}
PULL_FAMS = ("handmade", "transitional")
GENERIC_FAMS = ("machine", "nonglass")


def load_gold(min_rep, per_fam, seed):
    """-> stratified list of {img, tid, title, answer, canon, gold_fam}.

    One labelable row per thread (dedup by tid), filtered to high-reputation
    expert answers, balanced across gold families so the rare handmade/
    transitional cases aren't swamped by the machine majority.
    """
    by_fam = defaultdict(list)
    seen_tid = set()
    n_rows = n_answered = n_labelled = 0
    for line in FORUM_META.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        n_rows += 1
        ans, tid = r.get("answer"), r.get("tid")
        if not ans or r.get("answer_rep", 0) < min_rep or tid in seen_tid:
            continue
        n_answered += 1
        canon = extract_maker(ans)
        gold = CANON_FAMILY.get(canon) if canon else None
        if not gold:
            continue
        n_labelled += 1
        seen_tid.add(tid)
        by_fam[gold].append({"img": r["img"], "tid": tid,
                             "title": r.get("title", ""), "answer": ans,
                              "canon": canon, "gold_fam": gold})
    rng = random.Random(seed)
    picked = []
    for fam, rows in by_fam.items():
        rng.shuffle(rows)
        picked.extend(rows[:per_fam])
    rng.shuffle(picked)
    avail = {f: len(v) for f, v in by_fam.items()}
    return picked, dict(rows=n_rows, answered=n_answered,
                        labelled=n_labelled, avail=avail)


def run(rows, clf, detect):
    """Download + classify each row. -> list of rows augmented with pred_fam,
    pred_families, top (maker top-5), family_soft. Drops un-fetchable images."""
    urls = [r["img"] for r in rows]
    got = dict(download_pils(urls, workers=16))
    out = []
    pend = [r for r in rows if r["img"] in got]
    if detect:
        from marble_crop import detect_and_crop, is_marble  # noqa: F401
    for r in pend:
        im = got[r["img"]]
        if detect:
            from marble_crop import detect_and_crop
            crops = detect_and_crop(im) if not isinstance(im, str) else []
            if crops:
                im = crops[0][1]            # largest detected marble
        q = embed_images([im])[0]
        res = clf.predict(q)
        r = dict(r, pred_fam=res["families"][0][0],
                 pred_families=res["families"], top=res["top"],
                 family_soft=res["family_soft"])
        out.append(r)
    return out


def report(rows, stats, show, misses_only):
    if not rows:
        raise SystemExit("No gold rows classified — check the forum index / network.")
    fams = ["handmade", "transitional", "machine", "nonglass"]
    # family confusion: gold (row) x pred (col)
    conf = defaultdict(Counter)
    for r in rows:
        conf[r["gold_fam"]][r["pred_fam"]] += 1

    print("\n" + "=" * 72)
    print(f"GOLD-SET REGRESSION  —  {len(rows)} marbles classified")
    print(f"corpus: {stats['rows']:,} forum imgs · {stats['labelled']:,} "
          f"thread-deduped expert labels available {stats['avail']}")
    print("ground truth = reputation-ranked forum expert answer (noisy); "
          "SAMPLE, not an oracle.")
    print("=" * 72)

    print("\nFAMILY confusion  (rows = expert truth, cols = classifier):")
    hdr = "  ".join(f"{f[:5]:>7}" for f in fams)
    print(f"  {'truth↓':14}{hdr}   acc")
    for g in fams:
        tot = sum(conf[g].values())
        if not tot:
            continue
        cells = "  ".join(f"{conf[g][p]:7d}" for p in fams)
        acc = conf[g][g] / tot
        print(f"  {g:14}{cells}   {acc:4.0%}  (n={tot})")

    # headline: softness — expert PULL family called generic
    pull_tot = sum(sum(conf[g].values()) for g in PULL_FAMS)
    pull_soft = sum(conf[g][p] for g in PULL_FAMS for p in GENERIC_FAMS)
    # reverse: expert machine called handmade/transitional (over-upgrading)
    mach_tot = sum(conf["machine"].values())
    mach_up = sum(conf["machine"][p] for p in PULL_FAMS)
    # how often the soft-flag would have caught the buried-handmade misses
    soft_misses = [r for r in rows
                   if r["gold_fam"] in PULL_FAMS and r["pred_fam"] in GENERIC_FAMS]
    soft_caught = sum(1 for r in soft_misses if r["family_soft"])

    print("\nHEADLINE:")
    if pull_tot:
        print(f"  SOFTNESS rate  (expert handmade/transitional → called generic): "
              f"{pull_soft}/{pull_tot} = {pull_soft / pull_tot:.0%}")
        print(f"     └ of those {len(soft_misses)} misses, the SOFT-family flag "
              f"would re-flag {soft_caught} ({soft_caught / max(len(soft_misses),1):.0%}) "
              f"for PULL instead of jarring them.")
    if mach_tot:
        print(f"  reverse rate   (expert machine → called handmade/transitional): "
              f"{mach_up}/{mach_tot} = {mach_up / mach_tot:.0%}")
    # maker top-5 recall: is the expert's maker even among the candidates?
    has_maker = [r for r in rows if r["canon"] != "handmade"]
    # loose: does any top-5 label roll up to the gold family?
    fam_in5 = sum(1 for r in rows
                  if r["gold_fam"] in {FAMILY.get(l) for l, _ in r["top"][:5]})
    print(f"  family-in-top5 (gold family appears among the 5 maker candidates): "
          f"{fam_in5}/{len(rows)} = {fam_in5 / len(rows):.0%}")

    # per-item detail with the floored top-5 (so close 2nd/3rd are visible)
    detail = soft_misses if misses_only else rows
    if show and detail:
        random.Random(0).shuffle(detail)
        print(f"\nPER-ITEM (top-5 with noise floor)  —  "
              f"{'soft misses' if misses_only else 'sample'}, showing "
              f"{min(show, len(detail))}:")
        for r in detail[:show]:
            shown, nhid, _ = topk_floor(r["top"])
            cand = "  ".join(f"{l}:{p:.0%}" for l, p in shown) + (
                f"  (+{nhid})" if nhid else "")
            ffam = "  ".join(f"{f}:{p:.0%}"
                             for f, p in r["pred_families"][:2])
            flag = " ⚠SOFT" if r["family_soft"] else ""
            hit = "✓" if r["pred_fam"] == r["gold_fam"] else "✗"
            print(f"  {hit} t{r['tid']} «{r['title'][:34]}»")
            print(f"      expert: {r['canon']} → {r['gold_fam'].upper()}   |   "
                  f"pred family: {ffam}{flag}")
            print(f"      maker top-5: {cand}")
    print()


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=60,
                    help="max marbles PER gold family (stratified) [60]")
    ap.add_argument("--min-rep", type=float, default=5.0,
                    help="min answerer reputation to trust the label [5.0]")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--show", type=int, default=12,
                    help="how many per-item top-5 panels to print [12]")
    ap.add_argument("--misses-only", action="store_true",
                    help="per-item panels = only the soft misses")
    ap.add_argument("--detect", action="store_true",
                    help="auto-crop the marble out of each photo first (slower)")
    a = ap.parse_args()

    rows, stats = load_gold(a.min_rep, a.n, a.seed)
    print(f"sampled {len(rows)} gold marbles "
          f"(<= {a.n}/family, min_rep {a.min_rep}); classifying…", flush=True)
    clf = Classifier()
    done = run(rows, clf, a.detect)
    report(done, stats, a.show, a.misses_only)


if __name__ == "__main__":
    main()
