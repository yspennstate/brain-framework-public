# V2 independent-case power check

The twenty repeated pair runs are measurements within a case cluster, not twenty independent
units. `plan_power_v2` therefore works at the case level. A case is modelled as an improvement
`+1`, a harm `-1`, or a tie `0`; `true_delta` is the expected case effect and `discordance` is
the probability of a non-tied case. This is a deliberately coarse sign model, not a claim that
real case deltas have unit magnitude.

The planner exactly enumerates four gates from the joint improvement/harm/tie counts:

- observed mean case delta at least 0.04;
- at least twelve strictly positive cases;
- positive median case delta; and
- exact one-sided case sign-flip p-value at most 0.05.

It reports their exact joint probability. The interval-lower-bound gate is a clearly labelled
normal approximation using the independent-case variance. The final modelled envelope is the
smaller of that approximation and the exact structural joint probability. The planner still
does not model the separate faulty-case gate suite, clean specificity, stratum regressions,
runtime failures, verifier failures, or isolation failures, so it cannot certify a study.

When every case is non-tied (`discordance = 1`), representative scenarios are:

| Independent cases | True delta | Mean gate | Positive-case gate | Median gate | Sign-flip gate | Interval approximation | Exact structural joint | Modelled envelope |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 40 | 0.04 | 0.539 | 0.999 | 0.539 | 0.068 | 0.044 | 0.068 | 0.044 |
| 40 | 0.06 | 0.589 | 0.999 | 0.589 | 0.086 | 0.057 | 0.086 | 0.057 |
| 40 | 0.08 | 0.638 | 0.999 | 0.638 | 0.107 | 0.073 | 0.107 | 0.073 |
| 80 | 0.08 | 0.649 | 1.000 | 0.728 | 0.167 | 0.107 | 0.167 | 0.107 |
| 128 | 0.08 | 0.679 | 1.000 | 0.794 | 0.219 | 0.146 | 0.219 | 0.146 |
| 256 | 0.08 | 0.724 | 1.000 | 0.889 | 0.342 | 0.250 | 0.342 | 0.250 |

The table reverses an earlier, pseudoreplicated conclusion that forty cases were a generally
well-powered floor. Under this conservative case-sign model, forty independent cases have low
power for a six- or eight-point effect once the sign-flip and interval gates are included.
Repeated runs can estimate each case effect more precisely, but they cannot manufacture more
independent cases.

The four-point mean gate is an observed-effect threshold, not a power target. If the true mean
effect is exactly 0.04, the observed mean clears its own expectation only about half the time.
Increasing the case count narrows uncertainty but does not turn that equality into an 80%-power
alternative. Planning for high power requires a larger true effect, a precommitted weaker gate,
or many more genuinely independent cases.

Case-effect magnitude and sign probabilities must be estimated only from public development or
calibration data. No final-holdout outcome may choose the case count, discordance, or threshold;
changing any of them after the final seed commitment burns the study.
