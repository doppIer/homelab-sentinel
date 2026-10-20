import time
from collections import deque

from strands.interventions import Deny, InterventionHandler, Proceed


class BedrockBudget(InterventionHandler):
    name = "bedrock-budget"

    def __init__(self, max_per_hour=20, clock=time.time):
        self.max_per_hour = max_per_hour
        self.clock = clock
        self.calls = deque()

    def used(self):
        now = self.clock()
        while self.calls and now - self.calls[0] > 3600:
            self.calls.popleft()
        return len(self.calls)

    def before_model_call(self, event, **kwargs):
        if self.used() >= self.max_per_hour:
            return Deny(reason=f"bedrock budget reached: {self.max_per_hour} model calls in the last hour")
        self.calls.append(self.clock())
        return Proceed()


class KnownContainers(InterventionHandler):
    name = "known-containers"

    def __init__(self, list_names):
        self.list_names = list_names

    def before_tool_call(self, event, **kwargs):
        container = (event.tool_use.get("input") or {}).get("container")
        if container is None:
            return Proceed()
        names = self.list_names()
        if container in names:
            return Proceed()
        return Deny(reason=f"no container named {container!r}. Existing containers: {', '.join(sorted(names))}")
