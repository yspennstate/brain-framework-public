# -*- coding: utf-8 -*-
"""What THIS machine will actually do, measured rather than assumed.

EVERY DEPLOYMENT WRITES THIS FILE ITSELF. It is the one craft module that cannot be shipped:
it is a record of one machine's measured capability, and a number copied from another machine
is worse than no number - it will be quoted in a plan and the plan will be wrong.

The shipped version below is a TEMPLATE with the shape and the discipline, and no figures. Fill
it in from measurements taken here, and update it the moment a measurement contradicts it.

How to take the measurements the nodes below want:

    python tools/verify_tools.py          finds and RUNS each tool; records what actually works
    python fbrun.py --kind render --tool <tool> -- <command>
                                          wraps any render and records its real wall time, so the
                                          cost nodes fill themselves in from work you were doing
                                          anyway rather than from a benchmark nobody re-runs

Two rules this module exists to enforce.

1. AN INSTALLER'S WORD IS NOT A MEASUREMENT. A package manager that reports success can leave no
   executable on disk; `verify_tools.py` finds and runs the binary, and that result is the only
   one worth recording.
2. AN EXTRAPOLATION IS NOT A CAPACITY. "One clip took N minutes, so an hour of film takes 60N" is
   a bound, not a throughput - it assumes no reuse, no failure and no contention. Mark every
   scaled figure UPPER BOUND, in those words, and keep the single measured unit beside it.
"""

DOMAIN = ("craft.this_machine", "this machine",
          "Measured capability of the machine we actually work on.")

NODES = [
    ("machine.gpu", "the GPU: fill in model and memory",
     "Record what the card IS and what it has DONE here, not what its specification claims. Note "
     "any environment setting that hides it from a process (a visibility mask set for one user "
     "persists and is not a hardware diagnosis), the driver version and the date it was installed, "
     "and whether a representative workload has been stable SINCE that install. One job at a time "
     "on the card, including the encoder, and no duplicate model servers.",
     [("part_of", "craft.this_machine")],
     ["machine", "gpu", "template"]),

    ("machine.cpu", "the CPU: fill in cores and what they are shared with",
     "Cores, and how many of them are actually available to a render - a machine that is also "
     "someone's desktop has fewer. Record the priority class background work runs at and whether "
     "the scheduler honours it here.",
     [("part_of", "craft.this_machine")],
     ["machine", "cpu", "template"]),

    ("machine.disk", "the disk: fill in free space and what a render costs it",
     "Free space now, and the working-set size of one finished minute of film. Rendering fails "
     "late and expensively when the disk fills mid-encode; a render queue that does not check "
     "free space before starting will eventually prove that.",
     [("part_of", "craft.this_machine")],
     ["machine", "disk", "template"]),

    ("machine.render_cost", "what one unit of output actually costs here",
     "One MEASURED unit - a frame, a clip, a minute of encode - with the command that produced it "
     "and the wall time it took, taken by fbrun so the record is the run and not a memory. Any "
     "figure scaled up from it is an UPPER BOUND and says so.",
     [("part_of", "craft.this_machine"), ("measured_by", "metric.wall_time")],
     ["machine", "cost", "measured", "template"]),

    ("machine.toolchain", "which tools are installed AND verified to run",
     "The output of tools/verify_tools.py, not a list of things someone installed. A tool that is "
     "on disk and will not start is worse than an absent one, because a plan will name it.",
     [("part_of", "craft.this_machine")],
     ["machine", "tools", "template"]),
]

SUPPORT = [
    ("metric.wall_time", "metric", "wall time",
     "Elapsed real time for one unit of work, recorded by the run wrapper."),
]
