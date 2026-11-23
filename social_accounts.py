"""Social Accounts Manager for NeonClipping.
Supports YouTube, TikTok, Facebook (Pages & Reels), and Instagram.
Provides OAuth2 connect flows, direct access/page token addition, token refreshing,
and direct video/short/reel uploading for all platforms.
Works with zero external dependencies (pure Python standard library) and uses httpx if installed.
"""
import base64
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("NEONCLIPPING_DATA_DIR", str(ROOT / "data"))).resolve()
SOCIAL_DIR = DATA / "social"
SOCIAL_DIR.mkdir(parents=True, exist_ok=True)

# Migrate legacy top-level files if present
for _name in ("social_accounts.json", "social_apps.json", "social_posts.json"):
    _old = DATA / _name
    if _old.exists():
        try:
            _target = SOCIAL_DIR / (_name.replace("social_", ""))
            if not _target.exists():
                _target.write_text(_old.read_text(encoding="utf-8"), encoding="utf-8")
            _old.unlink(missing_ok=True)
        except Exception:
            pass

_lock = threading.RLock()
_states: dict = {}  # state -> (created_at, platform)

PLATFORMS = {
    "youtube": {"name": "YouTube", "icon": "youtube", "color": "#FF0000"},
    "tiktok": {"name": "TikTok", "icon": "tiktok", "color": "#00F2FE"},
    "facebook": {"name": "Facebook", "icon": "facebook", "color": "#1877F2"},
    "instagram": {"name": "Instagram", "icon": "instagram", "color": "#E1306C"},
}

