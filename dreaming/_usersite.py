"""_usersite.py - re-add the Roaming user-site under `python -s` (import side effect, idempotent).

Why: hook processes start with `python -s` to skip the user-site scan that dominates interpreter
startup on this machine (measured 2026-07-09: 364ms -> 110ms best-of-4; the system sitecustomize -
including the ib_trade_gate_patch safety perimeter - still runs, only -S would skip it). But numpy
lives IN the Roaming user-site, so any consumer that needs it imports this shim first. Appending
(not prepending) is a deliberate simplification: the only package hooks pull from user-site is
numpy, which exists nowhere else, so precedence cannot flip anything; a future dual-installed
package would need the stock ordering instead.
"""
import os
import sys

_p = os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "Python",
                  "Python%d%d" % sys.version_info[:2], "site-packages")
if os.path.isdir(_p) and _p not in sys.path:
    sys.path.append(_p)
