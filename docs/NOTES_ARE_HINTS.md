# A note from the brain is a hint, not a verdict

Every note the brain surfaces into a session - a reflex fire from `instinct_prime.py`, an
association from `spread_recall.py`, a math note from `math_sense.py`, a recalled memory, a
note served into a prompt - says "this resembles a place we have been". It does not say the
resemblance holds here, and it does not say the remembered thing is right. A note can point
the right way or the wrong way, and the reader has to weigh it against what is in front of
them. Obeying a note is as wrong as ignoring one: on a proof-checking bank of faulted
arguments, served statements about the cited result helped the solver find dropped
hypotheses and hurt it on wrong citations, over-extended scopes and over-sharp constants,
because a note about the result made a wrong use of it look legitimate. Net zero, inside one
run.

So since 2026-09-02 the hooks fire less, say how confident they are, and put a hint under
every note.

## Fire less

`instinct_prime.py` shows at most two reflexes per action on every path (the graph-augment
path used to lift the cap to four). A reflex fires once when a session enters its territory,
then after 30 minutes, then after 2 hours, then every 8 hours. The backoff state is one small
file per session under `.instinct_fired/`, replaced atomically. It used to be one shared JSON
that every concurrent session read and rewrote; a reader that hit a half-written file fell
back to an empty state and wrote back only its own key, so the whole population's counts
kept collapsing and reflexes re-fired about five times per session-reflex pair instead of
twice. Replayed over a day of real fires, the new windows keep about 43 percent of what the
old ones would have kept, and no first fire on entering a territory is delayed.

Command-hazard scars (the reflexes whose danger surface is `act`: process matching, heredoc
surgery, CRLF iteration, backslash paths) stay silent when the action is a Write or Edit of
prose (`.md`, `.txt`, `.tex`, `.rst`, `.bib`, `.html`), because prose cannot run a command;
a write of code or a script keeps every reflex, and the catastrophic scars fire regardless.
Thirty-two hand-judged fires from one day put the case: on Write/Edit calls 2 of 12 fires
were on-domain, both on code, against 9 of 20 on commands. The cap is applied once, after
that filter and the read-only-mention filter, with a refill from the ranked hits, so a
filtered scar never costs another reflex its slot.

The cap has a measured cost, and it falls where notes matter most: a report. Fourteen
suppressed fires judged by hand put the cap at 8 of 11 on-domain (a money write-up matched
ten judged scars and showed two) against 0 of 2 for each of the precision, prose and
read-only-mention gates. So when the cap holds fires that passed every other filter, the
block ends with one line naming them - at most five names, no text - and the reader can
recall any by name. The volume stays at two notes and a line.

A note is also capped in length: the longest scars run past two thousand characters, and a
wall of text under every action is the opposite of a confident hint. The hook cuts a note at a
sentence end near 600 characters (the scar line near 320) and says how much more there is;
the memory holds the rest, one recall away.

Everything a gate silences is written to `instinct_suppressed.tsv` (timestamp, event, act
proxy, reflex, reason among `precision`, `cap`, `mention`, `prose`, `backoff`, session).
That log is the falsifier for firing less: a nightly sample of suppressed fires judged
on-domain says whether a gate is eating fires it should not. The nightly judge draws that
sample itself (`fire_judge_nightly.py`, agenda section J3, four cases per pass, gate reasons
before the by-design backoff; the midday nap judges this section too, so the gates' cost is
read twice a day) and its verdicts land in the same judged log with the reason
attached, so the cost of every gate is measured on the same footing as the fires it saved. The
sheet builder takes any reason the hook logs, not a fixed list: a new reason once left its rows
unplaced and the builder's placement loop never ended, and the nightly dream would have hung on
it - when a producer gains a category, grep every consumer for the list. Two tripwires now stand
behind that rule: the regression gate builds the judge's agenda in a child process under a budget
and fails loudly on a timeout, and the dream itself builds the section the same way, writing a
visible skip line into the agenda instead of waiting.
Those suppressed-stratum rows never enter a reflex's precision (precision is how often a
shown fire was on-domain); `reflex_precision.py` keeps them in a separate `gates` table,
per reason, with the share the judge found on-domain as the gate's measured cost.

