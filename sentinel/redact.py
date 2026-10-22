import re

KEY_WORDS = r"token|secret|passw(?:or)?d|pass|pwd|api[_-]?key|access[_-]?key|private[_-]?key|credentials?|authorization"
SECRET = re.compile(rf"""(?i)([\w.-]*(?:{KEY_WORDS})[\w.-]*["']?)(\s*[=:]\s*)("[^"]*"|'[^']*'|[^\s,;&}}\]]+)""")
BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+")
URL_CREDENTIALS = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^\s:/@]+:)([^\s@/]+)(@)")
KNOWN_TOKENS = re.compile(
    r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"
    r"|\bgh[pousr]_[A-Za-z0-9]{30,}\b"
    r"|\bsk-[A-Za-z0-9_-]{20,}\b"
    r"|\bxox[abprs]-[A-Za-z0-9-]{10,}\b"
    r"|\b\d{8,10}:[A-Za-z0-9_-]{35}\b"
)


def redact(text):
    text = BEARER.sub(lambda m: f"{m.group(1)} ***", text)
    text = URL_CREDENTIALS.sub(r"\1***\3", text)
    text = KNOWN_TOKENS.sub("***", text)
    return SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)
