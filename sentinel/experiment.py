import json
import os
import sys
import time
import urllib.request

from dotenv import load_dotenv

from decision_patterns.log_triage.pattern import CRITERIA, QUESTION

EXPECTED = {
    "disk_raw": "action_needed",
    "disk_enriched": "action_needed",
    "docker_restart_raw": "noise/info",
    "docker_restart_summary": "noise/info",
    "docker_oom_crashloop": "critical",
    "docker_app_down": "critical",
    "docker_unhealthy": "action_needed/critical",
}

NOUL = {
    "failure": "Did something fail or break, as opposed to a planned or routine operation?",
    "down": "Is a service currently down, unhealthy or unable to serve its users?",
    "imminent": "Will this cause an outage or data loss within the next few hours if nobody acts?",
    "human": "Does a human need to act within 24 hours?",
}


def questions():
    q = {
        "choice": {"type": "choice", "instructions": QUESTION, "criteria": CRITERIA},
        "score": {"type": "score", "instructions": QUESTION, "criteria": list(CRITERIA)},
    }
    for key, text in NOUL.items():
        q[key] = {"type": "noul", "instructions": text}
    return q


def ask(url, state, qs):
    body = json.dumps({"state": state, "questions": qs}).encode()
    req = urllib.request.Request(url, data=body, headers={"content-type": "application/json"})
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.load(r)
    data.setdefault("latency_ms", (time.perf_counter() - started) * 1000)
    return data


def main(paths):
    load_dotenv(".env")
    url = os.getenv("DECIDER_URL", "http://127.0.0.1:8765").rstrip("/") + "/v1/systemone"
    qs = questions()
    rows = []
    for path in paths:
        name = os.path.basename(path).removesuffix(".json")
        state = json.load(open(path))["state"]
        data = ask(url, state, qs)
        a = data["answers"]
        rows.append({"fixture": name, "expected": EXPECTED.get(name, "?"), "latency_ms": data["latency_ms"], "answers": a})
        c = a["choice"]
        s = a["score"]
        noul = " ".join(f"{k}={a[k]['noul']:.2f}" for k in NOUL)
        score_view = {k: v for k, v in s.items() if k != "type" and not isinstance(v, dict)}
        print(f"\n{name}  (expected {EXPECTED.get(name, '?')})  {data['latency_ms']:.0f}ms for {len(qs)} questions")
        print(f"  choice: {c['choice']} conf={c.get('confidence', 0):.2f}")
        print(f"  score:  {score_view}")
        print(f"  noul:   {noul}")
    os.makedirs("logs", exist_ok=True)
    out = f"logs/experiment-{int(time.time())}.json"
    json.dump(rows, open(out, "w"), indent=1)
    print(f"\nraw answers -> {out}")


if __name__ == "__main__":
    main(sys.argv[1:])
