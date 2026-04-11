#!/usr/bin/env python3
"""
Poll YouTube channel RSS feeds and publish new videos.

Modes (SOCIAL_OUTPUT_MODE):
  - meta (default): Facebook Page + Instagram via Meta Graph API (needs your own Meta app/tokens).
  - webhook: POST JSON to one or more URLs (Pipedream, Make, custom). No Meta Developer app required
    on your side — the intermediary connects Facebook/Instagram with their OAuth.

State: scripts/published_history.json tracks per-destination success to avoid duplicates and allow retries.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

# -----------------------------------------------------------------------------
# Config (environment variables)
# -----------------------------------------------------------------------------

GRAPH_VERSION = (os.environ.get("META_GRAPH_API_VERSION") or "").strip() or "v21.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_VERSION}"

SCRIPT_DIR = Path(__file__).resolve().parent
HISTORY_PATH = SCRIPT_DIR / "published_history.json"


def _env(name: str, default: str | None = None) -> str | None:
    val = os.environ.get(name, default)
    if val is not None and str(val).strip() == "":
        return default
    return val


def _require_env(name: str) -> str:
    v = _env(name)
    if not v:
        logging.error("Missing required environment variable: %s", name)
        sys.exit(1)
    return v


def _parse_history() -> dict[str, Any]:
    if not HISTORY_PATH.is_file():
        return {"videos": {}}
    try:
        with open(HISTORY_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logging.warning("Could not read history file (%s); starting fresh.", e)
        return {"videos": {}}
    if "videos" not in data or not isinstance(data["videos"], dict):
        data["videos"] = {}
    return data


def _save_history(data: dict[str, Any]) -> None:
    tmp = HISTORY_PATH.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(HISTORY_PATH)


# Atom / YouTube namespaces
_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}


@dataclass
class VideoItem:
    video_id: str
    title: str
    url: str
    thumbnail_url: str
    published: datetime
    source_channel_id: str


def _parse_atom_datetime(text: str | None) -> datetime:
    if not text:
        return datetime.now(timezone.utc)
    # YouTube uses ISO8601 e.g. 2024-01-15T12:00:00+00:00
    try:
        t = text.strip()
        if t.endswith("Z"):
            t = t[:-1] + "+00:00"
        return datetime.fromisoformat(t)
    except ValueError:
        return datetime.now(timezone.utc)


def fetch_channel_feed(channel_id: str) -> list[VideoItem]:
    """Fetch and parse YouTube channel RSS (no API key, no quota)."""
    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={quote(channel_id, safe='')}"
    try:
        r = requests.get(feed_url, timeout=30)
        r.raise_for_status()
    except requests.RequestException as e:
        logging.error("Failed to fetch RSS for channel %s: %s", channel_id, e)
        return []

    try:
        root = ET.fromstring(r.content)
    except ET.ParseError as e:
        logging.error("RSS parse error for %s: %s", channel_id, e)
        return []

    out: list[VideoItem] = []
    for entry in root.findall("atom:entry", _NS):
        vid_el = entry.find("yt:videoId", _NS)
        if vid_el is None or not vid_el.text:
            continue
        video_id = vid_el.text.strip()

        title_el = entry.find("atom:title", _NS)
        title = (title_el.text or "New video").strip()

        link_url = ""
        for link in entry.findall("atom:link", _NS):
            if link.get("rel") == "alternate":
                link_url = link.get("href") or ""
                break
        if not link_url:
            link_url = f"https://www.youtube.com/watch?v={video_id}"

        thumb = ""
        media_group = entry.find("media:group", _NS)
        if media_group is not None:
            th = media_group.find("media:thumbnail", _NS)
            if th is not None:
                thumb = th.get("url") or ""
        if not thumb:
            thumb = f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg"

        published_el = entry.find("atom:published", _NS)
        published = _parse_atom_datetime(published_el.text if published_el is not None else None)

        out.append(
            VideoItem(
                video_id=video_id,
                title=title,
                url=link_url,
                thumbnail_url=thumb,
                published=published,
                source_channel_id=channel_id,
            )
        )
    return out


def _graph_get(path: str, params: dict[str, Any]) -> dict[str, Any] | None:
    try:
        r = requests.get(f"{GRAPH_BASE}{path}", params=params, timeout=60)
        data = r.json()
        if r.status_code >= 400 or "error" in data:
            logging.error("Graph GET error %s: %s", path, data)
            return None
        return data
    except requests.RequestException as e:
        logging.error("Graph GET request failed %s: %s", path, e)
        return None


def _graph_post(path: str, data: dict[str, Any]) -> dict[str, Any] | None:
    try:
        r = requests.post(f"{GRAPH_BASE}{path}", data=data, timeout=120)
        j = r.json()
        if r.status_code >= 400 or "error" in j:
            logging.error("Graph POST error %s: %s", path, j)
            return None
        return j
    except requests.RequestException as e:
        logging.error("Graph POST request failed %s: %s", path, e)
        return None


def post_facebook_page_feed(page_id: str, access_token: str, message: str, link: str) -> bool:
    """Publish a link post to a Facebook Page."""
    result = _graph_post(
        f"/{page_id}/feed",
        {
            "access_token": access_token,
            "message": message,
            "link": link,
        },
    )
    return bool(result and result.get("id"))


def get_instagram_business_account_id(page_id: str, access_token: str) -> str | None:
    data = _graph_get(f"/{page_id}", {"fields": "instagram_business_account", "access_token": access_token})
    if not data:
        return None
    iba = data.get("instagram_business_account") or {}
    iid = iba.get("id")
    return str(iid) if iid else None


def _wait_ig_container_ready(creation_id: str, access_token: str, ig_user_id: str, max_wait: int = 120) -> bool:
    deadline = time.time() + max_wait
    params = {"fields": "status_code", "access_token": access_token}
    while time.time() < deadline:
        data = _graph_get(f"/{creation_id}", params)
        if not data:
            return False
        status = (data.get("status_code") or "").upper()
        if status == "FINISHED":
            return True
        if status in ("ERROR", "EXPIRED"):
            logging.error("Instagram container failed: %s", data)
            return False
        time.sleep(2)
    logging.error("Timeout waiting for Instagram container %s", creation_id)
    return False


def post_instagram_photo_with_caption(
    ig_user_id: str,
    access_token: str,
    image_url: str,
    caption: str,
) -> bool:
    """
    Single image post with caption (includes video URL). Thumbnail must be a public URL
    (YouTube CDN works). Instagram Graph API: create container -> publish.
    """
    create = _graph_post(
        f"/{ig_user_id}/media",
        {
            "access_token": access_token,
            "image_url": image_url,
            "caption": caption[:2200],
        },
    )
    if not create or "id" not in create:
        return False
    creation_id = create["id"]
    if not _wait_ig_container_ready(creation_id, access_token, ig_user_id):
        return False
    pub = _graph_post(
        f"/{ig_user_id}/media_publish",
        {"access_token": access_token, "creation_id": creation_id},
    )
    return bool(pub and pub.get("id"))


def _destination_key_facebook(page_id: str) -> str:
    return f"fb:{page_id}"


def _destination_key_instagram() -> str:
    return "ig"


def _channel_label(video: VideoItem, artists_id: str, beats_id: str) -> str:
    if video.source_channel_id.strip() == artists_id.strip():
        return "artists"
    if video.source_channel_id.strip() == beats_id.strip():
        return "beats"
    return "unknown"


def _parse_webhook_urls() -> list[str]:
    raw = _env("WEBHOOK_URLS") or _env("WEBHOOK_URL")
    if not raw:
        return []
    return [u.strip() for u in raw.split(",") if u.strip()]


def _destination_key_webhook(index: int) -> str:
    return f"wh:{index}"


def post_json_webhook(
    url: str,
    payload: dict[str, Any],
    bearer: str | None = None,
) -> bool:
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "User-Agent": "social-automator/1.0 (+https://github.com/)",
    }
    if bearer:
        headers["Authorization"] = f"Bearer {bearer.strip()}"
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=90)
        if r.status_code >= 400:
            logging.error("Webhook HTTP %s from %s: %s", r.status_code, url[:80], (r.text or "")[:800])
            return False
        return True
    except requests.RequestException as e:
        logging.error("Webhook request failed (%s): %s", url[:80], e)
        return False


def build_webhook_payload(item: VideoItem, artists_id: str, beats_id: str) -> dict[str, Any]:
    return {
        "event": "youtube_new_video",
        "video_id": item.video_id,
        "title": item.title,
        "url": item.url,
        "thumbnail_url": item.thumbnail_url,
        "published": item.published.astimezone(timezone.utc).isoformat(),
        "source_channel_id": item.source_channel_id,
        "source_channel_label": _channel_label(item, artists_id, beats_id),
    }


def main_webhook() -> None:
    """RSS → HTTP POST only; Pipedream/Buffer/Make handle Facebook/Instagram on their side."""
    ch_artists = _require_env("YOUTUBE_CHANNEL_ID_ARTISTS")
    ch_beats = _require_env("YOUTUBE_CHANNEL_ID_BEATS")
    urls = _parse_webhook_urls()
    if not urls:
        logging.error("WEBHOOK mode requires WEBHOOK_URL or WEBHOOK_URLS (comma-separated).")
        sys.exit(1)

    bearer = _env("WEBHOOK_BEARER_TOKEN")

    history = _parse_history()
    videos_state: dict[str, Any] = history["videos"]

    all_items: list[VideoItem] = []
    all_items.extend(fetch_channel_feed(ch_artists))
    all_items.extend(fetch_channel_feed(ch_beats))

    by_id: dict[str, VideoItem] = {}
    for v in all_items:
        if v.video_id not in by_id or v.published < by_id[v.video_id].published:
            by_id[v.video_id] = v
    sorted_items = sorted(by_id.values(), key=lambda x: x.published)

    processed_any = False

    for item in sorted_items:
        vid = item.video_id
        entry = videos_state.get(vid)
        if not isinstance(entry, dict):
            entry = {"destinations": {}}
            videos_state[vid] = entry
        dest = entry.setdefault("destinations", {})
        if not isinstance(dest, dict):
            dest = {}
            entry["destinations"] = dest

        payload = build_webhook_payload(item, ch_artists, ch_beats)

        for idx, wh_url in enumerate(urls):
            key = _destination_key_webhook(idx)
            if dest.get(key):
                continue
            ok = post_json_webhook(wh_url, payload, bearer)
            if ok:
                dest[key] = datetime.now(timezone.utc).isoformat()
                processed_any = True
                logging.info("Webhook OK idx=%s video=%s", idx, vid)
            else:
                logging.warning("Webhook failed idx=%s video=%s (will retry next run)", idx, vid)

        entry["title"] = item.title
        entry["last_seen"] = datetime.now(timezone.utc).isoformat()

    history["videos"] = videos_state
    history["meta"] = {
        "last_run": datetime.now(timezone.utc).isoformat(),
        "mode": "webhook",
        "channels": {"artists": ch_artists, "beats": ch_beats},
    }

    try:
        _save_history(history)
    except OSError as e:
        logging.error("Failed to save history file: %s", e)
        sys.exit(1)

    if processed_any:
        logging.info("Run finished: new webhook deliveries recorded.")
    else:
        logging.info("Run finished: no new successful webhooks (or nothing pending).")


def load_page_tokens() -> list[tuple[str, str]]:
    """
    Returns list of (page_id, access_token).
    Supports FACEBOOK_PAGE_ID_1 + FACEBOOK_PAGE_ACCESS_TOKEN_1, or a single
    FACEBOOK_PAGE_ACCESS_TOKEN with FACEBOOK_PAGE_ID_1 and FACEBOOK_PAGE_ID_2.
    """
    pages: list[tuple[str, str]] = []

    t_common = _env("FACEBOOK_PAGE_ACCESS_TOKEN")
    for i in (1, 2):
        pid = _env(f"FACEBOOK_PAGE_ID_{i}")
        tok = _env(f"FACEBOOK_PAGE_ACCESS_TOKEN_{i}") or t_common
        if pid and tok:
            pages.append((pid.strip(), tok.strip()))

    if not pages:
        logging.warning(
            "No Facebook pages configured. Set FACEBOOK_PAGE_ID_1/2 and "
            "FACEBOOK_PAGE_ACCESS_TOKEN (or per-page FACEBOOK_PAGE_ACCESS_TOKEN_*)."
        )
    return pages


def main_meta() -> None:
    ch_artists = _require_env("YOUTUBE_CHANNEL_ID_ARTISTS")
    ch_beats = _require_env("YOUTUBE_CHANNEL_ID_BEATS")

    ig_page_id = _env("INSTAGRAM_FACEBOOK_PAGE_ID")
    ig_token = _env("INSTAGRAM_PAGE_ACCESS_TOKEN") or _env("FACEBOOK_PAGE_ACCESS_TOKEN")

    history = _parse_history()
    videos_state: dict[str, Any] = history["videos"]

    all_items: list[VideoItem] = []
    all_items.extend(fetch_channel_feed(ch_artists))
    all_items.extend(fetch_channel_feed(ch_beats))

    # Unique by video_id, keep earliest published if duplicates
    by_id: dict[str, VideoItem] = {}
    for v in all_items:
        if v.video_id not in by_id or v.published < by_id[v.video_id].published:
            by_id[v.video_id] = v
    sorted_items = sorted(by_id.values(), key=lambda x: x.published)

    facebook_pages = load_page_tokens()

    ig_user_id: str | None = None
    if ig_page_id and ig_token:
        ig_user_id = get_instagram_business_account_id(ig_page_id.strip(), ig_token.strip())
        if not ig_user_id:
            logging.error(
                "Could not resolve Instagram Business Account for page %s. "
                "Check INSTAGRAM_FACEBOOK_PAGE_ID and token permissions.",
                ig_page_id,
            )
    elif ig_page_id and not ig_token:
        logging.warning("INSTAGRAM_FACEBOOK_PAGE_ID set but no INSTAGRAM_PAGE_ACCESS_TOKEN / FACEBOOK_PAGE_ACCESS_TOKEN.")

    processed_any = False

    for item in sorted_items:
        vid = item.video_id
        entry = videos_state.get(vid)
        if not isinstance(entry, dict):
            entry = {"destinations": {}}
            videos_state[vid] = entry
        dest = entry.setdefault("destinations", {})
        if not isinstance(dest, dict):
            dest = {}
            entry["destinations"] = dest

        message = (
            f"🎵 {item.title}\n\n"
            f"{item.url}\n\n"
            f"#YouTube #NewMusic"
        )

        caption = f"{item.title}\n\n{item.url}\n\n#YouTube #NewMusic"

        # Facebook pages
        for page_id, token in facebook_pages:
            key = _destination_key_facebook(page_id)
            if dest.get(key):
                continue
            ok = post_facebook_page_feed(page_id, token, message, item.url)
            if ok:
                dest[key] = datetime.now(timezone.utc).isoformat()
                processed_any = True
                logging.info("Facebook post OK page=%s video=%s", page_id, vid)
            else:
                logging.warning("Facebook post failed page=%s video=%s (will retry next run)", page_id, vid)

        # Instagram (one account linked to INSTAGRAM_FACEBOOK_PAGE_ID)
        if ig_user_id and ig_token:
            key = _destination_key_instagram()
            if not dest.get(key):
                ok = post_instagram_photo_with_caption(
                    ig_user_id,
                    ig_token.strip(),
                    item.thumbnail_url,
                    caption,
                )
                if ok:
                    dest[key] = datetime.now(timezone.utc).isoformat()
                    processed_any = True
                    logging.info("Instagram post OK video=%s", vid)
                else:
                    logging.warning("Instagram post failed video=%s (will retry next run)", vid)

        entry["title"] = item.title
        entry["last_seen"] = datetime.now(timezone.utc).isoformat()

    history["videos"] = videos_state
    history["meta"] = {
        "last_run": datetime.now(timezone.utc).isoformat(),
        "mode": "meta",
        "channels": {"artists": ch_artists, "beats": ch_beats},
    }

    try:
        _save_history(history)
    except OSError as e:
        logging.error("Failed to save history file: %s", e)
        sys.exit(1)

    if processed_any:
        logging.info("Run finished: new posts recorded.")
    else:
        logging.info("Run finished: no new successful posts (or nothing pending).")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    logging.Formatter.converter = time.gmtime

    mode = (_env("SOCIAL_OUTPUT_MODE") or "meta").strip().lower()
    if mode in ("webhook", "pipedream", "buffer"):
        main_webhook()
    elif mode == "meta":
        main_meta()
    else:
        logging.error("Unknown SOCIAL_OUTPUT_MODE=%r (use meta or webhook).", mode)
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logging.exception("Fatal error in social automator")
        sys.exit(1)
