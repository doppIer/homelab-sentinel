# Demo video script (target 2:45, hard limit 3:00)

## Before recording

- `scripts/demo.sh clean`, then `sudo systemctl restart homelab-sentinel` so the Telegram cooldown is empty.
- For the backup scene, poll faster: add `LIFEBOAT_INTERVAL=20` to `.env` and restart sentinel. Put it back to 120 afterwards.
- Screen: terminal on the left, split in two (top: `journalctl -fu homelab-sentinel -o cat`, bottom: a shell in the repo). Telegram desktop on the right.
- Terminal font large enough to read at 1080p. Hide the AWS account id, bot token and chat id; nothing on screen should show `.env`.
- Record each scene separately and cut the waiting (coalesce window 15 s, lifeboat poll). Say in the video that waits were cut.

## Scenes

| Time | Screen | Narration |
|---|---|---|
| 0:00-0:15 | Telegram with one red alert, then the terminal | My home server runs 17 containers and produces events all day. I want Claude on Bedrock to investigate the ones that matter, and nothing else. The one deciding which events matter is a 2-billion-parameter model on a 2014 CPU. |
| 0:15-0:40 | README diagram, then the question and the routing table | Every event goes through Strands Decider 2B with one question: what happened on this server? Six factual answers. Severity is not Decider's job. It lives in this table: outages and failed backups go to Bedrock, crashes that recovered and disk warnings get a short message, planned changes go into a daily digest. |
| 0:40-1:00 | bottom: `scripts/demo.sh restart`; top: the `planned_change` line | I restart a container on purpose. Decider sees a planned change in about three and a half seconds. Route: digest. No Bedrock call, no message on my phone. |
| 1:00-1:40 | `scripts/demo.sh crash`; top: `ongoing_outage`; Telegram: the red alert, zoom on Cause / Evidence / Fix | Now a container that cannot reach its database. Decider says ongoing outage. Only now does the Strands agent call Claude Sonnet 4.6. It reads the container status and logs with read-only tools, quotes the fatal line, and sends me the commands to run. About five seconds and one and a half cents. |
| 1:40-2:10 | `scripts/demo.sh backup`; Telegram: the backup alert | My backups run through restic and systemd. Here a backup fails with an S3 403. First time I tried this, Decider called it routine, because none of my categories said "nothing is down, but data is unprotected". I added that category, data at risk. Now it goes to Bedrock, which reads the backup journal and points at the right credentials file. |
| 2:10-2:30 | `python -m sentinel.report` for the scenario run; then the benchmarks memory table | Over a ten-minute scenario, 5 of 9 events never reached Bedrock: 9 cents instead of 20, and that run had far more failures than a normal day. Over about 40 hours of real traffic before that, nothing happened, so Bedrock was never called and the evening digest skipped it too. Decider runs in int8 with bf16 embeddings, 4 GB instead of 7.7, with the same answers. |
| 2:30-2:45 | `decision_patterns/log_triage/strands_gate.py`, then the repo page | The decision is a reusable pattern: the question, the routing table and a Strands intervention handler that denies the model call before it happens. It's in the repo, link below. |

## Captions to put on screen

Use the numbers from your own recording, not from this file; Decider's confidence varies by a few points between runs.

- restart: `planned_change -> digest | 0 Bedrock calls`
- crash: `ongoing_outage -> escalate | Sonnet 4.6, about 5 s, about 1.5 cents`
- backup: `data_at_risk -> escalate`
- numbers: `10-minute scenario: 5 of 9 events stayed local | $0.087 vs $0.195`
- End card: github.com/doppIer/homelab-sentinel, `#strands-decider-2b` `#policy`
