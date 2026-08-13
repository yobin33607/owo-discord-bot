"""
Limey Chat Archive
==================
Scans a selfbot account's servers and DMs, then builds a downloadable
archive (JSON + readable HTML) — but only after the owner explicitly confirms on the
dashboard Archives page.

Flow:
  1. POST /api/archive/scan      -> starts an async scan on the bot's event loop
  2. GET  /api/archive/status    -> progress (polled by the page)
  3. POST /api/archive/create    -> owner confirms; archive built locally, then
                                    pushed to the GitHub data repo (zip + index.json)
  4. GET  /api/archive/download  -> serve the zip (from GitHub, local as fallback)
  5. POST /api/archive/delete    -> remove an archive (GitHub + local)
  6. POST /api/archive/rename    -> rename an archive (record + files)
  7. POST /api/archive/purge     -> delete every archive
  8. GET  /api/archive/info      -> full metadata for one archive
  9. GET  /api/archive/download-json / -html -> serve index.json / readable HTML

Finished archives are stored in the GitHub data repo under ``archives/``
(yobin33607/data). The local data/archives/ folder is used only as a build
space and as a fallback when a push fails or the repo is unreachable.
"""

import asyncio
import html
import json
import os
import re
import shutil
import time
import zipfile
from datetime import datetime

# ── Paths ──────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVE_DIR = os.path.join(BASE_DIR, "data", "archives")
INDEX_FILE = os.path.join(ARCHIVE_DIR, "index.json")

# Per-channel pause so selfbot tokens stay well under Discord rate limits.
CHANNEL_DELAY = 0.5

# ── In-memory scan state ───────────────────────────────
# user_id -> {status, message_limit, include_guilds, include_dms, counts,
#             results, error, ...}
scans = {}


def _init_scan(user_id, message_limit, include_guilds, include_dms):
    scans[user_id] = {
        "status": "scanning",
        "message_limit": message_limit,
        "include_guilds": bool(include_guilds),
        "include_dms": bool(include_dms),
        "started_at": time.time(),
        "finished_at": None,
        "guilds_total": 0,
        "guilds_done": 0,
        "dms_total": 0,
        "dms_done": 0,
        "channels_total": 0,
        "channels_done": 0,
        "messages_total": 0,
        "error": None,
        "results": {"guilds": [], "dms": []},
    }
    return scans[user_id]


def get_scan_status(user_id):
    """Return a JSON-safe copy of the scan state (results omitted)."""
    state = scans.get(user_id)
    if not state:
        return None
    return {
        "status": state.get("status", "idle"),
        "message_limit": state.get("message_limit"),
        "include_guilds": state.get("include_guilds", True),
        "include_dms": state.get("include_dms", True),
        "started_at": state.get("started_at"),
        "finished_at": state.get("finished_at"),
        "guilds_total": state.get("guilds_total", 0),
        "guilds_done": state.get("guilds_done", 0),
        "dms_total": state.get("dms_total", 0),
        "dms_done": state.get("dms_done", 0),
        "channels_total": state.get("channels_total", 0),
        "channels_done": state.get("channels_done", 0),
        "messages_total": state.get("messages_total", 0),
        "error": state.get("error"),
    }


def _message_to_dict(msg):
    return {
        "id": str(msg.id),
        "author": str(msg.author) if msg.author else "Unknown",
        "author_id": str(msg.author.id) if msg.author else None,
        "content": msg.content or "",
        "timestamp": msg.created_at.isoformat(timespec="seconds") if msg.created_at else None,
        "attachments": [a.url for a in (msg.attachments or [])],
        "type": str(msg.type),
    }


def _channel_name(ch):
    name = getattr(ch, "name", None)
    if name:
        return name
    recipient = getattr(ch, "recipient", None)
    if recipient:
        return str(recipient)
    return f"chat_{ch.id}"


async def _fetch_channel(bot, ch, message_limit):
    """Fetch up to message_limit messages from a channel (None = everything)."""
    try:
        messages = []
        async for msg in ch.history(limit=message_limit):
            messages.append(_message_to_dict(msg))
        messages.reverse()  # oldest -> newest
        return messages
    except Exception:
        return []


async def run_scan(bot, user_id, message_limit=200, include_guilds=True, include_dms=True):
    """Scan the account's servers and DMs. Updates scans[user_id] as it goes."""
    state = _init_scan(user_id, message_limit, include_guilds, include_dms)
    state["username"] = getattr(bot, "username", "") or f"account_{user_id}"
    try:
        # ── Servers ───────────────────────────────────
        if include_guilds:
            guilds = list(bot.guilds or [])
            state["guilds_total"] = len(guilds)
            for guild in guilds:
                g_entry = {
                    "id": str(guild.id),
                    "name": guild.name or "Unknown",
                    "channels": [],
                }
                channels = list(guild.text_channels or [])
                state["channels_total"] += len(channels)
                for ch in channels:
                    msgs = await _fetch_channel(bot, ch, message_limit)
                    g_entry["channels"].append({
                        "id": str(ch.id),
                        "name": ch.name or f"channel_{ch.id}",
                        "messages": msgs,
                    })
                    state["channels_done"] += 1
                    state["messages_total"] += len(msgs)
                    await asyncio.sleep(CHANNEL_DELAY)
                state["results"]["guilds"].append(g_entry)
                state["guilds_done"] += 1

        # ── DMs ───────────────────────────────────────
        if include_dms:
            dms = list(bot.private_channels or [])
            state["dms_total"] = len(dms)
            for dc in dms:
                msgs = await _fetch_channel(bot, dc, message_limit)
                recipient = getattr(dc, "recipient", None)
                state["results"]["dms"].append({
                    "id": str(dc.id),
                    "name": _channel_name(dc),
                    "recipient_id": str(recipient.id) if recipient else None,
                    "messages": msgs,
                })
                state["dms_done"] += 1
                state["messages_total"] += len(msgs)
                await asyncio.sleep(CHANNEL_DELAY)

        state["status"] = "ready"
    except Exception as e:
        state["status"] = "error"
        state["error"] = str(e)
    finally:
        state["finished_at"] = time.time()


