"""Manual e2e check for the wallet + auth endpoints (not part of the pytest suite)."""
import sys
import time

import requests

API = "http://localhost:8001/api"
DEV = f"wdev_manual_{int(time.time())}"


def p(label, r):
    print(f"{label}: {r.status_code} {r.text[:180]}")
    return r


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "off"

    p("wallet balance (new device)", requests.get(f"{API}/wallet/balance", params={"device_id": DEV}))
    p("topup -5 (invalid)", requests.post(f"{API}/wallet/topup", json={"device_id": DEV, "amount_usd": -5}))
    p("topup +5", requests.post(f"{API}/wallet/topup", json={"device_id": DEV, "amount_usd": 5}))
    p("balance after topup", requests.get(f"{API}/wallet/balance", params={"device_id": DEV}))

    p("auth/me no token", requests.get(f"{API}/auth/me"))
    p("auth/me bad token", requests.get(f"{API}/auth/me", headers={"Authorization": "Bearer nope"}))
    p("otp request garbage", requests.post(f"{API}/auth/otp/request", json={"identifier": "nope"}))

    ident = f"user{int(time.time())}@example.com"
    r = p("otp request", requests.post(f"{API}/auth/otp/request", json={"identifier": ident, "device_id": DEV}))
    code = r.json()["debug_otp"]
    p("otp verify wrong", requests.post(f"{API}/auth/otp/verify", json={"identifier": ident, "otp": "000000"}))
    r = p("otp verify right", requests.post(f"{API}/auth/otp/verify",
                                           json={"identifier": ident, "otp": code, "device_id": DEV}))
    token = r.json()["token"]
    uid = r.json()["user"]["id"]
    p("auth/me with token", requests.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {token}"}))
    p("linked user wallet", requests.get(f"{API}/wallet/balance", params={"device_id": f"user:{uid}"}))
    p("old device wallet zeroed", requests.get(f"{API}/wallet/balance", params={"device_id": DEV}))

    p("google", requests.post(f"{API}/auth/google", json={"token": "x"}))
    p("apple", requests.post(f"{API}/auth/apple", json={"token": "x"}))

    if mode == "on":
        p("analyze no device_id", requests.post(f"{API}/analyze", json={"symbol": "AAPL"}))
        empty = f"wdev_empty_{int(time.time())}"
        p("analyze empty wallet", requests.post(f"{API}/analyze", json={"symbol": "AAPL", "device_id": empty}))
        requests.post(f"{API}/wallet/topup", json={"device_id": empty, "amount_usd": 1})
        r = p("analyze funded", requests.post(f"{API}/analyze", json={"symbol": "AAPL", "device_id": empty}))
        p("balance after charge", requests.get(f"{API}/wallet/balance", params={"device_id": empty}))
        aid = r.json().get("id")
        for _ in range(60):
            d = requests.get(f"{API}/analysis/{aid}").json()
            if d["status"] != "running":
                break
            time.sleep(5)
        print("analysis status:", d["status"], "verdict:", (d.get("verdict") or {}).get("decision"))
        r = p("re-check (should be cached/free)",
              requests.post(f"{API}/analyze", json={"symbol": "AAPL", "device_id": empty}))
        print("served_from_cache:", r.json().get("served_from_cache"), "billed:", r.json().get("billed"))
        p("balance after re-check", requests.get(f"{API}/wallet/balance", params={"device_id": empty}))
    else:
        r = p("analyze no device_id (flag off)", requests.post(f"{API}/analyze", json={"symbol": "AAPL"}))
        print("billed field:", r.json().get("billed"), "price_charged:", r.json().get("price_charged"))


if __name__ == "__main__":
    main()
