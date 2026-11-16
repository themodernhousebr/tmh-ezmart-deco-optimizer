"""Bounded ICMP checks of literal node IPs; independent of Deco action sessions."""
from concurrent.futures import ThreadPoolExecutor
import ipaddress
import subprocess
import threading
import time


def ping(address):
    try:
        ip = ipaddress.ip_address(address)
        if ip.is_multicast or ip.is_unspecified or ip.is_loopback or ip.is_link_local or str(ip) == '255.255.255.255':
            return None
        result = subprocess.run(['ping', '-n', '-c', '2', '-W', '2', str(ip)],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=6, check=False)
        return True if result.returncode == 0 else False if result.returncode == 1 else None
    except (ValueError, TypeError, OSError, subprocess.TimeoutExpired):
        return None


class Monitor:
    def __init__(self, stop, interval, check=ping, clock=time.time):
        self.stop, self.interval, self.check, self.clock = stop, interval, check, clock
        self.lock = threading.Lock()
        self.targets, self.results = {}, {}
        self.wake = threading.Event()
        self.thread = threading.Thread(target=self.run, name='ezmart-ip', daemon=True)

    def update(self, targets):
        with self.lock:
            changed = targets != self.targets
            self.targets = dict(list(targets.items())[:64])
            self.results = {key: value for key, value in self.results.items()
                            if key in self.targets and value['ip'] == self.targets[key]}
        if changed:
            self.wake.set()

    def snapshot(self):
        with self.lock:
            return {key: dict(value) for key, value in self.results.items()}

    def cycle(self):
        with self.lock:
            targets = dict(self.targets)
        def probe(item):
            key, address = item
            return key, {'ip': address, 'connected': self.check(address), 'checked_at': self.clock()}
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = dict(pool.map(probe, targets.items()))
        with self.lock:
            self.results.update({key: value for key, value in results.items()
                                 if self.targets.get(key) == value['ip']})

    def run(self):
        due = 0
        while not self.stop.is_set():
            if self.wake.is_set() or self.clock() >= due:
                self.wake.clear()
                self.cycle()
                due = self.clock() + self.interval
            self.stop.wait(1)

    def start(self):
        self.thread.start()