# ── Scan search ───────────────────────────────────────

def search_scan(user_id, query, limit=200):
    """Search message content across a completed in-memory scan.

    Searches both servers (all text channels) and DMs for a case-insensitive
    substring match in message content and attachment URLs. Intended for
    use before an archive is created, so nothing is written anywhere.

    Returns (results_list, total_matches) — total_matches is the full count
    while results_list is capped at `limit`. Returns (None, None) when there
    is no completed scan for the account.
    """
    query = (query or "").strip()
    if not query:
        return [], 0

    state = scans.get(user_id)
    if not state or state.get("status") != "ready":
        return None, None

    q = query.lower()
    results = []
    total = 0

    def _matches(msg):
        content = (msg.get("content") or "").lower()
        attach = " ".join(msg.get("attachments") or []).lower()
        return q in content or q in attach

    def _append(kind, guild, channel, msg):
        nonlocal total
        total += 1
        if len(results) < limit:
            results.append({
                "kind": kind,
                "guild": guild,
                "channel": channel,
                "author": msg.get("author", "Unknown"),
                "timestamp": msg.get("timestamp"),
                "content": msg.get("content", ""),
                "attachments": msg.get("attachments") or [],
                "id": str(msg.get("id")) if msg.get("id") else None,
            })

    for guild in (state.get("results") or {}).get("guilds", []):
        for ch in guild.get("channels", []):
            for msg in ch.get("messages", []):
                if _matches(msg):
                    _append("guild", guild.get("name", "Unknown"),
                             ch.get("name", "channel"), msg)

    for dm in (state.get("results") or {}).get("dms", []):
        for msg in dm.get("messages", []):
            if _matches(msg):
                _append("dm", "Direct Messages", dm.get("name", "DM"), msg)

    return results, total


# ── HTML rendering ─────────────────────────────────────

def _escape(text):
    return html.escape(str(text), quote=True)


