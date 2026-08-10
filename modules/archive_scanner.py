"""
Limey Chat Archive
==================
Scans a selfbot account's servers and DMs, then builds a downloadable archive
(JSON + readable HTML) — but only after the owner explicitly confirms on the
dashboard Archives page.

Flow:
  1. POST /api/archive/scan      -> starts an async scan on the bot's event loop
  2. GET  /api/archive/status    -> progress (polled by the page)
  3. POST /api/archive/create    -> owner confirms; JSON + HTML written to disk
  4. GET  /api/archive/download  -> serve the zip
  5. POST /api/archive/delete    -> remove an archive

Everything is stored locally under data/archives/ (gitignored) — nothing is
pushed to the GitHub data repo.
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
                state["results"]["dms"].append({
                    "id": str(dc.id),
                    "name": _channel_name(dc),
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
<h1>🗄️ Chat Archive — {_escape(meta.get('username', 'Unknown'))}</h1>
<p class="meta">Scanned {meta.get('scanned_at', '?')} · {meta.get('guild_count', 0)} servers · {meta.get('dm_count', 0)} DMs · {meta.get('message_count', 0)} messages</p>
<h2>Servers</h2><ul>{list_html}</ul>
</body></html>"""


def _safe_name(text):
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", str(text))
    return (s or "unknown")[:80]


def _guild_dir(g):
    return f"{_safe_name(g['name'])}_{g['id']}"


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


def create_archive(user_id):
    """Write the completed scan out as JSON + HTML archive + zip.

    Returns (info_dict, error_str).
    """
    state = scans.get(user_id)
    if not state or state.get("status") != "ready":
        return None, "No completed scan for this account — run a scan first."
    results = state.get("results") or {"guilds": [], "dms": []}
    guilds, dms = results.get("guilds", []), results.get("dms", [])

    username = _safe_name(state.get("username") or f"account_{user_id}")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"{username}_{timestamp}"
    root = os.path.join(ARCHIVE_DIR, name)

    try:
        os.makedirs(ARCHIVE_DIR, exist_ok=True)
        shutil.rmtree(root, ignore_errors=True)
        os.makedirs(root, exist_ok=True)
        guild_dir = os.path.join(root, "guilds")
        dm_dir = os.path.join(root, "dms")
        os.makedirs(guild_dir, exist_ok=True)
        os.makedirs(dm_dir, exist_ok=True)

        message_count = state.get("messages_total", 0)

        # Per-guild JSON + channel pages
        for g in guilds:
            g_folder = os.path.join(guild_dir, _guild_dir(g))
            os.makedirs(g_folder, exist_ok=True)
            with open(os.path.join(g_folder, "guild.json"), "w", encoding="utf-8") as f:
                json.dump({"name": g["name"], "id": g["id"]}, f, indent=2, ensure_ascii=False)
            with open(os.path.join(g_folder, "index.html"), "w", encoding="utf-8") as f:
                f.write(_build_guild_html(g))
            for ch in g["channels"]:
                base = f"{_safe_name(ch['name'])}_{ch['id']}"
                with open(os.path.join(g_folder, base + ".json"), "w", encoding="utf-8") as f:
                    json.dump({
                        "channel": ch["name"],
                        "guild": g["name"],
                        "guild_id": g["id"],
                        "messages": ch["messages"],
                    }, f, indent=2, ensure_ascii=False)
                with open(os.path.join(g_folder, base + ".html"), "w", encoding="utf-8") as f:
                    f.write(_render_chat_page(f"{g['name']} — {ch['name']}", ch["messages"], ch["name"]))

        # Per-DM JSON + HTML
        for d in dms:
            base = f"{_safe_name(d['name'])}_{d['id']}"
            with open(os.path.join(dm_dir, base + ".json"), "w", encoding="utf-8") as f:
                json.dump({"name": d["name"], "id": d["id"], "messages": d["messages"]},
                          f, indent=2, ensure_ascii=False)
            with open(os.path.join(dm_dir, base + ".html"), "w", encoding="utf-8") as f:
                f.write(_render_chat_page(f"DM — {d['name']}", d["messages"], d["name"]))

        # Index files
        meta = {
            "username": state.get("username") or f"account_{user_id}",
            "user_id": user_id,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "scanned_at": datetime.fromtimestamp(state.get("finished_at") or time.time())
                .isoformat(timespec="seconds"),
            "message_limit": state.get("message_limit"),
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
            "username": meta["username"],
            "created_at": meta["created_at"],
            "guild_count": meta["guild_count"],
            "dm_count": meta["dm_count"],
            "message_count": meta["message_count"],
            "size_bytes": os.path.getsize(zip_path),
            "download": f"/api/archive/download/{name}",
        }
        _update_index(info)
        return info, None
    except Exception as e:
        return None, f"Failed to build archive: {e}"


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
    entries = _load_index()
    # Drop entries whose files no longer exist
    valid = []
    for e in entries:
        name = e.get("name", "")
        if os.path.exists(os.path.join(ARCHIVE_DIR, name + ".zip")):
            valid.append(e)
    if len(valid) != len(entries):
        try:
            with open(INDEX_FILE, "w", encoding="utf-8") as f:
                json.dump({"archives": valid}, f, indent=2, ensure_ascii=False)
        except Exception:
            pass
    return valid


def archive_zip_path(name):
    if not name or not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        return None
    path = os.path.join(ARCHIVE_DIR, name + ".zip")
    return path if os.path.exists(path) else None


def delete_archive(name):
    if not name or not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        return False
    removed = False
    root = os.path.join(ARCHIVE_DIR, name)
    if os.path.isdir(root):
        shutil.rmtree(root, ignore_errors=True)
        removed = True
    zip_path = os.path.join(ARCHIVE_DIR, name + ".zip")
    if os.path.exists(zip_path):
        os.remove(zip_path)
        removed = True
    if removed:
        entries = [e for e in _load_index() if e.get("name") != name]
        try:
            with open(INDEX_FILE, "w", encoding="utf-8") as f:
                json.dump({"archives": entries}, f, indent=2, ensure_ascii=False)
        except Exception:
            pass
    return removed
