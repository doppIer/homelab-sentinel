import json
import os
import sys

from dotenv import load_dotenv

from decision_patterns.log_triage import LogTriage


def main(paths):
    load_dotenv(".env")
    triage = LogTriage(
        os.getenv("DECIDER_URL", "http://127.0.0.1:8765"),
        escalate_below=float(os.getenv("ESCALATE_BELOW", "0.5")),
        risk_mass=float(os.getenv("RISK_MASS", "0.35")),
    )
    print(f"{'fixture':<32} {'choice':<14} {'conf':>5} {'route':<9} {'ms':>6}  probabilities")
    for path in paths:
        state = json.load(open(path))["state"]
        r = triage(state)
        probs = " ".join(f"{k}={v:.2f}" for k, v in r.probabilities.items())
        name = os.path.basename(path).removesuffix(".json")
        print(f"{name:<32} {r.choice:<14} {r.confidence:>5.2f} {r.route:<9} {r.latency_ms:>6.0f}  {probs}")


if __name__ == "__main__":
    main(sys.argv[1:])
