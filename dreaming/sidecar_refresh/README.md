# Sidecar refresh - prepared, NOT armed (owner's yes/no)

The semantic embedding sidecar has NO scheduled refresh (root-caused 2026-07-24:
the dream's E2 pass never fires in practice; the only refreshes are manual).
It went stale 07-23 -> 07-25 -> 07-29, costing four measured rediscoveries on
07-28 and three manual box round-trips on 07-29/30.

This package is the recorded fix option 1 from
`embedding_sidecar_stale_box_rebuild_2026_07_24`: a dedicated windowless
scheduled task that refreshes only when coverage demands it (missing > 40,
any stale, or age > 36h), skips during dreams and box distress, ships the
current authority for incremental reuse (seconds, not minutes), publishes
through the sanctioned atomic flow, and retries once if the corpus moved
mid-cycle. Logs to `dreaming\logs\sidecar_refresh_*.log`.

## To arm it (one command, owner's call)

    schtasks /Create /TN "BrainSidecarRefresh" /SC DAILY /ST 05:30 ^
      /TR "wscript.exe //B \"<HOME>\.claude\dreaming\sidecar_refresh\sidecar_refresh_launcher.vbs\"" /F

Interactive (logged-on) is fine: it needs the logged-on session's ssh key
access, and the VBS guarantees no window. 05:30 sits after the 03:30 nightly
dream. To remove: `schtasks /Delete /TN "BrainSidecarRefresh" /F`.

## Field evidence (2026-07-30 05:04 local, same night this was written)
Three manual cycles ran that morning. Two WITHOUT authority reuse lost the
moved-corpus race to a concurrently-filing campaign agent (12-min full encodes;
the publish gate correctly refused both). The third, WITH the reuse step this
runner implements, encoded in ~1 min and published: 3,242/3,242 COMPLETE,
authority 34626418, verified by a keyword-free semantic recall probe. The
reuse step is what makes the schedule race-proof in practice.

Dry-fired manually 2026-07-30 06:50 via the VBS itself: hidden end to end, log
written (sidecar_refresh_20260730_065021.log), and it SKIPPED on the compute gate
(warn/throttle) before touching anything - the politeness ordering working live.

## Alternatives on record (same memory)
- Promote E2 out of the time-boxed dream (bigger dream-prompt change).
- Keep refreshing manually (the state that has failed three times).

## Not included, separate owner call
`memory/dreams/` is never embedded (flat scan in build_memory_embeddings.py) -
whether dream journals belong in the semantic index is its own question.