APPS_CONFIG = {
    "youtube": ("Google Client ID", "Google Client Secret", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"),
    "tiktok": ("TikTok Client Key", "TikTok Client Secret", "TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET"),
    "facebook": ("Meta App ID", "Meta App Secret", "META_APP_ID", "META_APP_SECRET"),
    "instagram": ("Meta/Instagram App ID", "Meta/Instagram App Secret", "META_APP_ID", "META_APP_SECRET"),
}

YT_SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"
TT_SCOPES = "user.info.basic,video.publish"
FB_SCOPES = "pages_show_list,pages_read_engagement,pages_manage_posts,publish_video"
IG_SCOPES = "instagram_business_basic,instagram_business_content_publish"


# ------------------------------------------------------------------ HTTP client helper
def _http_request(url, method="GET", headers=None, data=None, json_data=None, timeout=60):
    """Universal HTTP request handling using urllib, with httpx fallback if installed."""
    try:
        import httpx
        with httpx.Client(timeout=timeout) as client:
            kwargs = {"headers": headers or {}}
            if json_data is not None:
                kwargs["json"] = json_data
            elif data is not None:
                kwargs["content" if isinstance(data, (bytes, bytearray)) else "data"] = data
            res = client.request(method, url, **kwargs)
            return res.status_code, res.headers, res.content, res.text
    except ImportError:
        pass

    # Standard library fallback
    hdrs = dict(headers or {})
    body = None
    if json_data is not None:
        body = json.dumps(json_data).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json; charset=utf-8")
    elif data is not None:
        if isinstance(data, dict):
            body = urllib.parse.urlencode(data).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
        elif isinstance(data, str):
            body = data.encode("utf-8")
        else:
            body = data

    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp_bytes = resp.read()
            return resp.status, dict(resp.headers), resp_bytes, resp_bytes.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        err_bytes = e.read()
        return e.code, dict(e.headers), err_bytes, err_bytes.decode("utf-8", errors="replace")
    except Exception as e:
        raise RuntimeError(f"Request to {url} failed: {e}") from e


def _check(code, text, what):
    if code >= 400:
        raise RuntimeError(f"{what} failed ({code}): {text[:350]}")
    try:
        return json.loads(text)
    except Exception:
        return text


# ------------------------------------------------------------------ Persistence
def _accounts_file() -> Path:
    return SOCIAL_DIR / "accounts.json"


def _apps_file() -> Path:
    return SOCIAL_DIR / "apps.json"


def _read_json(p: Path, default):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(p: Path, data):
    with _lock:
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(p)


# ------------------------------------------------------------------ Server Apps Configuration
def get_public_url() -> str:
    port = os.environ.get("NEONCLIPPING_PORT", "8770")
    default = f"http://127.0.0.1:{port}"
    return os.environ.get("NEONCLIPPING_PUBLIC_URL", default).rstrip("/")


def app_keys(platform: str) -> tuple:
    """Returns (client_id, client_secret) from social_apps.json or environment."""
    saved = _read_json(_apps_file(), {}).get(platform, {})
    cid = saved.get("id") or ""
    sec = saved.get("secret") or ""
    if cid and sec:
        return cid.strip(), sec.strip()
    if platform in APPS_CONFIG:
        _, _, env_id, env_sec = APPS_CONFIG[platform]
        cid = os.environ.get(env_id, "").strip() or cid
        sec = os.environ.get(env_sec, "").strip() or sec
    return cid.strip(), sec.strip()


def save_app_keys(platform: str, client_id: str, client_secret: str) -> dict:
    if platform not in PLATFORMS:
        raise ValueError(f"Unknown platform: {platform}")
    with _lock:
        data = _read_json(_apps_file(), {})
        data[platform] = {
            "id": client_id.strip(),
            "secret": client_secret.strip(),
            "updated": time.time(),
        }
        _write_json(_apps_file(), data)
    return list_apps()


def list_apps() -> dict:
    res = {}
    pub_url = get_public_url()
    for plat, info in PLATFORMS.items():
        cid, sec = app_keys(plat)
        labels = APPS_CONFIG.get(plat, ("Client ID", "Client Secret", "", ""))
        res[plat] = {
            "name": info["name"],
            "ready": bool(cid and sec),
            "id_label": labels[0],
            "secret_label": labels[1],
            "id_hint": (cid[:6] + "…" + cid[-4:]) if len(cid) > 12 else cid,
            "redirect_uri": f"{pub_url}/oauth/{plat}/callback",
        }
    return res


# ------------------------------------------------------------------ Account List / Management
def list_accounts() -> list:
    accs = _read_json(_accounts_file(), [])
    out = []
    for a in accs:
        p = a.get("platform", "")
        pinfo = PLATFORMS.get(p, {"name": p.title(), "icon": "share", "color": "#fff"})
        out.append({
            "id": a["id"],
            "platform": p,
            "platform_name": pinfo["name"],
            "name": a.get("name", "Unnamed Account"),
            "detail": a.get("detail", ""),
            "avatar": a.get("avatar", ""),
            "added": a.get("added", 0),
            "has_token": bool(a.get("access_token") or a.get("token") or a.get("page_token")),
        })
    return sorted(out, key=lambda x: x["added"], reverse=True)


def get_account(aid: str) -> dict:
    for a in _read_json(_accounts_file(), []):
        if a["id"] == aid:
            return a
    return None


def save_account(acc: dict):
    with _lock:
        accs = _read_json(_accounts_file(), [])
        accs = [a for a in accs if a["id"] != acc["id"]]
        accs.append(acc)
        _write_json(_accounts_file(), accs)


def remove_account(aid: str):
    with _lock:
        accs = _read_json(_accounts_file(), [])
        accs = [a for a in accs if a["id"] != aid]
        _write_json(_accounts_file(), accs)


# ------------------------------------------------------------------ OAuth Flows
def _create_state(platform: str) -> str:
    st = secrets.token_urlsafe(16)
    with _lock:
        now = time.time()
        for k in list(_states.keys()):
            if now - _states[k][0] > 900:
                _states.pop(k, None)
        _states[st] = (now, platform)
    return st


def _verify_state(state: str, platform: str):
    with _lock:
        item = _states.pop(state, None)
    if not item or item[1] != platform or time.time() - item[0] > 900:
        raise ValueError("Sign-in session expired or invalid. Please click Connect again.")


def connect_url(platform: str) -> str:
    if platform not in PLATFORMS:
        raise ValueError(f"Unknown platform: {platform}")
    cid, _ = app_keys(platform)
    if not cid:
        raise ValueError(f"{PLATFORMS[platform]['name']} App keys (Client ID/Secret) are not configured yet.")

    st = _create_state(platform)
    redir = f"{get_public_url()}/oauth/{platform}/callback"

    if platform == "youtube":
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
            "client_id": cid,
            "redirect_uri": redir,
            "response_type": "code",
            "scope": YT_SCOPES,
            "access_type": "offline",
            "prompt": "consent",
            "state": st,
        })
    elif platform == "tiktok":
        return "https://open.tiktokapis.com/v2/auth/authorize/?" + urllib.parse.urlencode({
            "client_key": cid,
            "redirect_uri": redir,
            "response_type": "code",
            "scope": TT_SCOPES,
            "state": st,
        })
    elif platform in ("facebook", "instagram"):
        scope = FB_SCOPES if platform == "facebook" else IG_SCOPES
        return "https://www.facebook.com/v21.0/dialog/oauth?" + urllib.parse.urlencode({
            "client_id": cid,
            "redirect_uri": redir,
            "response_type": "code",
            "scope": scope,
            "state": st,
        })
    raise ValueError(f"OAuth URL not implemented for {platform}")


