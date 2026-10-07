import asyncio
import json
import os
import re
import time
from ipaddress import ip_address
from urllib.parse import urlsplit
from datetime import datetime, timedelta, timezone

import httpx
from telethon import TelegramClient
from telethon.sessions import StringSession

API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]
SESSION = os.environ["TG_SESSION"]
API_BASE_URL = os.environ["WORKER_API_BASE_URL"].strip().rstrip("/")
TOKEN = os.environ["WORKER_INGEST_TOKEN"]
if not API_BASE_URL:
    raise RuntimeError("WORKER_API_BASE_URL is not configured")
if not TOKEN:
    raise RuntimeError("WORKER_INGEST_TOKEN is not configured")
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "250"))
TARGET_CHANNEL = os.environ.get("TARGET_CHANNEL", "").strip().lstrip("@").lower()
COLLECT_MODE = os.environ.get("COLLECT_MODE", "incremental").strip().lower()
RECONCILE_DAYS = max(1, int(os.environ.get("RECONCILE_DAYS", "30")))
MAX_CHANNEL_RETRIES = max(0, int(os.environ.get("MAX_CHANNEL_RETRIES", "2")))
RETRY_DELAY_SECONDS = max(1, int(os.environ.get("RETRY_DELAY_SECONDS", "5")))
CHANNEL_WARN_SECONDS = max(1, int(os.environ.get("CHANNEL_WARN_SECONDS", "300")))
RUN_WARN_SECONDS = max(1, int(os.environ.get("RUN_WARN_SECONDS", "900")))
LINK_CHECK_ENABLED = os.environ.get("LINK_CHECK_ENABLED", "1").strip().lower() not in {"0", "false", "no"}
LINK_CHECK_LIMIT = max(1, int(os.environ.get("LINK_CHECK_LIMIT", "80")))
LINK_CHECK_MAX_AGE_HOURS = max(1, int(os.environ.get("LINK_CHECK_MAX_AGE_HOURS", "24")))
LINK_CHECK_TIMEOUT_SECONDS = max(3, int(os.environ.get("LINK_CHECK_TIMEOUT_SECONDS", "12")))
LINK_CHECK_CONCURRENCY = max(1, int(os.environ.get("LINK_CHECK_CONCURRENCY", "8")))
SUMMARY_PATH = os.environ.get("COLLECTOR_SUMMARY_PATH", "collector-summary.json")

if COLLECT_MODE not in {"incremental", "recent_reconcile", "full_reconcile"}:
    raise RuntimeError(f"unsupported COLLECT_MODE: {COLLECT_MODE}")


def auth():
    return {"Authorization": f"Bearer {TOKEN}"}


def normalize_username(value):
    return str(value or "").strip().lstrip("@").lower()


def normalize_channel_id(value):
    value = str(value or "").strip()
    return value[4:] if value.startswith("-100") else value


def dedupe_channels(channels):
    """Keep one logical channel when legacy username and numeric-ID rows coexist."""
    chosen = {}
    for channel in channels:
        key = normalize_username(channel.get("username")) or str(channel.get("telegram_id") or "")
        if not key:
            continue
        previous = chosen.get(key)
        score = lambda x: (int(x.get("message_count") or 0), int(x.get("last_message_id") or 0), int(x.get("id") or 0))
        if previous is None or score(channel) > score(previous):
            chosen[key] = channel
    return list(chosen.values())


def utc_iso(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def write_summary(summary):
    summary["finished_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    summary["duration_seconds"] = round(time.monotonic() - summary["_started_monotonic"], 2)
    summary.pop("_started_monotonic", None)
    with open(SUMMARY_PATH, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False))


async def main():
    summary = {
        "started_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "mode": COLLECT_MODE,
        "reconcile_days": RECONCILE_DAYS if COLLECT_MODE == "recent_reconcile" else None,
        "duration_seconds": 0,
        "channel_count": 0,
        "succeeded_channels": 0,
        "failed_channels": 0,
        "total_imported": 0,
        "total_scanned": 0,
        "total_deleted": 0,
        "links_checked": 0,
        "links_healthy": 0,
        "links_unhealthy": 0,
        "link_check_errors": 0,
        "channels": [],
        "_started_monotonic": time.monotonic(),
    }
    failed = 0
    client = None
    try:
        client = TelegramClient(StringSession(SESSION), API_ID, API_HASH)
        await client.start()
        async with httpx.AsyncClient(timeout=90) as http:
            response = await http.get(f"{API_BASE_URL}/api/collector/channels", headers=auth())
            response.raise_for_status()
            channels = dedupe_channels(response.json().get("channels", []))
            if TARGET_CHANNEL:
                channels = [channel for channel in channels if normalize_username(channel.get("username")) == TARGET_CHANNEL or normalize_channel_id(channel.get("telegram_id")) == normalize_channel_id(TARGET_CHANNEL)]
            summary["channel_count"] = len(channels)
            print(f"mode={COLLECT_MODE} enabled channels: {len(channels)}")
            for _ in range(5):
                try:
                    purge = await http.post(f"{API_BASE_URL}/api/collector/purge", headers=auth())
                    purge.raise_for_status()
                    if not purge.json().get("processed"):
                        break
                    print(f"[PURGE] removed {purge.json().get('processed')} messages; remaining {purge.json().get('remaining')}")
                except Exception as exc:
                    print(f"[PURGE] skipped: {exc}")
                    break
            for channel in channels:
                result = await collect_channel_with_retry(client, http, channel)
                summary["channels"].append(result)
                summary["total_imported"] += result.get("imported", 0)
                summary["total_scanned"] += result.get("scanned", 0)
                summary["total_deleted"] += result.get("deleted", 0)
                if result["status"] == "success":
                    summary["succeeded_channels"] += 1
                else:
                    failed += 1
                    summary["failed_channels"] += 1
            if LINK_CHECK_ENABLED:
                try:
                    link_summary = await check_links(http)
                    summary.update(link_summary)
                except Exception as exc:
                    summary["link_check_errors"] = 1
                    print(f"::warning title=Link health check::{str(exc)[:500]}")
    finally:
        if client is not None:
            await client.disconnect()
        write_summary(summary)
    if failed:
        raise RuntimeError(f"{failed} channel(s) failed after retries")
    if summary["duration_seconds"] >= RUN_WARN_SECONDS:
        print(f"::warning title=Slow collector::run took {summary['duration_seconds']}s")


URL_RE = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)

