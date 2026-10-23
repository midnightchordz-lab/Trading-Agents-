# Hide the Position Sizer (branch `feature/hide-position-sizer`)

Builds on `feature/outlook-labels` @ `d20d138` (your latest save). Fast-forward:

```bash
cd /app
git fetch https://github.com/midnightchordz-lab/trading-agents-.git feature/hide-position-sizer
git merge --ff-only FETCH_HEAD
```
If `--ff-only` is blocked only by your own test-report auto-commit, a plain
`git merge --no-edit FETCH_HEAD` is fine. Never force-push or rebase.

## Change

`frontend/app/analysis/[id].tsx` no longer renders `<PositionSizer>`, and its import is removed.
Computing "N shares" against the desk's levels reads as sizing a trade for
the user, which the outlook framing must not do. `src/components/PositionSizer.tsx`
is kept unchanged so it can return later (for example behind a SEBI-registered partner).

Nothing else changes: no backend, no dependencies, no lockfile.

## Verify

1. On the web preview, open an analysis. The OUTLOOK tab shows: verdict block, grounding badge,
   then directly the multi-horizon card. There is no "POSITION SIZER" card and no `testID="position-sizer"`.
2. The rest still renders: levels (OUTLOOK LEVEL / INVALIDATION), chart, fear & greed, headlines, debate.
   There's no red screen.
3. If any of your saved Playwright checks expect `position-sizer`, update them to expect it ABSENT.

No iOS build.
