import asyncio
import json
import os
import time
from datetime import datetime, timezone

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
MAX_CHANNEL_RETRIES = max(0, int(os.environ.get("MAX_CHANNEL_RETRIES", "2")))
RETRY_DELAY_SECONDS = max(1, int(os.environ.get("RETRY_DELAY_SECONDS", "5")))
CHANNEL_WARN_SECONDS = max(1, int(os.environ.get("CHANNEL_WARN_SECONDS", "300")))
RUN_WARN_SECONDS = max(1, int(os.environ.get("RUN_WARN_SECONDS", "900")))
SUMMARY_PATH = os.environ.get("COLLECTOR_SUMMARY_PATH", "collector-summary.json")


def auth():
    return {"Authorization": f"Bearer {TOKEN}"}


def normalize_username(value):
    return str(value or "").strip().lstrip("@").lower()


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
        "duration_seconds": 0,
        "channel_count": 0,
        "succeeded_channels": 0,
        "failed_channels": 0,
        "total_imported": 0,
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
                channels = [channel for channel in channels if normalize_username(channel.get("username")) == TARGET_CHANNEL or str(channel.get("telegram_id")) == TARGET_CHANNEL]
            summary["channel_count"] = len(channels)
            print(f"enabled channels: {len(channels)}")
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
                summary["total_imported"] += result["imported"]
                if result["status"] == "success":
                    summary["succeeded_channels"] += 1
                else:
                    failed += 1
                    summary["failed_channels"] += 1
    finally:
        if client is not None:
            await client.disconnect()
        write_summary(summary)
    if failed:
        raise RuntimeError(f"{failed} channel(s) failed after retries")
    if summary["duration_seconds"] >= RUN_WARN_SECONDS:
        print(f"::warning title=Slow collector::run took {summary['duration_seconds']}s")


async def collect_channel_with_retry(client, http, channel):
    label = channel.get("username") or channel.get("telegram_id") or "unknown"
    started = time.monotonic()
    errors = []
    for attempt in range(1, MAX_CHANNEL_RETRIES + 2):
        try:
            imported = await collect_channel(client, http, channel)
            duration = round(time.monotonic() - started, 2)
            result = {"channel": label, "status": "success", "attempts": attempt, "duration_seconds": duration, "imported": imported, "error": None}
            if duration >= CHANNEL_WARN_SECONDS:
                print(f"::warning title=Slow channel::{label} took {duration}s")
            print(f"[CHANNEL] {label} duration={duration}s attempts={attempt}")
            return result
        except Exception as exc:
            errors.append(str(exc)[:500])
            if attempt <= MAX_CHANNEL_RETRIES:
                delay = RETRY_DELAY_SECONDS * attempt
                print(f"::warning title=Channel retry::{label} failed on attempt {attempt}; retrying in {delay}s: {errors[-1]}")
                await asyncio.sleep(delay)
    duration = round(time.monotonic() - started, 2)
    print(f"::warning title=Channel failed::{label} failed after {len(errors)} attempts")
    print(f"[CHANNEL] {label} duration={duration}s attempts={len(errors)} status=failed")
    return {"channel": label, "status": "failed", "attempts": len(errors), "duration_seconds": duration, "imported": 0, "error": errors[-1] if errors else "unknown error"}


async def collect_channel(client, http, channel):
    ref = channel.get("username") or channel.get("telegram_id")
    entity = await client.get_entity(ref)
    username = normalize_username(getattr(entity, "username", None) or channel.get("username") or "")
    title = getattr(entity, "title", None) or channel.get("title") or username
    last_id = int(channel.get("last_message_id") or 0)
    run_id = None
    imported = 0
    start_response = await http.post(f"{API_BASE_URL}/api/collector/run/start", json={"channel_id": str(entity.id)}, headers=auth())
    start_response.raise_for_status()
    run_id = start_response.json().get("run_id")
    print(f"[SYNC] {title} (@{username}) after message {last_id}")
    try:
        batch = []
        async for msg in client.iter_messages(entity, min_id=last_id, reverse=True):
            if not msg.message and not msg.media:
                continue
            media_type = type(msg.media).__name__ if msg.media else None
            media_name = None
            media_size = None
            if getattr(msg, "file", None):
                media_name = getattr(msg.file, "name", None)
                media_size = getattr(msg.file, "size", None)
            batch.append({"channel_id": str(entity.id), "channel_username": username, "channel_title": title, "message_id": msg.id, "published_at": utc_iso(msg.date), "edited_at": utc_iso(msg.edit_date), "text": msg.message or "", "media_type": media_type, "media_name": media_name, "media_size": media_size, "message_url": f"https://t.me/{username}/{msg.id}" if username else None, "search_text": msg.message or ""})
            if len(batch) >= BATCH_SIZE:
                await push(http, batch)
                imported += len(batch)
                batch.clear()
        if batch:
            await push(http, batch)
            imported += len(batch)
        if run_id:
            await http.post(f"{API_BASE_URL}/api/collector/run/finish", json={"run_id": run_id, "status": "success", "imported": imported}, headers=auth())
        print(f"[DONE] {title}: imported {imported}")
        return imported
    except Exception as exc:
        if run_id:
            await http.post(f"{API_BASE_URL}/api/collector/run/finish", json={"run_id": run_id, "status": "error", "imported": imported, "error": str(exc)[:500]}, headers=auth())
        raise


async def push(http, messages):
    response = await http.post(f"{API_BASE_URL}/api/ingest", json={"messages": messages}, headers=auth())
    response.raise_for_status()
    print(response.json())


if __name__ == "__main__":
    asyncio.run(main())
