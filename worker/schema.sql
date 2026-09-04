CREATE TABLE IF NOT EXISTS channels (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  telegram_id TEXT NOT NULL UNIQUE,
  username TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1,
  last_message_id INTEGER NOT NULL DEFAULT 0,
  message_count INTEGER NOT NULL DEFAULT 0,
  last_synced_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_channels_enabled ON channels(enabled);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  channel_id TEXT NOT NULL,
  channel_username TEXT NOT NULL DEFAULT '',
  channel_title TEXT NOT NULL DEFAULT '',
  message_id INTEGER NOT NULL,
  published_at TEXT,
  edited_at TEXT,
  text TEXT NOT NULL DEFAULT '',
  media_type TEXT,
  media_name TEXT,
  media_size INTEGER,
  message_url TEXT,
  search_text TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(channel_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_messages_channel_date ON messages(channel_id, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_channel_id ON messages(channel_id, message_id DESC);
CREATE INDEX IF NOT EXISTS idx_messages_published ON messages(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_username ON messages(channel_username);
CREATE INDEX IF NOT EXISTS idx_messages_media ON messages(media_type);

CREATE TABLE IF NOT EXISTS collection_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  channel_id TEXT,
  status TEXT NOT NULL,
  imported INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_collection_runs_started ON collection_runs(started_at DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
  text,
  channel_username,
  channel_title,
  content='messages',
  content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
  INSERT INTO messages_fts(rowid,text,channel_username,channel_title)
  VALUES(new.id,new.text,new.channel_username,new.channel_title);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts,rowid,text,channel_username,channel_title)
  VALUES('delete',old.id,old.text,old.channel_username,old.channel_title);
END;
CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts,rowid,text,channel_username,channel_title)
  VALUES('delete',old.id,old.text,old.channel_username,old.channel_title);
  INSERT INTO messages_fts(rowid,text,channel_username,channel_title)
  VALUES(new.id,new.text,new.channel_username,new.channel_title);
END;

CREATE TABLE IF NOT EXISTS bot_sessions (
  chat_id TEXT PRIMARY KEY,
  q TEXT NOT NULL DEFAULT '',
  channel TEXT NOT NULL DEFAULT '',
  from_date TEXT NOT NULL DEFAULT '',
  to_date TEXT NOT NULL DEFAULT '',
  media TEXT NOT NULL DEFAULT '',
  offset INTEGER NOT NULL DEFAULT 0,
  mode TEXT NOT NULL DEFAULT 'search',
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_bot_sessions_updated ON bot_sessions(updated_at);


CREATE TABLE IF NOT EXISTS auth_codes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  label TEXT NOT NULL DEFAULT '',
  code_hash TEXT NOT NULL UNIQUE,
  max_uses INTEGER,
  used_count INTEGER NOT NULL DEFAULT 0,
  expires_at TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_used_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_auth_codes_enabled ON auth_codes(enabled);
CREATE INDEX IF NOT EXISTS idx_auth_codes_expires ON auth_codes(expires_at);

CREATE TABLE IF NOT EXISTS auth_code_uses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code_id INTEGER NOT NULL,
  action TEXT NOT NULL DEFAULT 'add_channel',
  ip TEXT,
  user_agent TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(code_id) REFERENCES auth_codes(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_auth_code_uses_code ON auth_code_uses(code_id, created_at DESC);
