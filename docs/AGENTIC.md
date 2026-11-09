# Running agents on their own with this framework

This repository is the machinery. What makes it useful is an agent that keeps working for hours
or days, reading and writing the brain as it goes. This page says which parts of the framework do
that work and where the practical guide lives.

## Use a terminal agent

The hooks, the mesh and the dream all run as programs on your own computer, so the agent has to
run there too. A terminal agent (Claude Code or Codex in a terminal window) can run the hooks on
every prompt, read and write files, and keep a schedule. A chat window in a browser cannot.

Installation steps for Windows, macOS and Linux, and what to do if you have never opened a
terminal, are in the math brain's guide:
[docs/agentic/terminals.md](https://github.com/yspennstate/math-brain-public/blob/main/docs/agentic/terminals.md).
An agent you already have can also do the installation for you if you ask it.

## Give it a loop

An agent stops when it thinks it has answered. A loop prompt gives it a clock instead, for example:

```
/loop fire this every 5 minutes for the next 10 days. keep working on <the project>. read
NEXT.md first, do the next undone step, save the result, and rewrite the first line of NEXT.md.
```

Every five minutes the same prompt arrives again. The brain is what makes the repeat useful: the
hooks hand the agent what it learned in earlier rounds, and the NEXT line tells it where it stopped.
More examples and the reasons behind each part are in
[docs/agentic/loop-prompts.md](https://github.com/yspennstate/math-brain-public/blob/main/docs/agentic/loop-prompts.md).

## Which parts of the framework a long-running agent leans on

- `hooks/` and `dreaming/`: the session-start and per-prompt hooks give each round its memories,
  reflexes and open tasks, so round fifty knows what round one learned. The dream consolidates
  overnight.
- `mesh/`: when several agents loop at once, claims on files and repositories keep two of them
  from editing the same thing. Claim before you write; stop if the claim is refused.
- `fleet/`: relaunching an agent in the same session after a crash, so a loop survives a reboot.
- `governor/`: a state file that tells every agent when the machine is short of memory or disk, so
  a loop does not start something large at the wrong moment.
- `resilience/` and the compaction notes in [COMPACTION.md](COMPACTION.md): what an agent keeps
  when its context fills up halfway through a long job.

## More

- Starter prompts for a first session:
  [docs/agentic/starter-prompts.md](https://github.com/yspennstate/math-brain-public/blob/main/docs/agentic/starter-prompts.md)
- Skills an agent can load to learn these habits:
  [agent_tools/skills/](https://github.com/yspennstate/math-brain-public/tree/main/agent_tools/skills).
  The loop-writer skill turns a request like "keep working on this while I'm away" into a complete
  loop prompt:
  [loop-writer/SKILL.md](https://github.com/yspennstate/math-brain-public/blob/main/agent_tools/skills/loop-writer/SKILL.md)
- Short answers to the questions people ask first (cost, how long a loop runs, what happens when
  the context fills up):
  [docs/agentic/faq.md](https://github.com/yspennstate/math-brain-public/blob/main/docs/agentic/faq.md)
- A dated list of suggestions for prompts and loops, newest first:
  [docs/agentic/SUGGESTIONS.md](https://github.com/yspennstate/math-brain-public/blob/main/docs/agentic/SUGGESTIONS.md)
- When the brain seems to do nothing:
  [docs/agentic/struggling.md](https://github.com/yspennstate/math-brain-public/blob/main/docs/agentic/struggling.md)