The hook also records its own slow tail: any call over two seconds (`INSTINCT_SLOW_MS`) leaves a
row in `instinct_slow_calls.tsv`, because a call the runner kills at its timeout primes nothing
and would otherwise leave no trace at all; a run of rows near the timeout means fires were
killed, not silent.

Every trigger has a time budget, and one bad one kills the whole hook. The runner gives the
hook a few seconds; on a large Write or Edit payload a single quadratic pattern (a lookahead
over the whole text, an unbounded `[^\n]*` scan - and the JSON-serialised input has no real
newline for either to stop at) takes seconds alone, the runner kills the call, and every
reflex is lost silently for that action. After any compile, time every trigger alone on the
largest input the cap allows and treat anything over a second as a defect; cap the matched
text (16 KB here) against the corpus you actually have, not the one the cap was set for.

## The graph path primes on conscious recall only

A reflex can fire two ways: its own trigger matches the action (the regex path, the referent), or
the action is graph-near a scar (the graph path, an association). The graph path used to surface
any fire whose priming was nonzero, and the hook deposited every graph fire it computed into the
session's activation buffer, shown or not - so a reflex the model merely considered primed itself
to surface for the next twenty-five minutes on any action that reached its lowered bar. Forty
graph-path fires from one morning, judged by hand: two on-domain; thirty-nine of the forty would
not have fired with an empty buffer. Now a graph fire surfaces on priming only when a conscious
deposit is still hot (`GRAPH_PRIME_MIN`, a recall from the deliberate layer within about ten
minutes), the gut deposits nothing into the buffer it reads (a first step that deposited only the
shown fires still produced nine graph fires in twenty minutes, every one through priming - a shown
fire re-primes itself just as well), the fire log's path column travels into the judged rows, and
`reflex_precision.py` keeps a per-path table: a reflex whose graph-path fires are judged off-domain
is silenced on that path only, its own trigger untouched. The danger accumulator still ramps on a
real failure; that channel never primed anything by reflex.

One more door closed the same afternoon: a hub memory - the mistakes list, the self-model of failure
modes - is the source node of a third of the graph model's reflexes, and the conscious layer deposits
it every time an association surfaces it. One such deposit primed forty-two reflexes at once and said
nothing about any of them (five graph fires in forty-five minutes, none on-domain, all primed through
a hub). A source or anchor shared by more than five reflexes now carries no priming; a scar's own
memory does. The disconfirming test, run on the live model after the three steps: a reflex silent just
under its bar fires once its own memory is deposited the way a deliberate recall deposits it, so the
bridge the buffer was built for still carries; only the self-priming and the hub priming are gone.

## Say how confident it is

`reflex_precision.py` runs nightly after the judge pass and turns `judged_nightly.jsonl` into
`reflex_precision.json`: for each reflex, `n` judged fires (domain_at_play yes or no; unclear
abstains) and the smoothed precision `p = (yes + 1) / (n + 2)`, judged at `n >= 4`. The hook
scales a fire's charge by `0.6 + 0.8 p`, so a fire that tends to be right wins the spotlight;
unjudged reflexes and the never-damped safety scars keep their charge. A recognition cue
(`mode: guide`) judged on-domain on fewer than a quarter of its fires stays silent. Danger
scars (`mode: fire`) are ranked down, never silenced. The numbers are only as honest as the
judged rows: a judge that marks every fire on-domain re-inflates the hook.

## The lever that beats any gate: tighten the trigger to its referent

A judged precision under 0.4 at ten or more rows on a danger scar means the trigger fires on
vocabulary, not on the scar's referent. The fix is not a gate but a rewrite tested on the judged
cases: keep every reachable on-domain case, cut the off-domain fires, ship only then. Two things
learned doing it. The hook matches the JSON-serialised tool input, so a line start in that text is
the two characters backslash-n and a word boundary placed before a line-initial token fails; write
line-start tokens as `(?:^|\\n|(?<![A-Za-z0-9_]))token` and test candidates on `json.dumps` of the
input, never on the raw command. And a heredoc is not a verbatim channel in every shell tool; a
regex or an escape that must arrive byte-exact goes through a file written by the editor tool.
Judge a rewrite on fires from after it landed, not on the whole window: `fire_judge_nightly.py --since
"YYYY-MM-DD HH:MM"` draws the sheet from that time on, and rows judged from such a sheet carry `fired_at`,
so a precision table can split the eras. On the first such sample here the two rewritten danger scars
fired on their referents (4 of 5 on-domain) while the remaining noise came from recognition cues keyed on a
file name or a heading word - the next candidates for the same treatment.