def _render_chat_page(title, messages, chat_name):
    """Render a self-contained readable HTML page for one chat."""
    parts = [f"<div class='chat-title'>{_escape(title)}</div>"]
    if not messages:
        parts.append("<div class='empty'>No messages in this chat.</div>")
    for m in messages:
        author = _escape(m.get("author", "Unknown"))
        ts = _escape(m.get("timestamp") or "")
        content = _escape(m.get("content") or "")
        parts.append(
            f"<div class='msg'><span class='author'>{author}</span>"
            f"<span class='time'>{ts}</span><div class='content'>{content}</div>"
        )
        for url in m.get("attachments", []):
            parts.append(f"<div class='attach'><a href='{_escape(url)}'>📎 {_escape(url)}</a></div>")
        parts.append("</div>")
    body = "\n".join(parts)
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>{_escape(title)}</title>
<style>
body{{background:#1e1f29;color:#e4e6f0;font-family:Segoe UI,system-ui,sans-serif;max-width:900px;margin:0 auto;padding:24px;}}
h1,h2{{color:#8b8cff;}}
.chat-title{{font-size:1.4em;font-weight:700;margin-bottom:16px;color:#8b8cff;}}
.empty{{color:#888;padding:20px 0;}}
.msg{{border-bottom:1px solid #2c2e3d;padding:10px 0;}}
.author{{color:#ffb454;font-weight:600;margin-right:10px;}}
.time{{color:#888;font-size:0.8em;}}
.content{{margin-top:4px;white-space:pre-wrap;word-break:break-word;}}
.attach a{{color:#4da3ff;font-size:0.85em;}}
a{{color:#4da3ff;text-decoration:none;}}
</style></head><body>{body}</body></html>"""


def _render_index_html(meta, guilds, dms):
    scope_name = meta.get("scope_name") or meta.get("username") or "Unknown"
    scope_type = meta.get("scope_type")
    if scope_type == "dm":
        heading = "Direct Messages"
    else:
        heading = "Servers"
    rows = []
    for g in guilds:
        link = f"guilds/{_safe_name(g['name'])}_{g['id']}/index.html"
        rows.append(f"<li>📁 <a href='{_escape(link)}'>{_escape(g['name'])}</a> "
                    f"<span class='count'>({len(g['channels'])} channels)</span></li>")
    for d in dms:
        link = f"dms/{_safe_name(d['name'])}_{d['id']}.html"
        rows.append(f"<li>💬 <a href='{_escape(link)}'>{_escape(d['name'])}</a></li>")
    list_html = "\n".join(rows) if rows else "<li class='empty'>Nothing scanned.</li>"
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Archive Index</title>
<style>
body{{background:#1e1f29;color:#e4e6f0;font-family:Segoe UI,system-ui,sans-serif;max-width:900px;margin:0 auto;padding:24px;}}
h1{{color:#8b8cff;}}
li{{margin:8px 0;}}
a{{color:#4da3ff;text-decoration:none;}}
.count,.meta{{color:#888;font-size:0.85em;}}
</style></head><body>
<h1>🗄️ Chat Archive — {_escape(scope_name)}</h1>
<p class="meta">Scanned {meta.get('scanned_at', '?')} · {meta.get('message_count', 0)} messages · account {_escape(meta.get('username', 'Unknown'))}</p>
<h2>{heading}</h2><ul>{list_html}</ul>
</body></html>"""


def _safe_name(text):
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", str(text))
    return (s or "unknown")[:80]


def _guild_dir(g):
    return f"{_safe_name(g['name'])}_{g['id']}"


# ── GitHub data repo ──────────────────────────────────
# Lazy import so the scanner can be used/tested without the store available.


def _ghd():
    """The GitHub data store singleton (lazy)."""
    from utils.github_data_store import ghd
    return ghd


REPO_INDEX_PATH = "archives/index.json"


def _repo_zip_path(name):
    return f"archives/{name}.zip"


def _repo_json_path(name):
    return f"archives/{name}/index.json"


def _raw_github_url(path):
    """Raw download URL for a repo path (works for public repos).

    Note: downloads are served via the dashboard proxy (which uses the GitHub
    token), so this URL is informational only for private repos.
    """
    try:
        from urllib.parse import quote
    except ImportError:
        quote = lambda s: s
    store = _ghd()
    repo = getattr(store, "repo", "yobin33607/data")
    branch = getattr(store, "branch", "main")
    return f"https://raw.githubusercontent.com/{repo}/{branch}/{quote(path)}"


def fetch_github_file(path):
    """Download a file from the data repo using the store's token.

    Returns (bytes | None, error_str | None). Works for public and private
    repos because it authenticates with the GitHub token.
    """
    store = _ghd()
    try:
        api_base = getattr(store, "api_base", None) or f"https://api.github.com/repos/{getattr(store, 'repo', 'yobin33607/data')}/contents"
        headers = dict(getattr(store, "headers", {}) or {})
        headers["Accept"] = "application/vnd.github.v3.raw"
        request = getattr(store, "request", None)
        if request is None:
            import requests
            request = requests.request
        r = request("GET", f"{api_base}/{path}", headers=headers, timeout=30)
        if r.status_code == 200:
            return r.content, None
        return None, f"GitHub returned HTTP {r.status_code}"
    except Exception as e:
        return None, str(e)


# ── Archive builder ────────────────────────────────────

def _build_guild_html(g):
    """Per-guild index page linking to each channel page."""
    rows = []
    for ch in g["channels"]:
        link = f"{_safe_name(ch['name'])}_{ch['id']}.html"
        rows.append(
            f"<li>💬 <a href='{_escape(link)}'>{_escape(ch['name'])}</a> "
            f"<span class='count'>({len(ch['messages'])} messages)</span></li>"
        )
    list_html = "\n".join(rows) if rows else "<li class='empty'>No text channels.</li>"
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>{_escape(g['name'])}</title>
<style>
body{{background:#1e1f29;color:#e4e6f0;font-family:Segoe UI,system-ui,sans-serif;max-width:900px;margin:0 auto;padding:24px;}}
h1{{color:#8b8cff;}} li{{margin:8px 0;}} a{{color:#4da3ff;text-decoration:none;}}
.count,.empty{{color:#888;font-size:0.85em;}}
</style></head><body>
<h1>📁 {_escape(g['name'])}</h1>
<p class="count"><a href="../index.html">← Back to index</a></p>
<ul>{list_html}</ul>
</body></html>"""


def _push_archive_to_github(name):
    """Push a built archive (zip + index.json) to the GitHub data repo.

    Returns (info_dict | None, error_str | None).
    """
    store = _ghd()
    root = os.path.join(ARCHIVE_DIR, name)
    zip_path = os.path.join(ARCHIVE_DIR, name + ".zip")

    zip_bytes = None
    if os.path.exists(zip_path):
        try:
            with open(zip_path, "rb") as f:
                zip_bytes = f.read()
        except OSError as e:
            return None, f"Failed to read local archive zip: {e}"

    json_bytes = None
    json_local = os.path.join(root, "index.json")
    if os.path.exists(json_local):
        try:
            with open(json_local, "r", encoding="utf-8") as f:
                json_bytes = f.read()
        except OSError as e:
            return None, f"Failed to read local archive index.json: {e}"

    if zip_bytes is None and json_bytes is None:
        return None, "Archive files not found on disk."

    # GitHub contents API caps a single file at 100 MB.
    if zip_bytes is not None and len(zip_bytes) > 100 * 1024 * 1024:
        return None, "Archive zip is larger than GitHub's 100 MB file limit — stored locally instead."

    try:
        # index.json first (small) so a partial failure leaves only a tiny
        # orphaned file rather than a multi-MB zip.
        if json_bytes is not None:
            if not store.write_file(_repo_json_path(name), json_bytes,
                                    message=f"Archive {name} (index.json)"):
                return None, "Failed to push index.json to GitHub."
        if zip_bytes is not None:
            if not store.write_file(_repo_zip_path(name), zip_bytes,
                                    message=f"Archive {name} (zip)"):
                return None, "Failed to push zip to GitHub."
    except Exception as e:
        return None, f"GitHub push failed: {e}"

    return {
        "github_zip": _repo_zip_path(name),
        "github_json": _repo_json_path(name),
        "github_download": _raw_github_url(_repo_zip_path(name)),
    }, None


def _load_repo_index():
    """The archive index stored in the GitHub repo (authoritative)."""
    try:
        data = _ghd().read_json(REPO_INDEX_PATH)
    except Exception:
        return None
    if isinstance(data, dict):
        return data.get("archives", [])
    if isinstance(data, list):
        return data
    return None


def _update_repo_index(info):
    """Insert/replace an entry in the repo index; returns True on success."""
    try:
        entries = _load_repo_index() or []
    except Exception:
        entries = []
    entries = [e for e in entries if e.get("name") != info.get("name")]
    entries.insert(0, info)
    try:
        return bool(_ghd().write_json(REPO_INDEX_PATH, {"archives": entries},
                                      message="Update archive index"))
    except Exception:
        return False


def _remove_repo_archive(name):
    """Delete the archive files from GitHub; returns True if anything was removed."""
    removed = False
    try:
        store = _ghd()
        if store.delete_file(_repo_zip_path(name), message=f"Delete archive {name} (zip)"):
            removed = True
        if store.delete_file(_repo_json_path(name), message=f"Delete archive {name} (index.json)"):
            removed = True
    except Exception:
        pass
    return removed


# ── Archive scope helpers ──────────────────────────────
# Archives are organized per conversation: every server gets its own archive
# and every DM conversation gets its own archive (named after the other
# user). Each archive carries a scope_key so re-archiving the same server /
# DM — even from a different selfbot — replaces the previous archive (the
# latest scan wins).


def _scope_key_for_guild(g):
    return f"guild:{g['id']}"


def _scope_key_for_dm(d):
    if d.get("recipient_id"):
        return f"dm:{d['recipient_id']}"
    return f"dm_group:{d['id']}"


def _archive_name_exists(name):
    if os.path.exists(os.path.join(ARCHIVE_DIR, name)):
        return True
    if os.path.exists(os.path.join(ARCHIVE_DIR, name + ".zip")):
        return True
    return any(e.get("name") == name for e in list_archives())


def _unique_archive_name(base):
    name = base
    suffix = 2
    while _archive_name_exists(name):
        name = f"{base}_{suffix}"
        suffix += 1
    return name


def _remove_archives_with_scope(scope_key):
    """Delete every existing archive with the same scope key (latest wins)."""
    for entry in list_archives():
        if entry.get("scope_key") == scope_key:
            delete_archive(entry.get("name", ""))


def _build_scope_archive(scope_type, scope_name, scope_key, username, user_id,
                         guilds, dms, push_to_github, message_limit, finished_at):
    """Build one archive for a single scope (one server or one DM)."""
    # Latest scan wins: drop any previous archive for the same server / DM.
    _remove_archives_with_scope(scope_key)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = _safe_name(scope_name) or f"{scope_type}_{timestamp}"
    name = _unique_archive_name(f"{base}_{timestamp}")
    root = os.path.join(ARCHIVE_DIR, name)

    message_count = sum(len(ch.get("messages") or [])
                        for g in guilds for ch in g.get("channels") or [])
    message_count += sum(len(d.get("messages") or []) for d in dms)

    try:
        os.makedirs(ARCHIVE_DIR, exist_ok=True)
        shutil.rmtree(root, ignore_errors=True)
        os.makedirs(root, exist_ok=True)
        guild_dir = os.path.join(root, "guilds")
        dm_dir = os.path.join(root, "dms")
        os.makedirs(guild_dir, exist_ok=True)
        os.makedirs(dm_dir, exist_ok=True)

        # Per-guild JSON + channel pages (server archives)
        for g in guilds:
            g_folder = os.path.join(guild_dir, _guild_dir(g))
            os.makedirs(g_folder, exist_ok=True)
            with open(os.path.join(g_folder, "guild.json"), "w", encoding="utf-8") as f:
                json.dump({"name": g["name"], "id": g["id"]}, f, indent=2, ensure_ascii=False)
            with open(os.path.join(g_folder, "index.html"), "w", encoding="utf-8") as f:
                f.write(_build_guild_html(g))
            for ch in g["channels"]:
                ch_base = f"{_safe_name(ch['name'])}_{ch['id']}"
                with open(os.path.join(g_folder, ch_base + ".json"), "w", encoding="utf-8") as f:
                    json.dump({
                        "channel": ch["name"],
                        "guild": g["name"],
                        "guild_id": g["id"],
                        "messages": ch["messages"],
                    }, f, indent=2, ensure_ascii=False)
                with open(os.path.join(g_folder, ch_base + ".html"), "w", encoding="utf-8") as f:
                    f.write(_render_chat_page(f"{g['name']} — {ch['name']}", ch["messages"], ch["name"]))

        # Per-DM JSON + HTML (DM archives)
        for d in dms:
            dm_base = f"{_safe_name(d['name'])}_{d['id']}"
            with open(os.path.join(dm_dir, dm_base + ".json"), "w", encoding="utf-8") as f:
                json.dump({"name": d["name"], "id": d["id"], "messages": d["messages"]},
                          f, indent=2, ensure_ascii=False)
            with open(os.path.join(dm_dir, dm_base + ".html"), "w", encoding="utf-8") as f:
                f.write(_render_chat_page(f"DM — {d['name']}", d["messages"], d["name"]))

        # Index files
        meta = {
            "username": username,
            "user_id": user_id,
            "scope_type": scope_type,
            "scope_key": scope_key,
            "scope_name": scope_name,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "scanned_at": datetime.fromtimestamp(finished_at or time.time())
                .isoformat(timespec="seconds"),
            "message_limit": message_limit,
            "guild_count": len(guilds),
            "dm_count": len(dms),
            "message_count": message_count,
        }
        with open(os.path.join(root, "index.json"), "w", encoding="utf-8") as f:
            json.dump({"meta": meta, "guilds": guilds, "dms": dms}, f, indent=2, ensure_ascii=False)
        with open(os.path.join(root, "index.html"), "w", encoding="utf-8") as f:
            f.write(_render_index_html(meta, guilds, dms))

        # Zip it for download
        zip_path = os.path.join(ARCHIVE_DIR, name + ".zip")
        if os.path.exists(zip_path):
            os.remove(zip_path)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for foldername, _, filenames in os.walk(root):
                for filename in filenames:
                    full = os.path.join(foldername, filename)
                    zf.write(full, os.path.relpath(full, root))

        info = {
            "name": name,
            "username": username,
            "scope_type": scope_type,
            "scope_key": scope_key,
            "scope_name": scope_name,
            "created_at": meta["created_at"],
            "guild_count": meta["guild_count"],
            "dm_count": meta["dm_count"],
            "message_count": meta["message_count"],
            "size_bytes": os.path.getsize(zip_path),
            "download": f"/api/archive/download/{name}",
            "stored_in": "local",
        }

        # Push to GitHub, then drop the local copy on success.
        if push_to_github:
            github, push_err = _push_archive_to_github(name)
            if push_err:
                info["push_error"] = push_err
            elif github:
                info.update(github)
                info["stored_in"] = "github"
                info["download_github"] = github["github_download"]
                info["size_bytes"] = os.path.getsize(zip_path)
                # Local copy removed — GitHub is the source of truth.
                shutil.rmtree(root, ignore_errors=True)
                if os.path.exists(zip_path):
                    try:
                        os.remove(zip_path)
                    except OSError:
                        pass
                _update_repo_index(info)

        _update_index(info)
        return info, None
    except Exception as e:
        return None, f"Failed to build archive: {e}"


def create_archive(user_id, push_to_github=True):
    """Write the completed scan out as one archive per server and per DM.

    Every server becomes its own archive and every DM conversation becomes
    its own archive (named after the other user). Re-archiving the same
    server / DM — even from a different selfbot — replaces the previous
    archive, so the latest scan always wins.

    After building locally, each archive is pushed to the GitHub data repo
    and the local copy is removed (unless the push fails, in which case the
    local copy is kept as a fallback).

    Returns (infos, errs) — lists of created archive info dicts and per-scope
    error strings.
    """
    state = scans.get(user_id)
    if not state or state.get("status") != "ready":
        return [], ["No completed scan for this account — run a scan first."]
    results = state.get("results") or {"guilds": [], "dms": []}
    guilds, dms = results.get("guilds", []), results.get("dms", [])

    username = state.get("username") or f"account_{user_id}"
    infos, errs = [], []

    for g in guilds:
        info, err = _build_scope_archive(
            scope_type="guild", scope_name=g.get("name", "Unknown"),
            scope_key=_scope_key_for_guild(g),
            username=username, user_id=user_id,
            guilds=[g], dms=[], push_to_github=push_to_github,
            message_limit=state.get("message_limit"),
            finished_at=state.get("finished_at") or time.time(),
        )
        if err:
            errs.append(f"{g.get('name', 'Unknown')}: {err}")
        else:
            infos.append(info)

    for d in dms:
        info, err = _build_scope_archive(
            scope_type="dm", scope_name=d.get("name", "DM"),
            scope_key=_scope_key_for_dm(d),
            username=username, user_id=user_id,
            guilds=[], dms=[d], push_to_github=push_to_github,
            message_limit=state.get("message_limit"),
            finished_at=state.get("finished_at") or time.time(),
        )
        if err:
            errs.append(f"{d.get('name', 'DM')}: {err}")
        else:
            infos.append(info)

    return infos, errs


# ── Archive index (data/archives/index.json) ───────────

def _load_index():
    try:
        if os.path.exists(INDEX_FILE):
            with open(INDEX_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("archives", [])
    except Exception:
        pass
    return []


def _update_index(info):
    entries = _load_index()
    entries = [e for e in entries if e.get("name") != info.get("name")]
    entries.insert(0, info)
    try:
        os.makedirs(ARCHIVE_DIR, exist_ok=True)
        with open(INDEX_FILE, "w", encoding="utf-8") as f:
            json.dump({"archives": entries}, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def list_archives():
    """Archives available for download.

    The repo index is authoritative; local entries are merged in so an archive
    that could not be pushed (or was created while the repo was unreachable)
    still shows up. Stale local entries whose files are gone are dropped.
    """
    entries: list = []
    seen: set = set()

    repo_index = _load_repo_index()
    if isinstance(repo_index, list):
        for e in repo_index:
            name = e.get("name", "")
            if not name or name in seen:
                continue
            if not e.get("stored_in"):
                e["stored_in"] = "github"
            if not e.get("github_download"):
                e["github_download"] = _raw_github_url(_repo_zip_path(name))
            entries.append(e)
            seen.add(name)

    # Local fallback: entries whose files still exist.
    for e in _load_index():
        name = e.get("name", "")
        if not name or name in seen:
            continue
        if os.path.exists(os.path.join(ARCHIVE_DIR, name + ".zip")):
            if not e.get("stored_in"):
                e["stored_in"] = "local"
            entries.append(e)
            seen.add(name)

    try:
        with open(INDEX_FILE, "w", encoding="utf-8") as f:
            json.dump({"archives": entries}, f, indent=2, ensure_ascii=False)
    except Exception:
        pass
    return entries


def archive_zip_path(name):
    """Local zip path if the archive exists on disk (None otherwise)."""
    if not name or not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        return None
    path = os.path.join(ARCHIVE_DIR, name + ".zip")
    return path if os.path.exists(path) else None


def archive_download_github(name):
    """Raw GitHub URL for the archive zip, if it was pushed to the repo."""
    if not name or not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        return None
    repo_index = _load_repo_index()
    if not isinstance(repo_index, list):
        return None
    for e in repo_index:
        if e.get("name") == name:
            return e.get("github_download") or _raw_github_url(_repo_zip_path(name))
    return None


def delete_archive(name):
    """Delete an archive (GitHub files + local folder + zip)."""
    if not name or not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        return False
    removed = _remove_repo_archive(name)
    root = os.path.join(ARCHIVE_DIR, name)
    if os.path.isdir(root):
        shutil.rmtree(root, ignore_errors=True)
        removed = True
    zip_path = os.path.join(ARCHIVE_DIR, name + ".zip")
    if os.path.exists(zip_path):
        try:
            os.remove(zip_path)
            removed = True
        except OSError:
            pass

    # Drop cached parsed copies so a deleted archive can't keep being browsed.
    _reader_cache.pop(name, None)

    # Drop the entry from both indices.
    local_entries = [e for e in _load_index() if e.get("name") != name]
    try:
        with open(INDEX_FILE, "w", encoding="utf-8") as f:
            json.dump({"archives": local_entries}, f, indent=2, ensure_ascii=False)
    except Exception:
        pass
    repo_index = _load_repo_index() or []
    repo_entries = [e for e in repo_index if e.get("name") != name]
    if len(repo_entries) != len(repo_index):
        try:
            _ghd().write_json(REPO_INDEX_PATH, {"archives": repo_entries},
                              message="Update archive index")
        except Exception:
            pass
    return removed


# ── Archive actions (rename / purge / info / file serving) ─

def _rename_entry_in_list(entries, old_name, new_name):
    """Rename an entry inside an index list; returns (new_list, renamed_entry)."""
    renamed = None
    out = []
    for e in entries:
        if e.get("name") == old_name:
            e = dict(e)
            e["name"] = new_name
            e["download"] = f"/api/archive/download/{new_name}"
            e["github_zip"] = _repo_zip_path(new_name)
            e["github_json"] = _repo_json_path(new_name)
            e["github_download"] = _raw_github_url(_repo_zip_path(new_name))
            renamed = e
        out.append(e)
    return out, renamed


def rename_archive(old_name, new_name):
    """Rename an archive: record (both indices) + files on disk / in the repo.

    Local files are renamed in place; GitHub-stored archives are re-uploaded
    under the new name and the old copies deleted. Returns (info_dict, error_str)
    — on success info_dict is the updated archive entry (errors, if any partial
    failures happened, are surfaced in ``info["rename_warning"]``).
    """
    if not re.fullmatch(r"[A-Za-z0-9._-]+", old_name) or not re.fullmatch(r"[A-Za-z0-9._-]+", new_name):
        return None, "Invalid archive name."
    if old_name == new_name:
        return None, "New name is the same as the current name."

    entries = list_archives()
    entry = next((e for e in entries if e.get("name") == old_name), None)
    if not entry:
        return None, "Archive not found."
    if any(e.get("name") == new_name for e in entries):
        return None, f"An archive named '{new_name}' already exists."

    stored_in = entry.get("stored_in", "local")
    warnings = []

    # ── Local files (kept when a GitHub push failed / repo was unreachable) ──
    local_root = os.path.join(ARCHIVE_DIR, old_name)
    local_zip = os.path.join(ARCHIVE_DIR, old_name + ".zip")
    if os.path.isdir(local_root):
        try:
            os.rename(local_root, os.path.join(ARCHIVE_DIR, new_name))
        except OSError as e:
            warnings.append(f"local folder: {e}")
    if os.path.exists(local_zip):
        try:
            os.rename(local_zip, os.path.join(ARCHIVE_DIR, new_name + ".zip"))
        except OSError as e:
            warnings.append(f"local zip: {e}")

    # ── GitHub copy (authoritative for github-stored archives) ──
    if stored_in == "github":
        try:
            zip_bytes, zip_err = fetch_github_file(_repo_zip_path(old_name))
            json_bytes, json_err = fetch_github_file(_repo_json_path(old_name))
            zip_ok = (zip_err is None and zip_bytes is not None
                      and _ghd().write_file(_repo_zip_path(new_name), zip_bytes,
                                            message=f"Rename archive {old_name} -> {new_name} (zip)"))
            json_ok = (json_err is None and json_bytes is not None
                       and _ghd().write_file(_repo_json_path(new_name), json_bytes,
                                             message=f"Rename archive {old_name} -> {new_name} (index.json)"))
            # Only remove the old copies once both new files are safely in place.
            if zip_ok and json_ok:
                _ghd().delete_file(_repo_zip_path(old_name),
                                   message=f"Delete archive {old_name} (zip)")
                _ghd().delete_file(_repo_json_path(old_name),
                                   message=f"Delete archive {old_name} (index.json)")
            else:
                warnings.append("GitHub: failed to write renamed files (old copies kept)")
        except Exception as e:
            warnings.append(f"GitHub: {e}")

    # ── Both indices ──
    local_entries, _ = _rename_entry_in_list(_load_index(), old_name, new_name)
    try:
        os.makedirs(ARCHIVE_DIR, exist_ok=True)
        with open(INDEX_FILE, "w", encoding="utf-8") as f:
            json.dump({"archives": local_entries}, f, indent=2, ensure_ascii=False)
    except Exception:
        pass
    repo_index = _load_repo_index() or []
    repo_entries, renamed_entry = _rename_entry_in_list(repo_index, old_name, new_name)
    if len(repo_entries) != len(repo_index):
        try:
            _ghd().write_json(REPO_INDEX_PATH, {"archives": repo_entries},
                              message="Update archive index")
        except Exception:
            pass

    # Drop cached parsed copies so the renamed archive is re-read fresh.
    _reader_cache.pop(old_name, None)

    info = renamed_entry or dict(entry, name=new_name,
                                 download=f"/api/archive/download/{new_name}",
                                 github_zip=_repo_zip_path(new_name),
                                 github_json=_repo_json_path(new_name),
                                 github_download=_raw_github_url(_repo_zip_path(new_name)))
    if warnings:
        info["rename_warning"] = "; ".join(warnings)
    return info, None


def purge_archives():
    """Delete every archive (local + GitHub). Returns the number deleted."""
    count = 0
    for e in list_archives():
        if delete_archive(e.get("name", "")):
            count += 1
    return count


def archive_info(name):
    """Full metadata for one archive: its index entry + index.json meta.

    Returns (info_dict | None, error_str | None).
    """
    entries = list_archives()
    entry = next((e for e in entries if e.get("name") == name), None)
    if not entry:
        return None, "Archive not found."
    meta = {}
    data, err = load_archive_index(name)
    if err is None and isinstance(data, dict):
        meta = data.get("meta") or {}
    return {**entry, "meta": meta}, None


def archive_file_bytes(name, kind):
    """Serve an archive's index file: 'json' (raw index.json) or 'html' (readable index).

    Returns (bytes | None, mime | None, error_str | None). Checks the local copy
    first, then the GitHub repo. HTML is rendered server-side from the archive
    index because only the zip + index.json are pushed to GitHub.
    """
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        return None, None, "Invalid archive name."

    content = None
    local_path = os.path.join(ARCHIVE_DIR, name, "index.json")
    if os.path.exists(local_path):
        try:
            with open(local_path, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError as e:
            return None, None, f"Failed to read local archive: {e}"
    else:
        raw, err = fetch_github_file(_repo_json_path(name))
        if err or raw is None:
            return None, None, err or "Archive not found."
        try:
            content = raw.decode("utf-8")
        except (UnicodeDecodeError, AttributeError):
            content = None
    if content is None:
        return None, None, "Archive index is unreadable."

    if kind == "html":
        try:
            data = json.loads(content)
            meta = data.get("meta") or {}
            html_doc = _render_index_html(meta, data.get("guilds") or [], data.get("dms") or [])
            return html_doc.encode("utf-8"), "text/html; charset=utf-8", None
        except Exception as e:
            return None, None, f"Failed to render archive HTML: {e}"
    return content.encode("utf-8"), "application/json", None


# ── Archive reader (in-dashboard browsing) ──────────────
# Lets the dashboard open an existing archive and read its contents (servers,
# channels, messages) without downloading the zip. Archive index.json files
# live either in the GitHub data repo (archives/<name>/index.json) or locally
# (data/archives/<name>/index.json, kept when a GitHub push failed).

# name -> (fetched_at, parsed_index)
_reader_cache = {}
_READER_TTL = 300.0  # seconds
_READER_MAX = 8      # cap cached archives so memory stays bounded


def _reader_evict():
    if len(_reader_cache) <= _READER_MAX:
        return
    oldest = sorted(_reader_cache, key=lambda k: _reader_cache[k][0])
    for k in oldest[: len(_reader_cache) - _READER_MAX]:
        _reader_cache.pop(k, None)


def load_archive_index(name):
    """Parse an archive's index.json (local disk first, then GitHub repo).

    Returns (parsed_dict | None, error_str | None). Results are cached briefly
    so browsing many channels of the same archive doesn't re-download it.
    """
    if not name or not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        return None, "Invalid archive name."

    now = time.time()
    cached = _reader_cache.get(name)
    if cached and now - cached[0] < _READER_TTL:
        return cached[1], None
    _reader_evict()

    data = None
    # Local copy first (fallback archives are stored locally only).
    local_path = os.path.join(ARCHIVE_DIR, name, "index.json")
    if os.path.exists(local_path):
        try:
            with open(local_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            return None, f"Failed to read local archive: {e}"
    else:
        content, err = fetch_github_file(_repo_json_path(name))
        if err or content is None:
            return None, err or "Archive not found."
        try:
            data = json.loads(content.decode("utf-8"))
        except Exception as e:
            return None, f"Failed to parse archive: {e}"

    if not isinstance(data, dict):
        return None, "Archive index is malformed."
    _reader_cache[name] = (now, data)
    return data, None


def archive_detail(name):
    """Tree view of an archive: meta + servers/DMs with per-channel summaries.

    Returns (detail_dict | None, error_str | None). The full messages are NOT
    included here — the UI fetches them per channel via archive_channel_messages
    so we don't push entire chat logs to the browser at once.
    """
    data, err = load_archive_index(name)
    if err or data is None:
        return None, err or "Archive not found."

    meta = data.get("meta") or {}

    def _channel_summary(ch):
        msgs = ch.get("messages") or []
        first_ts = msgs[0].get("timestamp") if msgs else None
        last_ts = msgs[-1].get("timestamp") if msgs else None
        return {
            "id": str(ch.get("id", "")),
            "name": ch.get("name") or f"channel_{ch.get('id', '')}",
            "message_count": len(msgs),
            "first_ts": first_ts,
            "last_ts": last_ts,
        }

    guilds = []
    for g in data.get("guilds") or []:
        guilds.append({
            "id": str(g.get("id", "")),
            "name": g.get("name") or "Unknown",
            "channels": [_channel_summary(ch) for ch in (g.get("channels") or [])],
        })
    dms = [_channel_summary(ch) for ch in (data.get("dms") or [])]

    total_messages = sum(len(ch.get("messages") or [])
                         for g in data.get("guilds") or []
                         for ch in (g.get("channels") or []))
    total_messages += sum(len(ch.get("messages") or []) for ch in (data.get("dms") or []))

    return {
        "meta": meta,
        "guilds": guilds,
        "dms": dms,
        "total_messages": total_messages,
    }, None


def archive_channel_messages(name, loc):
    """Messages for one channel of an archive.

    loc is 'guild:<guild_id>:<channel_id>' or 'dm:<channel_id>'.
    Returns (channel_name, messages, error_str).
    """
    data, err = load_archive_index(name)
    if err or data is None:
        return None, None, err or "Archive not found."

    parts = (loc or "").split(":")
    if len(parts) == 3 and parts[0] == "guild":
        _, guild_id, channel_id = parts
        for g in data.get("guilds") or []:
            if str(g.get("id")) != str(guild_id):
                continue
            for ch in g.get("channels") or []:
                if str(ch.get("id")) == str(channel_id):
                    return ch.get("name") or f"channel_{channel_id}", ch.get("messages") or [], None
        return None, None, "Channel not found in archive."

    if len(parts) == 2 and parts[0] == "dm":
        _, channel_id = parts
        for ch in data.get("dms") or []:
            if str(ch.get("id")) == str(channel_id):
                return ch.get("name") or f"dm_{channel_id}", ch.get("messages") or [], None
        return None, None, "DM not found in archive."

    return None, None, "Invalid location."


# ── Cross-archive search ───────────────────────────────

def search_archives(query, limit=200, max_archives=15):
    """Search message content across already-created archives.

    Searches the most recent `max_archives` archives (newest first) so the
    request stays fast even with a big backlog. Returns (results_list, total)
    where results_list is capped at `limit`; matches outside the cap still
    count toward total.
    """
    query = (query or "").strip()
    if not query:
        return [], 0
    q = query.lower()

    results = []
    total = 0

    def _append(kind, archive_label, guild, channel, msg):
        nonlocal total
        total += 1
        if len(results) < limit:
            results.append({
                "kind": kind,
                "archive": archive_label,
                "guild": guild,
                "channel": channel,
                "author": msg.get("author", "Unknown"),
                "timestamp": msg.get("timestamp"),
                "content": msg.get("content", ""),
                "attachments": msg.get("attachments") or [],
            })

    def _matches(msg):
        content = (msg.get("content") or "").lower()
        attach = " ".join(msg.get("attachments") or []).lower()
        return q in content or q in attach

    scanned = 0
    for entry in list_archives():
        if scanned >= max_archives:
            break
        name = entry.get("name", "")
        if not name:
            continue
        data, err = load_archive_index(name)
        if err or data is None:
            continue
        scanned += 1
        label = entry.get("scope_name") or entry.get("username") or name

        for guild in data.get("guilds") or []:
            for ch in guild.get("channels") or []:
                for msg in ch.get("messages") or []:
                    if _matches(msg):
                        _append("guild", label, guild.get("name", "Unknown"),
                                ch.get("name", "channel"), msg)

        for ch in data.get("dms") or []:
            for msg in ch.get("messages") or []:
                if _matches(msg):
                    _append("dm", label, "Direct Messages", ch.get("name", "DM"), msg)

    return results, total
