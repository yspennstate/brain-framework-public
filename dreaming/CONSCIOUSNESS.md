# Conscious / unconscious memory, and steerable dreams

Two small subsystems added 2026-07-08 (owner: "make the whole brain more robust, conscious and
human, with unconscious memories and conscious memories"). Both are designed to fail safe: if a
sidecar is missing or a script errors, every consumer falls back to its old behaviour and nothing
breaks.

## The tiers (`consciousness.py`)

A mind doesn't hold all of memory at one level. A small part is *conscious* — what surfaces at wake,
what leads recall, what you reach for first. The vast rest is *unconscious* — not gone, not even hard
to find if you go looking, but latent: it doesn't clog the front of the mind, and it surfaces on its
own mostly in dreams and by association.

`consciousness.py` draws that line and writes `consciousness.tsv` (fname / tier / reason). The line is
drawn by **woven-in-ness, not salience** — salience is recency-heavy, so a year of near-identical
status logs would read "vivid" on recency alone. A memory is **conscious** if it is any of: curated
into the live index, referenced by two or more others, a user/feedback rule, recent (< ~2 weeks), an
identity/nav anchor, or simply distinct substantive work. It is **unconscious** only if it is the
redundant substrate: a high-frequency near-duplicate log (the inspector / build-watchdog cycle traces)
or an old, unreferenced, uncurated stub. Default is conscious — we demote only what is clearly
substrate, never real work that merely went un-linked. On the 07-08 corpus that was 203 of 1346 (~15%),
almost all of it the automated cycle logs.

Nothing is ever deleted or hidden. The tier changes only **precedence**: the dream reaches into the
unconscious on purpose (`dream.py` section E — that's how latent material surfaces in sleep), and the
nav layer keeps the substrate from leading conscious memories' neighbour lists. Search finds every
memory regardless of tier.

## Steerable dreams (`DREAM_FOCUS.md`)

Drop a `DREAM_FOCUS.md` in this folder and the next dream weights toward it (`dream.py` prints it at
the top of the agenda; the REM prompt treats it as "tonight's focus, without ignoring the rest").
`run_dream.ps1` retires it to `DREAM_FOCUS.last` after the dream reads it — rename, never delete — so
one focus steers exactly one dream unless re-written. Use it to point a dream at something that just
landed (e.g. a day's harvested discoveries) instead of letting it pick purely from recency.

## Wiring

`run_dream.ps1` runs `consciousness.py` before `dream.py` each cycle, so the tiers are fresh when the
agenda is built. `dream.py` reads `consciousness.tsv` for the unconscious replay and `DREAM_FOCUS.md`
for the focus. `build_brain_architecture.py` reads `consciousness.tsv` to demote unconscious
neighbours in `RELATED.md`. Every one of these reads is wrapped so a missing/broken sidecar just
means "no tiers, old behaviour."
