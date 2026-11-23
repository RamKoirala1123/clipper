"""Post queue and dispatcher for NeonClipping.
Posts clips directly or queues them for publishing to YouTube, TikTok, Facebook, and Instagram.
Tracks statuses, retries, and records links to the published posts on the clip.
"""
import json
import os
import secrets
import threading
import time
from pathlib import Path
import social_accounts

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("NEONCLIPPING_DATA_DIR", str(ROOT / "data"))).resolve()
DATA.mkdir(parents=True, exist_ok=True)

_lock = threading.RLock()
_FILE = DATA / "social_posts.json"


def _read_posts() -> list:
    if not _FILE.exists():
        return []
    try:
        return json.loads(_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_posts(posts: list):
    with _lock:
        tmp = _FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(posts, indent=2), encoding="utf-8")
        tmp.replace(_FILE)


def _mirror_to_clip(post: dict):
    """Saves posting status onto the clip's json record so clip cards and library can show it."""
    cid = post.get("clip_id")
    if not cid:
        return
    clip_file = DATA / f"{cid}.json"
    if not clip_file.exists():
        return
    try:
        with _lock:
            data = json.loads(clip_file.read_text(encoding="utf-8"))
            posts = data.setdefault("posts", {})
            posts[post["id"]] = {
                "id": post["id"],
                "account_id": post["account_id"],
                "account_name": post["account_name"],
                "platform": post["platform"],
                "status": post["status"],
                "url": post.get("post_url", ""),
                "error": post.get("error"),
                "posted_at": post.get("posted_at"),
            }
            tmp = clip_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(clip_file)
    except Exception:
        pass


def list_posts(clip_id: str = None) -> list:
    posts = _read_posts()
    if clip_id:
        posts = [p for p in posts if p.get("clip_id") == clip_id]
    return sorted(posts, key=lambda p: p.get("created_at", 0), reverse=True)


def get_post(pid: str) -> dict:
    for p in _read_posts():
        if p["id"] == pid:
            return p
    return None


def execute_post(p: dict):
    """Executes the upload for a single post item."""
    acc = social_accounts.get_account(p["account_id"])
    if not acc:
        p["status"] = "failed"
        p["error"] = "Account was disconnected or not found."
        _save_post_update(p)
        return

    video_path = DATA / f"{p['clip_id']}.mp4"
    if not video_path.exists():
        p["status"] = "failed"
        p["error"] = f"Video file not found: {video_path.name}"
        _save_post_update(p)
        return

    p["status"] = "posting"
    _save_post_update(p)

    title = p.get("title") or "Clip"
    desc = p.get("description") or ""
    tags = p.get("tags") or []
    privacy = p.get("privacy") or "public"

    try:
        plat = acc["platform"]
        if plat == "youtube":
            remote_id, url = social_accounts.youtube_upload(
                acc, video_path, title, desc, tags=tags, privacy=privacy
            )
        elif plat == "facebook":
            remote_id, url = social_accounts.facebook_upload(
                acc, video_path, title, desc, is_reel=True
            )
        elif plat == "tiktok":
            caption = f"{title}\n\n{desc}".strip()
            remote_id, url = social_accounts.tiktok_upload(
                acc, video_path, caption, privacy=privacy
            )
        elif plat == "instagram":
            caption = f"{title}\n\n{desc}".strip()
            remote_id, url = social_accounts.instagram_upload(
                acc, video_path, caption
            )
        else:
            raise RuntimeError(f"Platform {plat} is not supported.")

        p["status"] = "posted"
        p["posted_at"] = time.time()
        p["remote_id"] = remote_id
        p["post_url"] = url
        p["error"] = None
    except Exception as e:
        p["tries"] = p.get("tries", 0) + 1
        p["error"] = str(e)[-400:]
        p["status"] = "failed"

    _save_post_update(p)


def _save_post_update(post: dict):
    with _lock:
        posts = _read_posts()
        for i, item in enumerate(posts):
            if item["id"] == post["id"]:
                posts[i] = post
                break
        else:
            posts.append(post)
        _save_posts(posts)
    _mirror_to_clip(post)


def enqueue(clip_id: str, account_ids: list, title: str, description: str, tags: list = None, privacy: str = "public", now: bool = True) -> list:
    """Enqueues posts for multiple accounts and runs them if now=True."""
    tags = tags or []
    created = []

    # Read default title / hook from clip json if not provided
    clip_file = DATA / f"{clip_id}.json"
    if clip_file.exists():
        try:
            cj = json.loads(clip_file.read_text(encoding="utf-8"))
            settings = cj.get("settings", {})
            title = title or settings.get("title") or settings.get("hook") or "NeonClip"
            description = description or settings.get("post_caption") or ""
        except Exception:
            pass

    for aid in account_ids:
        acc = social_accounts.get_account(aid)
        if not acc:
            continue
        p = {
            "id": secrets.token_hex(8),
            "clip_id": clip_id,
            "account_id": aid,
            "account_name": acc.get("name", "Social Account"),
            "platform": acc.get("platform", ""),
            "title": title,
            "description": description,
            "tags": tags,
            "privacy": privacy,
            "status": "scheduled",
            "due_at": time.time(),
            "created_at": time.time(),
            "posted_at": None,
            "post_url": "",
            "remote_id": "",
            "tries": 0,
            "error": None,
        }
        with _lock:
            posts = _read_posts()
            posts.append(p)
            _save_posts(posts)
        _mirror_to_clip(p)
        created.append(p)

    if now:
        for p in created:
            threading.Thread(target=execute_post, args=(p,), daemon=True).start()

    return created