def connect_finish(platform: str, code: str, state: str) -> dict:
    _verify_state(state, platform)
    code = code.split("#")[0].strip()
    if not code:
        raise ValueError("No authentication code returned from provider.")

    if platform == "youtube":
        return _finish_youtube(code)
    elif platform == "tiktok":
        return _finish_tiktok(code)
    elif platform == "facebook":
        return _finish_facebook(code)
    elif platform == "instagram":
        return _finish_instagram(code)
    raise ValueError(f"Unknown platform: {platform}")


# ------------------------------------------------------------------ Direct Token Addition
def add_direct_token(platform: str, name: str, token: str, extra: dict = None) -> dict:
    """Connect an account directly with an API/Access Token or Page Token."""
    platform = platform.lower().strip()
    if platform not in PLATFORMS:
        raise ValueError(f"Platform {platform} is not supported.")
    token = token.strip()
    if not token:
        raise ValueError("Token cannot be empty.")
    extra = extra or {}
    aid = f"{platform}_{secrets.token_hex(6)}"

    if platform == "facebook":
        # Verify page or user token
        code, _, _, text = _http_request(
            f"https://graph.facebook.com/v21.0/me?fields=id,name,picture&access_token={token}"
        )
        data = _check(code, text, "Validating Facebook Token")
        account_name = extra.get("name") or data.get("name") or name or "Facebook Account"
        page_id = extra.get("page_id") or data.get("id")
        acc = {
            "id": f"fb_{page_id}",
            "platform": "facebook",
            "name": account_name,
            "page_id": str(page_id),
            "access_token": token,
            "detail": "Facebook Page" if extra.get("is_page") else "Facebook Account",
            "avatar": data.get("picture", {}).get("data", {}).get("url", ""),
            "added": time.time(),
        }
    elif platform == "youtube":
        # Verify Google/YouTube token
        code, _, _, text = _http_request(
            "https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true",
            headers={"Authorization": f"Bearer {token}"},
        )
        data = _check(code, text, "Validating YouTube Token")
        items = data.get("items", [])
        if not items:
            raise ValueError("No YouTube channels found for this token.")
        item = items[0]
        acc = {
            "id": f"yt_{item['id']}",
            "platform": "youtube",
            "name": item["snippet"]["title"],
            "access_token": token,
            "detail": "YouTube Channel",
            "avatar": item["snippet"]["thumbnails"]["default"]["url"],
            "added": time.time(),
        }
    elif platform == "tiktok":
        acc = {
            "id": aid,
            "platform": "tiktok",
            "name": name or "TikTok Creator",
            "access_token": token,
            "open_id": extra.get("open_id", ""),
            "detail": "TikTok Creator",
            "added": time.time(),
        }
    elif platform == "instagram":
        code, _, _, text = _http_request(
            f"https://graph.instagram.com/me?fields=user_id,username,account_type&access_token={token}"
        )
        data = _check(code, text, "Validating Instagram Token")
        username = data.get("username", "")
        uid = str(data.get("user_id") or data.get("id") or secrets.token_hex(4))
        acc = {
            "id": f"ig_{uid}",
            "platform": "instagram",
            "name": f"@{username}" if username else (name or "Instagram Account"),
            "user_id": uid,
            "access_token": token,
            "detail": "Instagram Business/Creator",
            "added": time.time(),
        }
    save_account(acc)
    return acc


