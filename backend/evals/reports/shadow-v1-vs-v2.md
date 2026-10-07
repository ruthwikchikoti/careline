# Shadow comparison — candidate release vs incumbent

*When:* 2026-10-07T16:53:48+05:30
*Set:* the full 250-item eval set, keyless deterministic slice

| Metric | A: red_flags@v1 (regex only) | B: active (red_flags@v2+f8d738e5005a) | Better |
|---|---|---|---|
| Emergency recall | 0.033 | 1.000 | **B** |
| Missed emergencies | 58 | 0 | **B** |
| Cross-patient leaks | 0 | 0 | = |
| Superseded leaks | 0 | 0 | = |
| Injection answered | 0 | 0 | = |
| Out-of-scope redirect acc. | 1.000 | 1.000 | = |
| No-answer accuracy | 0.989 | 0.989 | = |
| Over-escalation rate | 0.071 | 0.071 | = |
| Latency p50 (ms) | 0.075 | 0.877 | A |
| Latency p99 (ms) | 0.149 | 1.706 | A |

*In-scope answer accuracy (informational, keyless): A=0.186 B=0.186*

Verdict: promote B iff every safety metric is B-or-equal and the
eval gate passes on B — see evals/reports/ for the gate run.
