#!/usr/bin/env python3
"""Read the ANSWERS in Marble Connection threads, not the (OP-guess) titles.

A thread title is the original poster's QUESTION — "Akro Popeye?", "MK Bumblebee
with a twist?" — and is frequently WRONG. The real identification is in the
REPLIES, especially from high-reputation regulars (moderators / supporting
members / high post counts) who answer hundreds of these. Treat the title as an
unverified guess; trust the experienced responders.

This parses a thread's posts (Invision Power Board), scores each responder by
reputation, and surfaces the expert answer(s) — so forum retrieval reports what
the experts concluded, not what the asker guessed.

Reputation = member-group weight × log10(post_count+10), optionally boosted by
cross-thread answer frequency (how many threads this person has answered in the
crawl — the strongest "trusted regular" signal). Build that registry with
`build_responder_registry()` over many threads.
"""
from __future__ import annotations

import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vindex import http_get

# member-group → trust weight (Invision ranks seen on marbleconnection)
GROUP_WEIGHT = {
    "administrator": 4.0, "admin": 4.0, "global moderator": 3.5, "moderator": 3.5,
    "supporting member": 2.5, "advanced member": 2.0, "veteran member": 2.0,
    "members": 1.0, "member": 1.0, "new member": 0.5, "newbie": 0.4,
}


def _posts_to_int(s: str) -> int:
    s = s.lower().replace(",", "").strip()
    m = re.match(r"^([\d.]+)k$", s)
    if m:
        return int(float(m.group(1)) * 1000)
    return int(s) if s.isdigit() else 0


def _group_weight(group) -> float:
    g = (group or "").lower()
    for k, w in GROUP_WEIGHT.items():
        if k in g:
            return w
    return 1.0


def reputation(group, posts, *, freq=0) -> float:
    """Trust score for a responder. freq = #threads they've answered (registry)."""
    base = _group_weight(group) * math.log10((posts or 0) + 10)
    return round(base * (1 + min(freq, 50) / 25.0), 3)   # frequency up-weights, capped


# maker/type keywords — a short reply naming one of these is a real answer
_KW = ("akro", "pelt", "vitro", "vacor", "alley", "master", "jabo", "marble king",
       " mk", "christensen", "cac", "rainbo", "conqueror", "popeye", "oxblood",
       "sulphide", "lutz", "slag", "swirl", "patch", "onionskin", "modern",
       "chinese", "foreign", "opal", "tri-lite", "trilite", "phantom", "meteor",
       "sunburst", "navarre", "german", "bumblebee", "st mary", "paden", "imperial")


def clean_text(t: str) -> str:
    """Strip trailing reaction/vote counts ('bump 1' -> 'bump', 'x3 1 1' -> 'x3')."""
    return re.sub(r"(\s+\d+)+\s*$", "", (t or "").strip()).strip()


def is_substantive(t: str) -> bool:
    """A real ID answer, not a bump/agree/emoji/number."""
    t = clean_text(t)
    if len(t) < 6:
        return False
    if len(t.split()) >= 4:
        return True
    return any(k in t.lower() for k in _KW)   # short but names a maker/type


def best_answer(replies):
    """Highest-reputation SUBSTANTIVE reply (skips bump/x2/yes/emoji); falls back
    to the highest-rep reply if none qualify."""
    subs = [r for r in replies if is_substantive(r.get("text", ""))]
    pool = subs or list(replies)
    return max(pool, key=lambda r: r.get("rep", 0)) if pool else None


# --- consensus: agreements ARE second confirmations (user rule, 2026-06-25) ----
# Short replies like "x2", "agree", "yes", "+1" are expert SECOND CONFIRMATIONS,
# not junk — a known moderator's "x2" backs the ID above it. "bump" is NOT an
# agreement (it's "someone answer faster"). xN is not trusted as a hard integer;
# agreements are weighted by responder reputation instead.
MAKER_CANON = [
    ("Peltier", ("peltier", "pelt", "rainbo", "nlr", "peerless")),
    ("Akro", ("akro", "popeye", "corkscrew")),
    ("Vitro", ("vitro", "conqueror", "tri-lite", "trilite", "tiger eye", "phantom", "all-red")),
    ("Marble King", ("marble king", " mk", "rainbow", "bumblebee", "st mary", "paden")),
    ("Master", ("master", "sunburst", "meteor", "comet")),
    ("Alley", ("alley",)),
    ("JABO", ("jabo",)),
    ("Christensen Agate", ("christensen", "cac", "guinea", "flame")),
    ("M.F. Christensen", ("m.f. christensen", "mfc", "m f christensen")),
    ("Vacor/modern", ("vacor", "mega", "chinese", "modern", "foreign", "asian", "mexico")),
    ("Ravenswood/Champion/Cairo/Heaton", ("ravenswood", "champion", "cairo", "heaton")),
    ("handmade", ("handmade", "german", "onionskin", "lutz", "swirl", "sulphide",
                  "clambroth", "indian", "end of day")),
]


def agreement_kind(text):
    """-> 'xN' | 'agree' | None.  'bump' deliberately NOT an agreement."""
    t = clean_text(text).lower()
    if not t:
        return None
    if re.fullmatch(r"x\s?\d+", t):
        return "xN"
    if len(t.split()) <= 5 and re.search(
            r"\b(agreed?|yes|yep|yup|yuu+p|ditto|same|me too|\+\s?1)\b", t):
        return "agree"
    return None