# ------------------------------------------------------------------ Provider Details
def _finish_youtube(code: str) -> dict:
    cid, sec = app_keys("youtube")
    redir = f"{get_public_url()}/oauth/youtube/callback"
    code_st, _, _, text = _http_request(
        "https://oauth2.googleapis.com/token",
        method="POST",
        data={
            "code": code,
            "client_id": cid,
            "client_secret": sec,
            "redirect_uri": redir,
            "grant_type": "authorization_code",
        },
    )
    tok = _check(code_st, text, "YouTube Token Exchange")
    acc_tok = tok.get("access_token")
    ref_tok = tok.get("refresh_token")
    if not acc_tok:
        raise ValueError("Google did not return an access token.")

    # Read channel details
    c_st, _, _, c_text = _http_request(
        "https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true",
        headers={"Authorization": f"Bearer {acc_tok}"},
    )
    ch = _check(c_st, c_text, "Reading YouTube Channel")
    items = ch.get("items", [])
    if not items:
        raise ValueError("This Google account has no YouTube channel. Please create one on YouTube first.")
    item = items[0]
    acc = {
        "id": f"yt_{item['id']}",
        "platform": "youtube",
        "name": item["snippet"]["title"],
        "channel_id": item["id"],
        "access_token": acc_tok,
        "refresh_token": ref_tok or "",
        "expires_at": time.time() + tok.get("expires_in", 3600) - 60,
        "avatar": item["snippet"]["thumbnails"]["default"]["url"],
        "detail": "YouTube Channel",
        "added": time.time(),
    }
    save_account(acc)
    return acc


def _yt_token(acc: dict) -> str:
    if acc.get("expires_at", 0) > time.time() and acc.get("access_token"):
        return acc["access_token"]
    ref = acc.get("refresh_token")
    if not ref:
        return acc.get("access_token", "")
    cid, sec = app_keys("youtube")
    code_st, _, _, text = _http_request(
        "https://oauth2.googleapis.com/token",
        method="POST",
        data={
            "client_id": cid,
            "client_secret": sec,
            "refresh_token": ref,
            "grant_type": "refresh_token",
        },
    )
    tok = _check(code_st, text, "Refreshing YouTube Token")
    acc["access_token"] = tok["access_token"]
    acc["expires_at"] = time.time() + tok.get("expires_in", 3600) - 60
    save_account(acc)
    return acc["access_token"]


