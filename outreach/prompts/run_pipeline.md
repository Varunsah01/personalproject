Read GUARDRAILS.md and outreach/CLAUDE.md before doing anything.

Check if outreach/STOP exists. If it does, stop immediately and report that the pipeline is paused.

Run the full outreach pipeline:

```bash
python outreach/pipeline.py --stage all
```

After it completes, summarise the results:
- How many rows each stage processed
- Any errors or stages that were halted
- Current tracker.csv status counts (research_done, people_found, contact_found, drafted, queued)

If any stage halted due to error rate, flag it clearly.
