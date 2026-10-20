from decision_patterns.log_triage.pattern import CRITERIA, QUESTION, ROUTES, LogTriage, Triage

__all__ = ["CRITERIA", "QUESTION", "ROUTES", "LogTriage", "Triage", "LogTriageGate"]


def __getattr__(name):
    if name == "LogTriageGate":
        from decision_patterns.log_triage.strands_gate import LogTriageGate

        return LogTriageGate
    raise AttributeError(name)
