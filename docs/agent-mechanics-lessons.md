# Two lessons about checks that report success while broken

Both come from agents running long loops. In each case the check printed what a healthy run prints, and nobody
noticed for hours.

## 1. Withdrawing an output does not withdraw its parts

A pipeline built finished outputs from a shared bank of parts: each new build referred to parts by path, and a part
made for one build could be reused, byte for byte, in many later ones. When one finished output was withdrawn because
of a bad part, the bank kept supplying the same part. A scan of every build plan found it in twelve more published
outputs.

What to do when a part is pulled:

- Scan every build plan and every published output for that part, and confirm each match by content hash, not by
  file name or folder.
- Ban the part by its hash as well as its path, so a copy under another name is still caught.
- Hash every candidate at build time. A cheap pre-filter, such as comparing file sizes against the banned originals,
  quietly turns itself off once an original is deleted, and then nothing is filtered at all.

## 2. A filter on a script's output hides that the script failed

A loop checked for new replies every few minutes with a command of this shape:

    python -s check_inbox.py > out.txt 2>&1; grep -i "re:" out.txt

The `-s` flag drops the user site-packages, where the mail client library was installed, so every run died on
import. The traceback went into `out.txt`, the `grep` found no matching line, and the loop read the silence as "no new
mail" for hours.

What to do:

- A check whose failure looks the same as "nothing found" will report nothing found for as long as it is broken.
  Make it print something that differs: the exit code, a line count, or a heartbeat line the script writes on success.
- After any change to the command, run it once and read the whole output by eye, against a case where the answer is
  known to be yes.
- Do not pass `-s` (or `-I`) to a script that needs packages from the user site.