def retry(pid: str) -> dict:
    p = get_post(pid)
    if not p:
        raise ValueError("Post not found.")
    p["status"] = "scheduled"
    p["error"] = None
    _save_post_update(p)
    threading.Thread(target=execute_post, args=(p,), daemon=True).start()
    return p


def cancel(pid: str) -> dict:
    p = get_post(pid)
    if not p:
        raise ValueError("Post not found.")
    if p["status"] not in ("scheduled", "posting"):
        raise ValueError("Only waiting or queued posts can be cancelled.")
    p["status"] = "cancelled"
    _save_post_update(p)
    return p


# ------------------------------------------------------------------ Background Runner
def _worker():
    while True:
        time.sleep(10)
        try:
            now = time.time()
            posts = _read_posts()
            due = [p for p in posts if p.get("status") == "scheduled" and p.get("due_at", 0) <= now]
            for p in due:
                threading.Thread(target=execute_post, args=(p,), daemon=True).start()
        except Exception:
            pass


_worker_started = False

def start_worker():
    global _worker_started
    with _lock:
        if not _worker_started:
            _worker_started = True
            threading.Thread(target=_worker, daemon=True).start()


def attach(app, data):
    """Attaches social posting and account routes to the FastAPI application."""
    from fastapi import HTTPException
    from fastapi.responses import RedirectResponse
    import urllib.parse

    start_worker()

    @app.get("/api/accounts")
    def get_accounts():
        return social_accounts.list_accounts()

    @app.delete("/api/accounts/{aid}")
    def delete_account(aid: str):
        social_accounts.remove_account(aid)
        return {"ok": True}

    @app.post("/api/accounts/connect-token")
    def connect_token(b: dict):
        platform = str(b.get("platform", "")).strip().lower()
        name = str(b.get("name", "")).strip()
        token = str(b.get("token", "")).strip()
        extra = b.get("extra") or {}
        try:
            acc = social_accounts.add_direct_token(platform, name, token, extra)
            return {"ok": True, "account": acc}
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.get("/api/apps")
    def get_apps():
        return social_accounts.list_apps()

    @app.post("/api/apps")
    def post_apps(b: dict):
        platform = str(b.get("platform", "")).strip().lower()
        cid = str(b.get("id", "")).strip()
        sec = str(b.get("secret", "")).strip()
        try:
            return social_accounts.save_app_keys(platform, cid, sec)
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.get("/oauth/{platform}/start")
    def oauth_start(platform: str):
        if platform not in social_accounts.PLATFORMS:
            raise HTTPException(404, "Unsupported platform")
        try:
            url = social_accounts.connect_url(platform)
            return RedirectResponse(url, status_code=303)
        except Exception as e:
            q = urllib.parse.urlencode({"social_error": str(e)})
            return RedirectResponse(f"/?{q}", status_code=303)

    @app.get("/oauth/{platform}/callback")
    def oauth_callback(platform: str, code: str = "", state: str = "", error: str = "", error_description: str = ""):
        if platform not in social_accounts.PLATFORMS:
            raise HTTPException(404, "Unsupported platform")
        if error or not code:
            msg = error_description or error or "Login was cancelled or failed."
            q = urllib.parse.urlencode({"social_error": msg})
            return RedirectResponse(f"/?{q}", status_code=303)
        try:
            acc = social_accounts.connect_finish(platform, code, state)
            q = urllib.parse.urlencode({"social_connected": platform, "account_name": acc.get("name", "")})
            return RedirectResponse(f"/?{q}", status_code=303)
        except Exception as e:
            q = urllib.parse.urlencode({"social_error": str(e)})
            return RedirectResponse(f"/?{q}", status_code=303)

    @app.post("/api/posts")
    def create_posts(b: dict):
        clip_id = str(b.get("clip_id") or b.get("file") or "").strip()
        account_ids = [str(a) for a in b.get("accounts", [])]
        if not clip_id:
            raise HTTPException(400, "Provide clip_id or file")
        if not account_ids:
            raise HTTPException(400, "Select at least one social account to post to.")
        title = str(b.get("title", "")).strip()
        desc = str(b.get("description", "")).strip()
        tags = b.get("tags") or []
        privacy = str(b.get("privacy", "public")).strip()
        now = bool(b.get("now", True))
        try:
            made = enqueue(clip_id, account_ids, title, desc, tags=tags, privacy=privacy, now=now)
            return {"ok": True, "queued": len(made), "posts": made}
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.get("/api/posts")
    def get_posts_list(clip_id: str = None):
        return list_posts(clip_id)

    @app.post("/api/posts/{pid}/{action}")
    def post_action(pid: str, action: str):
        try:
            if action == "retry":
                return retry(pid)
            elif action == "cancel":
                return cancel(pid)
            else:
                raise HTTPException(400, "Invalid action. Use retry or cancel.")
        except ValueError as e:
            raise HTTPException(400, str(e))