def _finish_tiktok(code: str) -> dict:
    cid, sec = app_keys("tiktok")
    redir = f"{get_public_url()}/oauth/tiktok/callback"
    code_st, _, _, text = _http_request(
        "https://open.tiktokapis.com/v2/oauth/token/",
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": cid,
            "client_secret": sec,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redir,
        },
    )
    tok = _check(code_st, text, "TikTok Token Exchange")
    data = tok.get("data") or tok
    acc_tok = data.get("access_token")
    if not acc_tok:
        raise ValueError("TikTok sign-in did not return access token.")

    open_id = data.get("open_id", "")
    acc = {
        "id": f"tt_{open_id or secrets.token_hex(4)}",
        "platform": "tiktok",
        "name": "TikTok Creator",
        "access_token": acc_tok,
        "refresh_token": data.get("refresh_token", ""),
        "open_id": open_id,
        "expires_at": time.time() + int(data.get("expires_in", 7200)) - 120,
        "detail": "TikTok Creator Account",
        "added": time.time(),
    }
    # Attempt to query creator info
    try:
        q_st, _, _, q_text = _http_request(
            "https://open.tiktokapis.com/v2/post/publish/creator_info/query/",
            method="POST",
            headers={"Authorization": f"Bearer {acc_tok}", "Content-Type": "application/json; charset=UTF-8"},
            json_data={},
        )
        cinfo = _check(q_st, q_text, "TikTok Creator Query").get("data", {})
        if cinfo.get("creator_username"):
            acc["name"] = "@" + cinfo["creator_username"]
        elif cinfo.get("creator_nickname"):
            acc["name"] = cinfo["creator_nickname"]
        if cinfo.get("creator_avatar_url"):
            acc["avatar"] = cinfo["creator_avatar_url"]
    except Exception:
        pass
    save_account(acc)
    return acc


def _tt_token(acc: dict) -> str:
    if acc.get("expires_at", 0) > time.time() and acc.get("access_token"):
        return acc["access_token"]
    ref = acc.get("refresh_token")
    if not ref:
        return acc.get("access_token", "")
    cid, sec = app_keys("tiktok")
    code_st, _, _, text = _http_request(
        "https://open.tiktokapis.com/v2/oauth/token/",
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": cid,
            "client_secret": sec,
            "grant_type": "refresh_token",
            "refresh_token": ref,
        },
    )
    tok = _check(code_st, text, "Refreshing TikTok Token").get("data", {})
    acc["access_token"] = tok.get("access_token", acc["access_token"])
    acc["refresh_token"] = tok.get("refresh_token", ref)
    acc["expires_at"] = time.time() + int(tok.get("expires_in", 7200)) - 120
    save_account(acc)
    return acc["access_token"]


def _finish_facebook(code: str) -> dict:
    cid, sec = app_keys("facebook")
    redir = f"{get_public_url()}/oauth/facebook/callback"
    code_st, _, _, text = _http_request(
        f"https://graph.facebook.com/v21.0/oauth/access_token?client_id={cid}&redirect_uri={redir}&client_secret={sec}&code={code}"
    )
    tok = _check(code_st, text, "Facebook Token Exchange")
    user_tok = tok.get("access_token")
    if not user_tok:
        raise ValueError("Facebook login did not return user access token.")

    # Read user's pages
    p_st, _, _, p_text = _http_request(
        f"https://graph.facebook.com/v21.0/me/accounts?fields=id,name,access_token,picture&access_token={user_tok}"
    )
    pages = _check(p_st, p_text, "Reading Facebook Pages").get("data", [])
    if not pages:
        # Save as personal account
        m_st, _, _, m_text = _http_request(
            f"https://graph.facebook.com/v21.0/me?fields=id,name,picture&access_token={user_tok}"
        )
        me = _check(m_st, m_text, "Reading Facebook Profile")
        acc = {
            "id": f"fb_{me['id']}",
            "platform": "facebook",
            "name": me.get("name", "Facebook Account"),
            "access_token": user_tok,
            "detail": "Facebook Profile",
            "avatar": me.get("picture", {}).get("data", {}).get("url", ""),
            "added": time.time(),
        }
        save_account(acc)
        return acc

    # Save pages
    last_acc = None
    for page in pages:
        acc = {
            "id": f"fb_page_{page['id']}",
            "platform": "facebook",
            "name": page["name"],
            "page_id": page["id"],
            "access_token": page.get("access_token", user_tok),
            "detail": "Facebook Page",
            "avatar": page.get("picture", {}).get("data", {}).get("url", ""),
            "added": time.time(),
        }
        save_account(acc)
        last_acc = acc
    return last_acc


