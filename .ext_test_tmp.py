"""Temporary verification of the captcha webhook id helper + info route."""
import os
import sys

sys.path.insert(0, os.getcwd())

from dashboard.app import app, _get_captcha_webhook_id  # noqa: E402
from utils.github_data_store import ghd  # noqa: E402


def patch_settings(global_url=None, per_account=None):
    def read_json(path, default=None):
        if path == "config/settings.json":
            return {"security": {"webhook": {"url": global_url}}} if global_url else default
        if path == "config/settings_123.json":
            return {"security": {"webhook": {"url": per_account}}} if per_account else default
        return default

    def list_files(path):
        return ["config/settings.json", "config/settings_123.json"] if per_account else ["config/settings.json"]

    ghd.read_json = read_json
    ghd.list_files = list_files


patch_settings(global_url="https://discord.com/api/webhooks/999000111/abc")
assert _get_captcha_webhook_id() == "999000111", "global settings parse failed"

patch_settings(per_account="https://discord.com/api/webhooks/888777666/tok")
assert _get_captcha_webhook_id() == "888777666", "per-account settings parse failed" 

patch_settings()
assert _get_captcha_webhook_id() is None, "unconfigured should be None"

print("HELPER_OK")

patch_settings(global_url="https://discord.com/api/webhooks/999000111/abc")
with app.test_request_context("/api/extension/info"):
    from flask import session
    session["logged_in"] = True
    session["username"] = "admin"
    session["role"] = "admin"
    resp = app.view_functions["extension_info"]()
    body = resp.get_json()
    print("info:", body)
    assert body.get("success") is True
    assert body.get("webhook_id") == "999000111"
    assert body.get("version")

print("ROUTE_OK")
