import argparse
import json
import os
import time
from collections import Counter

from dotenv import load_dotenv

from sentinel.notify import Telegram

SYSTEM = """You write the evening report for a single-person homelab (Ubuntu server, Docker).
You get the day's events after a local model already triaged them.
Write plain text for Telegram: no markdown, no code fences.
At most 1200 characters. Start with one line that says whether the day was quiet, bumpy or bad.
Then group related events, mention anything that repeated, and end with at most three things worth checking tomorrow.
Do not invent events or causes that are not in the list."""

ROUTE_ORDER = ["escalate", "notify", "digest", "drop"]


def load_rows(path, since):
    if not os.path.exists(path):
        return []
    rows = []
    with open(path) as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("ts", 0) >= since:
                rows.append(row)
    return rows


def stats(rows):
    routes = Counter(r.get("route") for r in rows)
    bedrock_calls = sum(1 for r in rows if r.get("bedrock_input_tokens"))
    tokens_in = sum(r.get("bedrock_input_tokens", 0) or 0 for r in rows)
    tokens_out = sum(r.get("bedrock_output_tokens", 0) or 0 for r in rows)
    return {
        "events": len(rows),
        "routes": {k: routes.get(k, 0) for k in ROUTE_ORDER},
        "bedrock_calls": bedrock_calls,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
    }


def event_lines(rows, limit=60):
    lines = []
    for r in sorted(rows, key=lambda r: (ROUTE_ORDER.index(r.get("route", "drop")) if r.get("route") in ROUTE_ORDER else 9, r.get("ts", 0))):
        if r.get("route") == "drop":
            continue
        stamp = time.strftime("%H:%M", time.localtime(r.get("ts", 0)))
        line = f"{stamp} [{r.get('route')}] {r.get('source')}/{r.get('subject')} {r.get('choice')} ({r.get('confidence', 0):.2f}): {r.get('state', '')[:220]}"
        if r.get("analysis"):
            cause = next((l for l in r["analysis"].splitlines() if l.lower().startswith("cause")), "")
            if cause:
                line += f" | analysis {cause[:160]}"
        lines.append(line)
    if len(lines) > limit:
        lines = lines[:limit] + [f"... and {len(lines) - limit} more"]
    return lines


def prompt(rows, s, hours):
    head = (
        f"Last {hours} hours: {s['events']} events. "
        f"Routes: escalate={s['routes']['escalate']} notify={s['routes']['notify']} "
        f"digest={s['routes']['digest']} drop={s['routes']['drop']}. "
        f"Bedrock analyses: {s['bedrock_calls']}."
    )
    return head + "\n\nEvents (dropped ones omitted):\n" + "\n".join(event_lines(rows))


def footer(s, model_id):
    return (
        f"\n\n— {s['events']} events, {s['routes']['escalate']} sent to Bedrock for analysis, "
        f"{s['routes']['drop'] + s['routes']['digest']} handled locally by Decider. "
        f"Summary by {model_id.split('.')[-1].split('-v')[0]}."
    )


def summarize(text, model_id, region, client=None):
    if client is None:
        import boto3

        client = boto3.client("bedrock-runtime", region_name=region)
    response = client.converse(
        modelId=model_id,
        system=[{"text": SYSTEM}],
        messages=[{"role": "user", "content": [{"text": text}]}],
        inferenceConfig={"maxTokens": 600, "temperature": 0.2},
    )
    usage = response.get("usage", {})
    return response["output"]["message"]["content"][0]["text"].strip(), usage.get("inputTokens", 0), usage.get("outputTokens", 0)


def build(rows, hours, model_id, region, client=None):
    s = stats(rows)
    if s["events"] == 0 or s["events"] == s["routes"]["drop"]:
        return f"🌙 Daily digest: quiet day, {s['events']} events in the last {hours} hours and nothing worth reading.", 0, 0
    body, tokens_in, tokens_out = summarize(prompt(rows, s, hours), model_id, region, client)
    return "🌙 Daily digest\n\n" + body + footer(s, model_id), tokens_in, tokens_out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_dotenv(".env")
    path = os.getenv("DECISION_LOG", "logs/decisions.jsonl")
    model_id = os.getenv("BEDROCK_SUMMARY_MODEL", "eu.anthropic.claude-haiku-4-5-20251001-v1:0")
    rows = load_rows(path, time.time() - args.hours * 3600)
    text, tokens_in, tokens_out = build(rows, args.hours, model_id, os.getenv("AWS_REGION", "eu-north-1"))
    print(text)
    print(f"\n[digest] events={len(rows)} tokens in={tokens_in} out={tokens_out}", flush=True)
    if args.dry_run:
        return
    bot = Telegram.from_env()
    if bot:
        bot.post(text)


if __name__ == "__main__":
    main()