def extract_maker(text):
    tl = " " + clean_text(text).lower() + " "
    for canon, kws in MAKER_CANON:
        if any(k in tl for k in kws):
            return canon
    return None


def consensus(posts):
    """Tally reputation-weighted expert votes per maker across a thread's replies.

    A stated ID (substantive reply naming a maker) opens/feeds a tally; a pure
    agreement ('x2'/'agree') adds the agreer's reputation to the MOST RECENT ID.
    Returns (ranked, flags): ranked = [(maker, {weight, n, supporters, quote})],
    flags = notes where an agreement was ambiguous (multiple IDs above it).
    """
    cands, flags = {}, []
    leading = None
    since_agree = []                       # makers stated since the last agreement
    for r in posts[1:]:
        txt = clean_text(r.get("text", ""))
        if not txt:
            continue
        mk, ag = extract_maker(txt), agreement_kind(txt)
        if mk and (is_substantive(txt) or ag):          # a stated ID
            c = cands.setdefault(mk, {"weight": 0.0, "n": 0, "supporters": [], "quote": ""})
            c["weight"] += r.get("rep", 0); c["n"] += 1
            c["supporters"].append(r["author"])
            if len(txt) > len(c["quote"]):
                c["quote"] = txt[:90]
            leading = mk; since_agree.append(mk)
        elif ag:                                          # pure agreement -> most recent ID
            if leading is None:
                continue
            if len(set(since_agree)) > 1:
                flags.append(f"ambiguous '{txt[:20]}' by {r['author']} "
                             f"(IDs above: {sorted(set(since_agree))})")
            c = cands[leading]
            c["weight"] += r.get("rep", 0); c["n"] += 1
            c["supporters"].append(r["author"])
            since_agree = [leading]
        # else: non-ID, non-agreement (bump, OP back-and-forth) -> ignore
    ranked = sorted(cands.items(), key=lambda kv: -kv[1]["weight"])
    return ranked, flags


def parse_posts(htmltext: str):
    """[{i, is_op, author, group, posts, rep, text}] for one thread page.

    Post 0 is the OP (the question). Posts 1.. are replies (the answers).
    """
    from lxml import html as lhtml
    doc = lhtml.fromstring(htmltext)
    arts = doc.xpath('//article[@id and contains(@class,"ipsComment")]')
    out = []
    for i, a in enumerate(arts):
        nm = [t.strip() for t in
              a.xpath('.//aside//*[contains(@class,"ipsType_break")]//text()') if t.strip()]
        if not nm:
            nm = [t.strip() for t in
                  a.xpath('.//aside//a[contains(@href,"/profile/")]//text()') if t.strip()]
        name = nm[0] if nm else "?"
        lis = [" ".join(s.strip() for s in li.xpath(".//text()") if s.strip())
               for li in a.xpath(".//aside//li")]
        lis = [t for t in lis if t]
        group, posts = None, 0
        for t in lis:
            if group is None and any(k in t.lower() for k in GROUP_WEIGHT):
                group = t
            if re.match(r"^[\d,]+\.?\d*k?$", t):
                posts = max(posts, _posts_to_int(t))
        body = " ".join(s.strip() for s in a.xpath(
            './/*[@data-role="commentContent"]//text() | '
            './/div[contains(@class,"cPost_contentWrap")]//text()') if s.strip())
        out.append({"i": i, "is_op": i == 0, "author": name, "group": group,
                    "posts": posts, "rep": reputation(group, posts), "text": body})
    return out


def thread_answer(turl: str, *, registry=None, top=2):
    """Fetch a thread → (op_question, [expert replies sorted by reputation]).

    registry: optional {author: freq} to up-weight trusted regulars.
    """
    posts = parse_posts(http_get(turl))
    if not posts:
        return None, []
    op = posts[0]
    replies = posts[1:]
    if registry:
        for r in replies:
            r["rep"] = reputation(r["group"], r["posts"], freq=registry.get(r["author"], 0))
    replies.sort(key=lambda r: -r["rep"])
    return op, replies[:top]


def build_responder_registry(turls, *, verbose=True):
    """Count how many threads each author answered → the 'trusted regular' signal."""
    from collections import Counter
    freq, groups = Counter(), {}
    for j, u in enumerate(turls):
        try:
            posts = parse_posts(http_get(u))
        except Exception:
            continue
        for r in posts[1:]:
            freq[r["author"]] += 1
            groups[r["author"]] = r["group"]
        if verbose and (j + 1) % 25 == 0:
            print(f"  scanned {j+1}/{len(turls)} threads", flush=True)
    return freq, groups


if __name__ == "__main__":
    # demo: python lib/forum_replies.py <thread-url>
    url = sys.argv[1] if len(sys.argv) > 1 else \
        "https://marbleconnection.com/topic/44683-mk-bumblebee-with-a-twist/"
    op, experts = thread_answer(url, top=3)
    print(f"OP (treat as GUESS): {op['author']} — {op['text'][:120]}\n")
    print("EXPERT REPLIES (trust these):")
    for r in experts:
        print(f"  [{r['rep']:.2f}] {r['author']} ({r['group']}, {r['posts']} posts):")
        print(f"      {r['text'][:200]}")
