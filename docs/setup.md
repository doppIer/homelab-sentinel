# Setup

Tested on Ubuntu with Docker, Python 3.13 and 3.14, CPU only.

## What you need

- Linux with Docker. The user running sentinel must be able to run `docker` (member of the `docker` group).
- About 8 GB of free RAM while Decider loads (peak 7.5 GB with int8 + bf16 embeddings, 4.0 GB resident after). The fp32 default needs about 10 GB at load and 7.7 GB after.
- An AWS account with Amazon Bedrock access to Claude Sonnet 4.6 (analysis) and Claude Haiku 4.5 (daily digest) in your region.
- Optional: a Telegram bot, Netdata on `:19999`, [homelab-lifeboat](https://github.com/doppIer/homelab-lifeboat) backups.

## 1. Install

```bash
git clone https://github.com/doppIer/homelab-sentinel && cd homelab-sentinel
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests     # no AWS, no GPU, no Decider needed
```

`requirements.txt` pins the CPU build of torch. On a GPU machine, drop the `--extra-index-url` line and the `+cpu` suffix.

## 2. Run Decider 2B

The first start downloads `StrandsAgents/strands-decider-2B-qwen3.5-v1-2610` from Hugging Face.

```bash
python scripts/decider_int8.py 8765 --embed-bf16
curl -s localhost:8765/v1/systemone -H 'content-type: application/json' -d '{"state":"source=docker container=web action=restart stop=clean outcome=running_again meaning=restarted on request","questions":{"what":{"type":"choice","instructions":"What happened on this server?","criteria":{"planned_change":"an operator restarted something","ongoing_outage":"a service is down right now"}}}}'
```

Run only one copy. A second fp32 copy on a 16 GB machine swap-thrashed my server until a power cycle. The systemd unit in `deploy/strands-decider-int8.service` caps memory (`MemoryMax=9G`, `MemorySwapMax=0`), so Decider is killed instead of freezing the host.

## 3. Bedrock access

1. In the Bedrock console, open the model catalog and request access to Claude Sonnet 4.6 and Claude Haiku 4.5. Anthropic models ask for a short use-case form once per account.
2. The first invocation of an Anthropic model subscribes the account through AWS Marketplace. If your IAM user is locked down, give it `aws-marketplace:Subscribe` and `aws-marketplace:ViewSubscriptions` for that one call, then remove them.
3. Create an IAM user (or role) that can only invoke models:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
      "Resource": [
        "arn:aws:bedrock:*:*:inference-profile/eu.anthropic.claude-sonnet-4-6",
        "arn:aws:bedrock:*:*:inference-profile/eu.anthropic.claude-haiku-4-5-20251001-v1:0",
        "arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-4-6",
        "arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0"
      ]
    }
  ]
}
```

Outside the EU, use the `us.` or `global.` inference profile ids instead of `eu.`.

4. Check it: `python -m sentinel.check_bedrock`.

## 4. Configure

```bash
cp .env.example .env
```

| Variable | Meaning |
|---|---|
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` | the Bedrock-only IAM user from step 3 |
| `BEDROCK_ANALYSIS_MODEL` | outage analysis; leave empty to run Decider-only |
| `BEDROCK_SUMMARY_MODEL` | daily digest |
| `MAX_BEDROCK_CALLS_PER_HOUR` | hard cap enforced by `BedrockBudget` (default 20) |
| `UNSURE_BELOW`, `OUTAGE_RISK` | the two thresholds of the routing policy (0.5, 0.25) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | optional; without them decisions only go to the console and the log |
| `NETDATA_URL`, `LIFEBOAT_INTERVAL`, `COALESCE_SECONDS` | collectors |

Telegram: create a bot with @BotFather, put the token in `.env`, send your bot any message, then `python -m sentinel.notify` prints your chat id and `python -m sentinel.notify test` sends a test message.

`.env` is in `.gitignore`. Keep it that way.

## 5. Run

```bash
python -m sentinel.main                    # all collectors
python -m sentinel.main --no-netdata       # without Netdata
python -m sentinel.main --no-bedrock       # Decider only, nothing leaves the machine
```

Every decision is appended to `logs/decisions.jsonl`. `python -m sentinel.report --since 2026-10-22T00:00:00+00:00` turns it into a traffic and cost report.

## 6. Run it as a service

The units in `deploy/` assume user `h4n` and `/home/h4n/homelab-sentinel`. Edit `User=`, `WorkingDirectory=` and `ExecStart=` first.

```bash
sudo cp deploy/strands-decider-int8.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now strands-decider-int8
scripts/install.sh       # homelab-sentinel.service and the 21:00 digest timer
```

The lifeboat collector only reads `systemctl show`, so sentinel needs neither root nor the restic password.

## 7. See it work

```bash
scripts/demo.sh restart    # planned change: digest, no Bedrock call
scripts/demo.sh crash      # a container that cannot reach its database: Bedrock analysis on Telegram
scripts/demo.sh oom        # a container over its 64 MB limit
scripts/demo.sh backup     # a restic run that fails with S3 403 (needs sudo for systemd-run)
scripts/demo.sh clean
scripts/scenario.sh        # 10 minutes: planned operations plus the three failures, then the report
```
