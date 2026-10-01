# Batch checking

For a large audit, groups of claims can be split up **if** the user or the active instructions explicitly allow subagents. Use the tools the environment actually provides; this file does not authorize delegation on its own.

1. The lead agent creates `audit.json` and unique claim IDs. It gives each worker a separate set of IDs and the fixed paragraph numbering. It never lets two workers edit the main file at the same time.
2. Each worker returns a separate JSON file with `claims`, `sources`, and `searches`. It describes the exact sources it checked and their limitations, and it does not accept another agent's conclusions without review.
3. Before running `merge-batch`, the lead agent reviews the batches for conflicting verdicts, duplicate sources, and ID collisions. The command merges by **ID**, not by meaning; fix colliding IDs beforehand.
4. After each merge, run `validate` and review the evidence and the two-way links. Completeness and the final verdicts are the lead agent's responsibility.

```bash
python "<skill-root>/scripts/audit_tool.py" merge-batch "<audit-dir>/audit.json" --batch "<audit-dir>/research/batch_01.json"
```
