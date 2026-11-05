# The discussion gate on pre_report's number branches

`pre_report.py` blocks an outgoing message when a reported figure contradicts a promoted anchor in
`lived_anchors.json`, or when a drawdown or sign statistic contradicts the measured record. Those
branches cannot tell a message that reports a wrong number from one that discusses it. Each of these
would block:

- "We should not report the pipeline as $123k per year; that is about ten times under the ledger." (mismatch)
- "A 60-85% drawdown claim on the demo book is wrong; the measured worst is far smaller." (drawdown)
- "The old memory claimed the current strategy loses money every year; it was retracted." (sign)
- "Earlier I wrongly said the pipeline earns $123k per year; ignore that." (mismatch)

On the deployment where this was measured, the real-prose block rate was low (40 of 11,402 assistant
messages, 0.35%). The cost is concentrated, though: a correction turn is made of exactly this prose, and a
guard that blocks correct discussion teaches its reader to click past it.

## What was tried

A vocabulary gate, the `_DISCUSS` pattern in `pre_report.py`. When a message carries discussion phrasing
("do not report", "wrongly said", "retracted", "withdrawn", "was wrong", "the correction:"), the mismatch
and drawdown/sign flags were dropped. Deployment rules were left alone because each rule carries its own
negation pattern. On the four probes above false blocks fell from 4 to 0, and on real prose from 3 to 1 in
2,500 messages.

## Why it is off

The gate opened a worse hole. A fresh wrong report that happens to use the same words passed unblocked,
for example "Setting the withdrawn anchor aside, the pipeline earns $123k per year." The adversarial set in
`referee_mention_fix_proto.py` fell from 5 of 5 blocked to 2 of 5. Mention and act share their surface
vocabulary, so a word list cannot separate them. A missed wrong number defeats the purpose of the guard,
while a false block on discussion is rare (about 0.03% of real messages) and costs one retry.

So `check()` still computes `discuss` and returns it for logging, and it suppresses nothing.

## Trying again

A new gate has to pass two checks before it is wired in:

1. `python referee_mention_fix_proto.py`: every ACT case and every ADVERSARIAL case still blocks (no new
   false negatives), with the MENTION cases as high as you can get them. On a fresh install with no
   promoted anchors, the cases that depend on an anchor do not block at all; promote one in
   `lived_anchors.json` (`"blocking_oracle": true`) to exercise them.
2. `python eval_pre_report_overfire.py 60` over your own transcript archive: the real-prose block rate
   must not rise.

Structure is a more promising signal than vocabulary: the figure sitting inside quotation marks, or a
clause whose subject is an earlier message rather than the system being measured.
