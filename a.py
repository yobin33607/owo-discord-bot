#!/usr/bin/env python3

import os
import requests

OWNER = "yobin33607"
REPO = "owo-discord-bot"

TOKEN = os.environ.get("GITHUB_TOKEN")
if not TOKEN:
    raise SystemExit("Set GITHUB_TOKEN environment variable.")

url = f"https://api.github.com/repos/{OWNER}/{REPO}/code-scanning/alerts"

headers = {
    "Accept": "application/vnd.github+json",
    "Authorization": f"Bearer {TOKEN}",
    "X-GitHub-Api-Version": "2022-11-28",
}

params = {
    "tool_name": "CodeQL",
    "state": "open",
    "per_page": 100,
}

alerts = []

while url:
    r = requests.get(url, headers=headers, params=params)
    r.raise_for_status()

    alerts.extend(r.json())

    # Handle pagination
    if "next" in r.links:
        url = r.links["next"]["url"]
        params = None
    else:
        url = None

print(f"Found {len(alerts)} CodeQL alerts\n")

for alert in alerts:
    rule = alert["rule"]["id"]
    severity = alert["rule"].get("severity", "unknown")
    security_severity = alert["rule"].get("security_severity_level", "unknown")

    instance = alert.get("most_recent_instance", {})
    location = instance.get("location", {})

    print("=" * 80)
    print(f"Alert #{alert['number']}")
    print(f"Rule: {rule}")
    print(f"Severity: {severity}")
    print(f"Security Severity: {security_severity}")
    print(f"State: {alert['state']}")
    print(f"URL: {alert['html_url']}")
    print(
        f"Location: {location.get('path', '?')}:{location.get('start_line', '?')}"
    )
    print(f"Description: {alert['rule'].get('description', '')}")