Before changing the text form the hook matches (escaped versus real newlines), replay the whole trigger set
over a day of real tool inputs in both forms and read the gained and lost hits per reflex with examples.
Done here on 2,500 inputs: the real-newline form gained 46 hits and lost 230, and the losses were one reflex
whose trigger matched a single backslash followed by a letter - every line break on the escaped input - plus
true positives of triggers written for the escaped form. The form stayed; the trigger was rewritten to the
literal escape it was meant to catch (backslash-backslash-letter on the escaped input, not preceded by a
third backslash so a shell line continuation stays silent). The lesson generalises: a trigger that fires
on most multi-line commands is usually detecting the encoding, not the referent.

## The hint

Each note carries one line on how far to trust it.

- Reflex: `hint: judged on-domain a of n times it fired - a fire is a question, not a
  verdict`, or `hint: unjudged - ... this may or may not apply here`. The block header says
  "a note can point right or wrong; weigh each against what you see, do not obey it";
  recognition cues say "a hint, not an order".
- Association: the memory's own status, read from its frontmatter (`metadata.status`,
  `superseded_by`) with the index tag as the fallback - `verified` ("it may bear on this or
  it may not"), `provisional` ("it can point the wrong way as easily as the right one"),
  `SUPERSEDED by <slug>` ("read that one instead"), or an owner-stated rule ("it binds where
  it applies; check that it applies here and that no later ruling replaced it"). A scar-tagged
  memory says so wherever the tag sits ("a mistake and its rule; check whether the same shape
  is in play"), and a memory with no status tag falls back to its own type from the frontmatter:
  feedback binds if this is the same situation, project is a fact as of its date, reference is a
  pointer, and a memory about the owner asks you to read its date.
- Math note: what kind of thing it is and where it came from - `theorem with a proof text on
  file`, `concept lead`, `harvested draft`, `statement not reviewed against its source`,
  `unverified` - under a header that says a matched result is not evidence the argument uses
  it correctly. Only whole notes are served: a note truncated mid-statement lost its hint.

## Writing notes for the next reader

Give the next reader the hint too: `status:` in the memory's frontmatter, `superseded_by:`
inside the memory a new one replaces (so a sampled excerpt cannot teach the withdrawn
value), the measurement behind a claim, and what would change it. A note without its size
of doubt is the note that gets obeyed.

## Controls

- Same session, same command twice: two fires, then silence; a different session fires fresh
  with its own state file.
- One text matching a gated recognition cue with the gate off and on: `['outside_view']`
  then `[]`.
- A danger scar on a real write: two fires, each with a hint line, under the new header.
- A command matching seven scars shows two and names five more on the cap line; a single-hit
  command shows nothing extra.
- The hook regression gate (`humility_check.py`): 0 core failures.
- `test_reflex_precision_paths.py` (eight cases: the per-path table, suppressed rows kept out of it,
  unclear abstains, the graph gate reads the graph path only and fails open, the prime floor's
  bounds) and `graph_fire/test_graph_priming.py` (three cases: a hub source does not prime, a
  scar's own memory does, nothing fires unprimed under the bar) - plain unittest, no third-party
  modules.

## A rewritten trigger starts its record at the rewrite

A trigger rewritten to its referent is a new trigger, and the judged fires of the old form say nothing about
it. Left in the table they score the new form on a record it never earned: after eleven rewrites in one day,
two rewritten cues read p 0.14 and 0.17 on rows fired entirely before their rewrites, below the gate that
silences a cue. `trigger_rewrites.json` maps a reflex id to the minute its rewrite landed; `reflex_precision.py`
drops that reflex's trigger-path rows fired before it (and undated rows, which predate the record) and reports
`reset_at` and `dropped_before_rewrite` on the entry. The graph path is untouched. Rule: when you rewrite a
trigger, add its id and the commit's minute to the record in the same commit.
