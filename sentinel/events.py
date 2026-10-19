import time
from dataclasses import dataclass, field


@dataclass
class Event:
    source: str
    subject: str
    kind: str
    state: str
    raw: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