def _finish_instagram(code: str) -> dict:
    cid, sec = app_keys("instagram")
    redir = f"{get_public_url()}/oauth/instagram/callback"
    code_st, _, _, text = _http_request(
        "https://api.instagram.com/oauth/access_token",
        method="POST",
        data={
            "client_id": cid,
            "client_secret": sec,
            "grant_type": "authorization_code",
            "redirect_uri": redir,
            "code": code,
        },
    )
    short = _check(code_st, text, "Instagram Token Exchange")
    if isinstance(short.get("data"), list) and short["data"]:
        short = short["data"][0]
    tok_st, _, _, tok_text = _http_request(
        f"https://graph.instagram.com/access_token?grant_type=ig_exchange_token&client_secret={sec}&access_token={short['access_token']}"
    )
    long_tok = _check(tok_st, tok_text, "Exchanging Long-lived Instagram Token")
    token = long_tok.get("access_token", short["access_token"])
    return add_direct_token("instagram", "", token)


# ------------------------------------------------------------------ Direct Upload Functions
def youtube_upload(acc: dict, video_path: Path, title: str, description: str, tags: list = None, privacy: str = "public") -> tuple:
    """Uploads a video / Short to YouTube using the v3 Resumable Upload API.
    Returns (video_id, youtube_url).
    """
    token = _yt_token(acc)
    clean_title = re.sub(r"[<>]", "", title or "New Clip")[:100]
    clean_desc = re.sub(r"[<>]", "", description or "")[:4900]
    if "#Shorts" not in clean_desc and "#shorts" not in clean_desc:
        clean_desc = (clean_desc + "\n\n#Shorts").strip()

    metadata = {
        "snippet": {
            "title": clean_title,
            "description": clean_desc,
            "tags": (tags or [])[:15],
            "categoryId": "22",  # People & Blogs / Entertainment
        },
        "status": {
            "privacyStatus": privacy if privacy in ("public", "unlisted", "private") else "public",
            "selfDeclaredMadeForKids": False,
        },
    }

    size = video_path.stat().st_size
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": "video/mp4",
        "X-Upload-Content-Length": str(size),
    }

    # Start Resumable Session
    c_st, c_headers, _, c_text = _http_request(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
        method="POST",
        headers=headers,
        json_data=metadata,
        timeout=60,
    )
    if c_st >= 400:
        raise RuntimeError(f"YouTube upload init failed ({c_st}): {c_text[:300]}")

    upload_url = c_headers.get("Location") or c_headers.get("location")
    if not upload_url:
        raise RuntimeError("YouTube did not provide upload URL in Location header.")

    # Upload video content
    with video_path.open("rb") as f:
        file_bytes = f.read()

    u_st, _, _, u_text = _http_request(
        upload_url,
        method="PUT",
        headers={"Content-Type": "video/mp4", "Content-Length": str(size)},
        data=file_bytes,
        timeout=1800,
    )
    res = _check(u_st, u_text, "Uploading video content to YouTube")
    vid = res.get("id", "")
    url = f"https://www.youtube.com/shorts/{vid}" if vid else ""
    return vid, url


