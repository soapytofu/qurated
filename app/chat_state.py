"""Bounded, short-lived conversation state; account quotas live in SQLite."""
import threading
import time
from collections import OrderedDict


class ChatState:
    def __init__(self, capacity=1000, ttl=3600, clock=time.monotonic):
        self.capacity, self.ttl, self.clock = capacity, ttl, clock
        self.lock = threading.Lock()
        self.entries = OrderedDict()

    def acquire(self, key):
        with self.lock:
            now = self.clock()
            for old in list(self.entries):
                entry = self.entries[old]
                if not entry['busy'] and now - entry['at'] >= self.ttl:
                    del self.entries[old]
            if key in self.entries and self.entries[key]['busy']:
                return None
            if key not in self.entries:
                while len(self.entries) >= self.capacity:
                    victim = next((k for k, v in self.entries.items() if not v['busy']), None)
                    if victim is None:
                        return None
                    del self.entries[victim]
                self.entries[key] = dict(history=[], prior=[], at=now, busy=False)
            entry = self.entries[key]
            entry.update(busy=True, at=now)
            self.entries.move_to_end(key)
            return entry

    def release(self, key):
        with self.lock:
            entry = self.entries.get(key)
            if entry:
                del entry['history'][:-6]
                del entry['prior'][:-3]
                entry.update(busy=False, at=self.clock())
