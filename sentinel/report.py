import argparse
import json
import os
import statistics
from collections import Counter
from datetime import datetime, timezone

PRICE_IN = 3.30 / 1_000_000
PRICE_OUT = 16.50 / 1_000_000
DEMO_SUBJECTS = ("sentinel-demo", "demo")
DEFAULT_SINCE = "2026-10-20T17:28:00+00:00"


def load(path, since):
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


def is_demo(row):
    return str(row.get("subject", "")).startswith(DEMO_SUBJECTS)


def pct(values, q):
    if not values:
        return 0.0
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]


def table(title, counter, total):
    lines = [f"| {title} | events | share |", "|---|---|---|"]
    for key, n in counter.most_common():
        lines.append(f"| {key} | {n} | {n / total:.0%} |")
    return "\n".join(lines)


def cost_of(rows):
    analyses = [r for r in rows if r.get("bedrock_input_tokens")]
    tokens_in = sum(r["bedrock_input_tokens"] for r in analyses)
    tokens_out = sum(r["bedrock_output_tokens"] for r in analyses)
    return analyses, tokens_in, tokens_out, tokens_in * PRICE_IN + tokens_out * PRICE_OUT


def summarize(rows, label, reference):
    n = len(rows)
    if n == 0:
        return f"### {label}\n\nNo events.\n"
    analyses, tokens_in, tokens_out, cost = cost_of(rows)
    everything = reference * n
    latencies = [r["latency_ms"] / 1000 for r in rows if r.get("latency_ms")]
    first = datetime.fromtimestamp(min(r["ts"] for r in rows), tz=timezone.utc)
    last = datetime.fromtimestamp(max(r["ts"] for r in rows), tz=timezone.utc)
    hours = max((last - first).total_seconds() / 3600, 1 / 60)

    out = [
        f"### {label}",
        "",
        f"Window: {first:%Y-%m-%d %H:%M} to {last:%Y-%m-%d %H:%M} UTC ({hours:.1f} h), {n} events.",
        "",
        "| | |",
        "|---|---|",
        f"| Events handled by Decider alone (no Bedrock call) | {n - len(analyses)} of {n} ({(n - len(analyses)) / n:.0%}) |",
        f"| Bedrock analyses | {len(analyses)} |",
        f"| Bedrock tokens in / out | {tokens_in:,} / {tokens_out:,} |",
        f"| Bedrock cost, actual | ${cost:.4f} |",
        f"| Cost if every event went to Bedrock (at ${reference:.4f} per analysis) | ${everything:.4f} |",
        f"| Decider latency median / p95 | {statistics.median(latencies):.1f} s / {pct(latencies, 0.95):.1f} s |",
        "",
        table("Source", Counter(r.get("source") for r in rows), n),
        "",
        table("Decider choice", Counter(r.get("choice") for r in rows), n),
        "",
        table("Route", Counter(r.get("route") for r in rows), n),
        "",
    ]
    escalations = [r for r in rows if r.get("route") == "escalate"]
    if escalations:
        out += ["Escalations:", ""]
        for r in escalations:
            stamp = datetime.fromtimestamp(r["ts"], tz=timezone.utc).strftime("%m-%d %H:%M")
            out.append(f"- {stamp} {r.get('source')}/{r.get('subject')} {r.get('choice')} ({r.get('confidence', 0):.2f}): {r.get('reason')}")
        out.append("")
    return "\n".join(out)


def detail(rows):
    out = ["### Every event", ""]
    for r in sorted(rows, key=lambda r: r["ts"]):
        stamp = datetime.fromtimestamp(r["ts"], tz=timezone.utc).strftime("%H:%M:%S")
        probs = r.get("probabilities") or {}
        top = sorted(probs, key=probs.get, reverse=True)[:2]
        ranked = ", ".join(f"{k} {probs[k]:.2f}" for k in top)
        tokens = f" bedrock {r['bedrock_input_tokens']}/{r['bedrock_output_tokens']}" if r.get("bedrock_input_tokens") else ""
        out.append(f"- {stamp} {r.get('source')}/{r.get('subject')} -> {r.get('route')} ({ranked}){tokens}")
        out.append(f"  state: {str(r.get('state', ''))[:240]}")
        if r.get("analysis"):
            out.append(f"  analysis: {r['analysis'][:400]}".replace("\n", " | "))
    return "\n".join(out) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", default=os.getenv("DECISION_LOG", "logs/decisions.jsonl"))
    parser.add_argument("--since", default=DEFAULT_SINCE)
    parser.add_argument("--detail", action="store_true")
    args = parser.parse_args()
    since = datetime.fromisoformat(args.since).timestamp()
    rows = load(args.log, since)
    real = [r for r in rows if not is_demo(r)]
    demo = [r for r in rows if is_demo(r)]
    print(f"## Live traffic report (since {args.since})\n")
    analyses, _, _, cost = cost_of(rows)
    reference = cost / len(analyses) if analyses else 0.0145
    print(summarize(rows, "All events", reference))
    print(summarize(real, "Real containers and backups (demo scenarios excluded)", reference))
    print(summarize(demo, "Demo scenarios", reference))
    if args.detail:
        print(detail(rows))


if __name__ == "__main__":
    main()
