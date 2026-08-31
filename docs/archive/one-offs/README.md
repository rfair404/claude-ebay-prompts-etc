# one-offs — completed scripts, kept as the record of a write

Each file here ran **once**, against the live account, to repair a specific
batch of offers. None of them is a tool: they hardcode a policy id, read a
survey dump that no longer exists at that path, and write their results to a
scratch JSON at the repo root. They are kept because a script that mutated
live listings is the only honest record of what was changed and how it was
verified — every one of them reads the offer back after writing rather than
trusting the write.

**Nothing here should be re-run.** The repo-root paths they expect are gone,
and `mpn_apply.py`'s `ROOT` no longer resolves to the repo root from this
directory. To do the same work today, use the tool the one-off was built on:
`tools/policy_sweep.py` for policy repair, `lib/list_edit_group.py` for
multi-variation offers.

| File | What it did |
|---|---|
| `apply_null_return_policy.py` | Put the 25 offers found with a null `returnPolicyId` onto the standard return policy, reading each one back to confirm. |
| `apply_return_policy_rest.py` | The follow-up sweep: re-surveyed every PUBLISHED offer, then moved the remainder onto the same policy, standalone offers first and CHOICE/multi-variation listings second. |
| `mpn_apply.py` | Moved every MPN-format (multi-variation) offer onto the free/seller-paid + eIS fulfillment policy and raised any variation under the $5-profit floor, backing up the prior offer bodies first. |

Both `apply_*` scripts were dot-prefixed (`.apply_rest.py`) while they lived at
the repo root, where `/.*.py` is gitignored. They were renamed when they moved
here (#99) — a hidden file in an archive is a file nobody reads.