def safe_external_url(raw):
    value = str(raw or "").rstrip("),.;!?]}>")
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return None
        if parsed.port not in {None, 80, 443}:
            return None
        host = parsed.hostname.lower().rstrip(".")
        if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".internal")):
            return None
        try:
            ip = ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
                return None
        except ValueError:
            pass
        return value[:2000]
    except ValueError:
        return None

async def check_one_link(http, url, semaphore):
    async with semaphore:
        started = time.monotonic()
        try:
            response = await http.head(url, follow_redirects=False)
            if response.status_code in {405, 501}:
                response = await http.get(url, headers={"Range": "bytes=0-0"}, follow_redirects=False)
            code = int(response.status_code)
            return {
                "url": url,
                "status": "healthy" if 200 <= code < 400 else "unhealthy",
                "status_code": code,
                "final_url": str(response.headers.get("location") or url)[:2000],
                "response_ms": round((time.monotonic() - started) * 1000),
                "error": None,
            }
        except Exception as exc:
            return {
                "url": url,
                "status": "error",
                "status_code": None,
                "final_url": None,
                "response_ms": round((time.monotonic() - started) * 1000),
                "error": str(exc)[:500],
            }

async def check_links(http):
    response = await http.get(
        f"{API_BASE_URL}/api/collector/link-health?limit={LINK_CHECK_LIMIT}&max_age_hours={LINK_CHECK_MAX_AGE_HOURS}",
        headers=auth(),
    )
    response.raise_for_status()
    candidates = []
    for row in response.json().get("links", []):
        url = safe_external_url(row.get("url"))
        if url and not url.lower().startswith(("https://t.me/", "http://t.me/", "https://telegram.me/", "http://telegram.me/")):
            candidates.append(url)
    candidates = list(dict.fromkeys(candidates))[:LINK_CHECK_LIMIT]
    if not candidates:
        return {"links_checked": 0, "links_healthy": 0, "links_unhealthy": 0, "link_check_errors": 0}
    timeout = httpx.Timeout(LINK_CHECK_TIMEOUT_SECONDS)
    semaphore = asyncio.Semaphore(LINK_CHECK_CONCURRENCY)
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": "telegram-history-search-link-check/1.0"}) as checker:
        results = await asyncio.gather(*(check_one_link(checker, url, semaphore) for url in candidates))
    saved = await http.post(f"{API_BASE_URL}/api/collector/link-health", json={"results": results}, headers=auth())
    saved.raise_for_status()
    healthy = sum(1 for x in results if x["status"] == "healthy")
    unhealthy = sum(1 for x in results if x["status"] == "unhealthy")
    errors = sum(1 for x in results if x["status"] == "error")
    print(f"[LINKS] checked={len(results)} healthy={healthy} unhealthy={unhealthy} errors={errors}")
    return {"links_checked": len(results), "links_healthy": healthy, "links_unhealthy": unhealthy, "link_check_errors": errors}


async def collect_channel_with_retry(client, http, channel):
    label = channel.get("username") or channel.get("telegram_id") or "unknown"
    started = time.monotonic()
    errors = []
    for attempt in range(1, MAX_CHANNEL_RETRIES + 2):
        try:
            result = await collect_channel(client, http, channel)
            result.update({"channel": label, "status": "success", "attempts": attempt, "duration_seconds": round(time.monotonic() - started, 2), "error": None})
            if result["duration_seconds"] >= CHANNEL_WARN_SECONDS:
                print(f"::warning title=Slow channel::{label} took {result['duration_seconds']}s")
            print(f"[CHANNEL] {label} duration={result['duration_seconds']}s attempts={attempt}")
            return result
        except Exception as exc:
            errors.append(str(exc)[:500])
            if attempt <= MAX_CHANNEL_RETRIES:
                delay = RETRY_DELAY_SECONDS * attempt
                print(f"::warning title=Channel retry::{label} failed on attempt {attempt}; retrying in {delay}s: {errors[-1]}")
                await asyncio.sleep(delay)
    duration = round(time.monotonic() - started, 2)
    print(f"::warning title=Channel failed::{label} failed after {len(errors)} attempts")
    return {"channel": label, "status": "failed", "attempts": len(errors), "duration_seconds": duration, "imported": 0, "scanned": 0, "deleted": 0, "error": errors[-1] if errors else "unknown error"}