def facebook_upload(acc: dict, video_path: Path, title: str, description: str, is_reel: bool = True, progress_cb=None) -> tuple:
    """Uploads video or Reel to Facebook Page via Meta Graph API using chunked Resumable Upload.
    Handles files of any size (even hundreds of megabytes) reliably without HTTP 413 errors.
    Returns (video_id, post_url).
    """
    token = acc.get("access_token", "")
    page_id = acc.get("page_id") or "me"
    size = video_path.stat().st_size
    clean_title = (title or "Clip")[:100]
    clean_desc = (description or "")[:5000]

    # Phase 1: Start upload session
    start_url = f"https://graph.facebook.com/v21.0/{page_id}/videos"
    start_data = urllib.parse.urlencode({
        "upload_phase": "start",
        "file_size": str(size),
        "access_token": token
    }).encode("utf-8")

    st, _, _, text = _http_request(start_url, method="POST", data=start_data, timeout=60)
    res_start = _check(st, text, "Starting Facebook Video Upload Session")

    video_id = str(res_start.get("video_id") or "")
    session_id = str(res_start.get("upload_session_id") or "")
    start_offset = int(res_start.get("start_offset") or 0)
    end_offset = int(res_start.get("end_offset") or min(start_offset + 4 * 1024 * 1024, size))

    if not session_id:
        raise RuntimeError("Facebook did not return an upload_session_id.")

    # Phase 2: Transfer chunks (4MB per chunk)
    chunk_size = 4 * 1024 * 1024
    transfer_url = f"https://graph-video.facebook.com/v21.0/{page_id}/videos"

    with video_path.open("rb") as f:
        while start_offset < size:
            target_end = min(start_offset + chunk_size, size)
            f.seek(start_offset)
            chunk_bytes = f.read(target_end - start_offset)

            boundary = "----NeonFBChunk" + secrets.token_hex(8)
            body = bytearray()
            def add_field(n, v):
                body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{n}\"\r\n\r\n{v}\r\n".encode("utf-8"))

            add_field("upload_phase", "transfer")
            add_field("upload_session_id", session_id)
            add_field("start_offset", str(start_offset))
            add_field("access_token", token)
            body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"video_file_chunk\"; filename=\"chunk.mp4\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode("utf-8"))
            body.extend(chunk_bytes)
            body.extend(f"\r\n--{boundary}--\r\n".encode("utf-8"))

            st, _, _, text = _http_request(
                transfer_url,
                method="POST",
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                data=bytes(body),
                timeout=300
            )
            res_transfer = _check(st, text, f"Uploading video chunk at offset {start_offset}")

            new_start = int(res_transfer.get("start_offset", target_end))
            if new_start <= start_offset:
                start_offset = target_end
            else:
                start_offset = new_start

            if progress_cb and size > 0:
                pct = min(99, int((start_offset / size) * 100))
                progress_cb(pct)

    # Phase 3: Finish upload session
    finish_url = f"https://graph.facebook.com/v21.0/{page_id}/videos"
    finish_params = {
        "upload_phase": "finish",
        "upload_session_id": session_id,
        "title": clean_title,
        "description": clean_desc,
        "published": "true" if is_reel else "false",
        "access_token": token
    }
    finish_data = urllib.parse.urlencode(finish_params).encode("utf-8")
    st, _, _, text = _http_request(finish_url, method="POST", data=finish_data, timeout=60)
    _check(st, text, "Finishing Facebook Video Upload")

    if progress_cb:
        progress_cb(100)

    url = f"https://www.facebook.com/{page_id}/videos/{video_id}" if (video_id and page_id != "me") else f"https://www.facebook.com/{video_id}"
    return video_id, url


