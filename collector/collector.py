import os, asyncio
from datetime import timezone
import httpx
from telethon import TelegramClient
from telethon.sessions import StringSession


def normalize_username(value):
    """Return the one canonical form used for Telegram username comparisons."""
    return str(value or "").strip().lstrip("@").lower()


API_ID=int(os.environ["TG_API_ID"])
API_HASH=os.environ["TG_API_HASH"]
SESSION=os.environ["TG_SESSION"]
API_BASE_URL=os.environ["API_BASE_URL"].rstrip("/")
TOKEN=os.environ["COLLECTOR_TOKEN"]
BATCH_SIZE=int(os.environ.get("BATCH_SIZE","250"))
TARGET_CHANNEL=normalize_username(os.environ.get("TARGET_CHANNEL",""))

def auth(): return {"Authorization":f"Bearer {TOKEN}"}

def dedupe_channels(channels):
    """Keep one logical channel when legacy username and numeric-ID rows coexist.

    Prefer the row with the greatest synced message count/ID so an empty legacy
    row cannot trigger a second full import. This only affects the collector's
    work list; it never deletes database rows or messages.
    """
    chosen={}
    for channel in channels:
        key=normalize_username(channel.get("username")) or str(channel.get("telegram_id") or "")
        if not key: continue
        previous=chosen.get(key)
        score=lambda x:(int(x.get("message_count") or 0),int(x.get("last_message_id") or 0),int(x.get("id") or 0))
        if previous is None or score(channel)>score(previous): chosen[key]=channel
    return list(chosen.values())

def utc_iso(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value=value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00","Z")

async def main():
    client=TelegramClient(StringSession(SESSION),API_ID,API_HASH)
    await client.start()
    async with httpx.AsyncClient(timeout=90) as http:
        r=await http.get(f"{API_BASE_URL}/api/collector/channels",headers=auth());r.raise_for_status()
        channels=dedupe_channels(r.json().get("channels",[]))
        if TARGET_CHANNEL:
            channels=[c for c in channels if normalize_username(c.get("username"))==TARGET_CHANNEL or str(c.get("telegram_id"))==TARGET_CHANNEL]
        print(f"enabled channels: {len(channels)}" )
        for _ in range(5):
            try:
                pr=await http.post(f"{API_BASE_URL}/api/collector/purge",headers=auth())
                pr.raise_for_status()
                if not pr.json().get("processed"): break
                print(f"[PURGE] removed {pr.json().get('processed')} messages; remaining {pr.json().get('remaining')}" )
            except Exception as exc:
                print(f"[PURGE] skipped: {exc}")
                break
        for channel in channels:
            try: await collect_channel(client,http,channel)
            except Exception as exc: print(f"[ERROR] {channel.get('username') or channel.get('telegram_id')}: {exc}")
    await client.disconnect()

async def collect_channel(client,http,channel):
    ref=channel.get("username") or channel.get("telegram_id")
    entity=await client.get_entity(ref)
    username=normalize_username(getattr(entity,"username",None) or channel.get("username") or "")
    title=getattr(entity,"title",None) or channel.get("title") or username
    last_id=int(channel.get("last_message_id") or 0)
    run_id=None; imported=0
    sr=await http.post(f"{API_BASE_URL}/api/collector/run/start",json={"channel_id":str(entity.id)},headers=auth())
    sr.raise_for_status();run_id=sr.json().get("run_id")
    print(f"[SYNC] {title} (@{username}) after message {last_id}")
    try:
        batch=[]
        async for msg in client.iter_messages(entity,min_id=last_id,reverse=True):
            if not msg.message and not msg.media: continue
            media_type=type(msg.media).__name__ if msg.media else None
            media_name=None;media_size=None
            if getattr(msg,"file",None):
                media_name=getattr(msg.file,"name",None)
                media_size=getattr(msg.file,"size",None)
            batch.append({
                "channel_id":str(entity.id),"channel_username":username,"channel_title":title,
                "message_id":msg.id,"published_at":utc_iso(msg.date),
                "edited_at":utc_iso(msg.edit_date),"text":msg.message or "",
                "media_type":media_type,"media_name":media_name,"media_size":media_size,
                "message_url":f"https://t.me/{username}/{msg.id}" if username else None,"search_text":msg.message or ""
            })
            if len(batch)>=BATCH_SIZE:
                await push(http,batch);imported+=len(batch);batch.clear()
        if batch: await push(http,batch);imported+=len(batch)
        if run_id: await http.post(f"{API_BASE_URL}/api/collector/run/finish",json={"run_id":run_id,"status":"success","imported":imported},headers=auth())
        print(f"[DONE] {title}: imported {imported}")
    except Exception as exc:
        if run_id:
            await http.post(f"{API_BASE_URL}/api/collector/run/finish",json={"run_id":run_id,"status":"error","imported":imported,"error":str(exc)[:500]},headers=auth())
        raise

async def push(http,messages):
    r=await http.post(f"{API_BASE_URL}/api/ingest",json={"messages":messages},headers=auth());r.raise_for_status();print(r.json())

if __name__=="__main__": asyncio.run(main())
