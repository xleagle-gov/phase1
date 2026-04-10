"""
Bouncer batch email verification.
Submits a list of emails, polls until complete, and returns only deliverable addresses.
"""

import time
import requests
from config import BOUNCER_API_KEY

BOUNCER_BASE = "https://api.usebouncer.com/v1.1/email/verify/batch"


def verify_emails_batch(email_list, poll_interval=10, max_wait=300):
    """
    Submit emails to Bouncer's batch API and return only deliverable addresses.

    Returns list of deliverable email strings, or None on API failure.
    """
    if not email_list:
        return []

    if not BOUNCER_API_KEY:
        print("  [Bouncer] API key not configured — skipping verification, returning all emails.")
        return list(email_list)

    headers = {
        "x-api-key": BOUNCER_API_KEY,
        "Content-Type": "application/json",
    }

    payload = [{"email": e} for e in email_list]

    try:
        resp = requests.post(BOUNCER_BASE, headers=headers, json=payload, timeout=30)
    except requests.RequestException as e:
        print(f"  [Bouncer] Submit request failed: {e}")
        return None

    if resp.status_code != 200:
        print(f"  [Bouncer] Batch submit failed: {resp.status_code} — {resp.text}")
        return None

    batch_id = resp.json().get("batchId")
    quantity = resp.json().get("quantity", len(email_list))
    print(f"  [Bouncer] Batch created: {batch_id} ({quantity} emails)")

    status_url = f"{BOUNCER_BASE}/{batch_id}"
    auth_headers = {"x-api-key": BOUNCER_API_KEY}
    elapsed = 0

    while elapsed < max_wait:
        time.sleep(poll_interval)
        elapsed += poll_interval

        try:
            status_resp = requests.get(
                status_url, headers=auth_headers, params={"with-stats": "true"}, timeout=15
            )
        except requests.RequestException as e:
            print(f"  [Bouncer] Poll error: {e}")
            continue

        if status_resp.status_code != 200:
            print(f"  [Bouncer] Poll HTTP {status_resp.status_code}")
            continue

        data = status_resp.json()
        status = data.get("status")
        stats = data.get("stats", {})
        processed = data.get("processed", 0)
        print(f"  [Bouncer] {status} — {processed}/{quantity} processed  {stats}")

        if status == "completed":
            break
    else:
        print(f"  [Bouncer] Timed out after {max_wait}s")
        return None

    download_url = f"{BOUNCER_BASE}/{batch_id}/download"
    try:
        dl_resp = requests.get(
            download_url,
            headers=auth_headers,
            params={"download": "deliverable"},
            timeout=30,
        )
    except requests.RequestException as e:
        print(f"  [Bouncer] Download request failed: {e}")
        return None

    if dl_resp.status_code != 200:
        print(f"  [Bouncer] Download failed: {dl_resp.status_code} — {dl_resp.text}")
        return None

    results = dl_resp.json()
    deliverable = [r["email"] for r in results]
    print(f"  [Bouncer] {len(deliverable)}/{len(email_list)} emails deliverable")
    return deliverable