def tiktok_upload(acc: dict, video_path: Path, caption: str, privacy: str = "public") -> tuple:
    """Uploads a video to TikTok using the Content Posting API.
    Returns (publish_id, video_url).
    """
    token = _tt_token(acc)
    size = video_path.stat().st_size
    priv_map = {
        "public": "PUBLIC_TO_EVERYONE",
        "unlisted": "FOLLOWER_OF_CREATOR",
        "private": "SELF_ONLY",
    }
    priv_level = priv_map.get(privacy, "PUBLIC_TO_EVERYONE")

    init_body = {
        "post_info": {
            "title": caption[:2200],
            "privacy_level": priv_level,
            "disable_duet": False,
            "disable_comment": False,
            "disable_stitch": False,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": size,
            "chunk_size": size,
            "total_chunk_count": 1,
        },
    }

    # Step 1: Init post
    i_st, _, _, i_text = _http_request(
        "https://open.tiktokapis.com/v2/post/publish/video/init/",
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        json_data=init_body,
        timeout=60,
    )
    init_res = _check(i_st, i_text, "Starting TikTok upload").get("data", {})
    publish_id = init_res.get("publish_id")
    upload_url = init_res.get("upload_url")
    if not upload_url:
        raise RuntimeError("TikTok did not provide upload URL.")

    # Step 2: Upload file
    with video_path.open("rb") as f:
        file_bytes = f.read()

    u_st, _, _, u_text = _http_request(
        upload_url,
        method="PUT",
        headers={
            "Content-Type": "video/mp4",
            "Content-Length": str(size),
            "Content-Range": f"bytes 0-{size - 1}/{size}",
        },
        data=file_bytes,
        timeout=1800,
    )
    if u_st not in (200, 201, 206):
        raise RuntimeError(f"Uploading file to TikTok failed ({u_st}): {u_text[:200]}")

    # Step 3: Poll status
    post_url = ""
    for _ in range(30):
        time.sleep(4)
        s_st, _, _, s_text = _http_request(
            "https://open.tiktokapis.com/v2/post/publish/status/fetch/",
            method="POST",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json_data={"publish_id": publish_id},
            timeout=30,
        )
        st_data = _check(s_st, s_text, "Checking TikTok status").get("data", {})
        status = st_data.get("status")
        if status == "PUBLISH_COMPLETE":
            ids = st_data.get("publicly_available_post_id") or st_data.get("publicaly_available_post_id") or []
            if ids:
                post_url = f"https://www.tiktok.com/video/{ids[0]}"
            break
        elif status == "FAILED":
            raise RuntimeError(f"TikTok post failed: {st_data.get('fail_reason', 'unknown')}")

    return publish_id, post_url


def instagram_upload(acc: dict, video_path: Path, caption: str) -> tuple:
    """Uploads Reel to Instagram Business/Creator account."""
    token = acc.get("access_token", "")
    uid = acc.get("user_id", "")
    size = video_path.stat().st_size

    # Step 1: Create media container
    c_st, _, _, c_text = _http_request(
        f"https://graph.instagram.com/{uid}/media",
        method="POST",
        headers={"Authorization": f"Bearer {token}"},
        data={
            "media_type": "REELS",
            "upload_type": "resumable",
            "caption": caption[:2200],
            "share_to_feed": "true",
        },
        timeout=60,
    )
    container = _check(c_st, c_text, "Starting Instagram Reel container")
    container_id = container.get("id")

    # Step 2: Upload file bytes
    with video_path.open("rb") as f:
        file_bytes = f.read()

    u_st, _, _, u_text = _http_request(
        f"https://rupload.facebook.com/ig-api-upload/v21.0/{container_id}",
        method="POST",
        headers={
            "Authorization": f"OAuth {token}",
            "offset": "0",
            "file_size": str(size),
        },
        data=file_bytes,
        timeout=1800,
    )
    if u_st not in (200, 201):
        raise RuntimeError(f"Instagram file upload failed ({u_st}): {u_text[:200]}")

    # Step 3: Wait for processing
    for _ in range(40):
        time.sleep(5)
        st_st, _, _, st_text = _http_request(
            f"https://graph.instagram.com/{container_id}?fields=status_code,status",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        st_data = _check(st_st, st_text, "Checking Instagram processing status")
        if st_data.get("status_code") in ("FINISHED", "PUBLISHED"):
            break
        elif st_data.get("status_code") in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram processing error: {st_data.get('status')}")

    # Step 4: Publish
    p_st, _, _, p_text = _http_request(
        f"https://graph.instagram.com/{uid}/media_publish",
        method="POST",
        headers={"Authorization": f"Bearer {token}"},
        data={"creation_id": container_id},
        timeout=60,
    )
    pub = _check(p_st, p_text, "Publishing Instagram Reel")
    pub_id = pub.get("id", container_id)
    return pub_id, f"https://www.instagram.com/reel/{pub_id}"

