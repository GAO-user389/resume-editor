"""Debug reply endpoint raw response."""
import requests, json, time

BASE = "https://resume-editor-h0d0.onrender.com"
FP = "test_fp_debug_" + "d" * 20

# 1. 提反馈
r = requests.post(f"{BASE}/api/feedback/submit", json={
    "fingerprint": FP,
    "content": "debug test email",
    "contact": "XGTX20020526@163.com",
    "image_base64": "",
}, timeout=15)
fid = r.json()["feedback"]["id"]
print(f"fid={fid}")

# 2. admin reply
s = requests.Session()
s.post(f"{BASE}/api/admin/login", json={"password": "admin123"}, timeout=15)
r = s.post(f"{BASE}/api/admin/feedbacks/{fid}/reply", json={
    "reply": "debug reply"
}, timeout=30)
print(f"status={r.status_code}")
print(f"body={r.text}")
