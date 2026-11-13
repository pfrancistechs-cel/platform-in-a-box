"""Steady background traffic: the users of the platform. RATE requests per second to TARGET."""
import os
import threading
import time
import urllib.request

TARGET = os.environ.get("TARGET", "http://router:8080/")
RATE = float(os.environ.get("RATE", "20"))
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def one():
    try:
        OPENER.open(TARGET, timeout=4).read()
    except Exception:
        pass


def main():
    interval, nxt = 1.0 / RATE, time.time()
    while True:
        threading.Thread(target=one, daemon=True).start()
        nxt += interval
        time.sleep(max(0.0, nxt - time.time()))


if __name__ == "__main__":
    main()