async def collect_channel(client, http, channel):
    ref = channel.get("username") or channel.get("telegram_id")
    entity = await client.get_entity(ref)
    username = normalize_username(getattr(entity, "username", None) or channel.get("username") or "")
    title = getattr(entity, "title", None) or channel.get("title") or username
    last_id = int(channel.get("last_message_id") or 0)
    reconcile_id = None
    run_id = None
    imported = 0
    scanned = 0
    deleted = 0
    cutoff = None
    if COLLECT_MODE == "recent_reconcile":
        cutoff = datetime.now(timezone.utc) - timedelta(days=RECONCILE_DAYS)
    start_response = await http.post(f"{API_BASE_URL}/api/collector/run/start", json={"channel_id": str(entity.id), "mode": COLLECT_MODE}, headers=auth())
    start_response.raise_for_status()
    run_id = start_response.json().get("run_id")
    if COLLECT_MODE != "incremental":
        reconcile_response = await http.post(f"{API_BASE_URL}/api/collector/reconcile/start", json={"channel_id": str(entity.id), "mode": COLLECT_MODE, "cutoff_at": utc_iso(cutoff)}, headers=auth())
        reconcile_response.raise_for_status()
        reconcile_id = reconcile_response.json().get("reconcile_id")
    print(f"[SYNC] {title} (@{username}) mode={COLLECT_MODE} after message {last_id}")
    try:
        batch = []
        seen_ids = []
        iterator = client.iter_messages(entity, min_id=last_id, reverse=True) if COLLECT_MODE == "incremental" else client.iter_messages(entity, reverse=False)
        async for msg in iterator:
            if cutoff is not None and msg.date and msg.date.replace(tzinfo=timezone.utc) < cutoff:
                break
            if not msg.message and not msg.media:
                continue
            scanned += 1
            seen_ids.append(int(msg.id))
            media_type = type(msg.media).__name__ if msg.media else None
            media_name = None
            media_size = None
            if getattr(msg, "file", None):
                media_name = getattr(msg.file, "name", None)
                media_size = getattr(msg.file, "size", None)
            batch.append({"channel_id": str(entity.id), "channel_username": username, "channel_title": title, "message_id": msg.id, "published_at": utc_iso(msg.date), "edited_at": utc_iso(msg.edit_date), "text": msg.message or "", "media_type": media_type, "media_name": media_name, "media_size": media_size, "message_url": f"https://t.me/{username}/{msg.id}" if username else None, "search_text": msg.message or ""})
            if len(batch) >= BATCH_SIZE:
                await push(http, batch, reconcile_id, seen_ids)
                imported += len(batch)
                batch.clear()
                seen_ids.clear()
        if batch:
            await push(http, batch, reconcile_id, seen_ids)
            imported += len(batch)
        if reconcile_id:
            finish = await http.post(f"{API_BASE_URL}/api/collector/reconcile/finish", json={"reconcile_id": reconcile_id, "scanned": scanned}, headers=auth())
            finish.raise_for_status()
            data = finish.json()
            deleted = int(data.get("deleted", 0))
            if not data.get("completed", False):
                raise RuntimeError(data.get("message", "reconcile did not complete safely"))
        if run_id:
            await http.post(f"{API_BASE_URL}/api/collector/run/finish", json={"run_id": run_id, "status": "success", "imported": imported}, headers=auth())
        print(f"[DONE] {title}: imported={imported} scanned={scanned} deleted={deleted}")
        return {"imported": imported, "scanned": scanned, "deleted": deleted}
    except Exception as exc:
        if reconcile_id:
            await http.post(f"{API_BASE_URL}/api/collector/reconcile/finish", json={"reconcile_id": reconcile_id, "scanned": scanned, "status": "error", "error": str(exc)[:500]}, headers=auth())
        if run_id:
            await http.post(f"{API_BASE_URL}/api/collector/run/finish", json={"run_id": run_id, "status": "error", "imported": imported, "error": str(exc)[:500]}, headers=auth())
        raise


async def push(http, messages, reconcile_id=None, seen_ids=None):
    response = await http.post(f"{API_BASE_URL}/api/ingest", json={"messages": messages}, headers=auth())
    response.raise_for_status()
    if reconcile_id and seen_ids:
        seen_response = await http.post(f"{API_BASE_URL}/api/collector/reconcile/seen", json={"reconcile_id": reconcile_id, "message_ids": seen_ids}, headers=auth())
        seen_response.raise_for_status()
    print(response.json())


if __name__ == "__main__":
    asyncio.run(main())
