def build_seed_probe_rows_script(old_id: str, new_id: str) -> str:
    return f"""
import json
import sqlite3
import time

db = "/app/backend/data/webui.db"
conn = sqlite3.connect(db, timeout=5)
cur = conn.cursor()

old_id = {old_id!r}
new_id = {new_id!r}
now = int(time.time())
old_ts = now - (3 * 86400)
new_ts = now

# If frontend migrations are unavailable in the test environment, bootstrap
# the minimal tables the purger logic requires.
cur.execute(
    \"\"\"
    CREATE TABLE IF NOT EXISTS chat (
        id TEXT PRIMARY KEY,
        user_id TEXT,
        title TEXT,
        chat TEXT,
        created_at INTEGER,
        updated_at INTEGER,
        share_id TEXT,
        archived INTEGER,
        pinned INTEGER,
        meta TEXT,
        folder_id TEXT
    )
    \"\"\"
)
cur.execute(
    \"\"\"
    CREATE TABLE IF NOT EXISTS chatidtag (
        id TEXT PRIMARY KEY,
        tag_name TEXT,
        chat_id TEXT,
        user_id TEXT,
        timestamp INTEGER
    )
    \"\"\"
)

chat_cols = {{row[1] for row in cur.execute("PRAGMA table_info(chat)")}}
if "id" not in chat_cols or "updated_at" not in chat_cols:
    raise RuntimeError(f"Unexpected chat schema. Columns: {{sorted(chat_cols)}}")

chat_payload = json.dumps({{"history": {{"messages": {{}}, "currentId": None}}}})
tables = {{row[0] for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}}
has_chatidtag = "chatidtag" in tables

for cid in (old_id, new_id):
    if has_chatidtag:
        cur.execute("DELETE FROM chatidtag WHERE chat_id = ?", (cid,))
    cur.execute("DELETE FROM chat WHERE id = ?", (cid,))

insert_order = [
    "id",
    "user_id",
    "title",
    "chat",
    "created_at",
    "updated_at",
    "archived",
    "pinned",
    "meta",
    "folder_id",
]
insert_cols = [c for c in insert_order if c in chat_cols]

def insert_chat(cid: str, ts: int) -> None:
    values = {{
        "id": cid,
        "user_id": "retention-test-user",
        "title": f"retention-test-{{cid}}",
        "chat": chat_payload,
        "created_at": ts,
        "updated_at": ts,
        "archived": 0,
        "pinned": 0,
        "meta": "{{}}",
        "folder_id": None,
    }}
    params = [values[c] for c in insert_cols]
    placeholders = ",".join(["?"] * len(insert_cols))
    cur.execute(
        f"INSERT INTO chat ({{','.join(insert_cols)}}) VALUES ({{placeholders}})",
        params,
    )

insert_chat(old_id, old_ts)
insert_chat(new_id, new_ts)

if has_chatidtag:
    tag_cols = {{row[1] for row in cur.execute("PRAGMA table_info(chatidtag)")}}
else:
    tag_cols = set()

if has_chatidtag and {{"id", "chat_id"}}.issubset(tag_cols):
    tag_order = ["id", "tag_name", "chat_id", "user_id", "timestamp"]
    tag_insert_cols = [c for c in tag_order if c in tag_cols]
    placeholders = ",".join(["?"] * len(tag_insert_cols))
    values = {{
        "id": f"retention-tag-{{old_id}}",
        "tag_name": "retention-test",
        "chat_id": old_id,
        "user_id": "retention-test-user",
        "timestamp": old_ts,
    }}
    params = [values[c] for c in tag_insert_cols]
    cur.execute(
        f"INSERT INTO chatidtag ({{','.join(tag_insert_cols)}}) VALUES ({{placeholders}})",
        params,
    )

conn.commit()
conn.close()
"""


def build_probe_counts_script(old_id: str, new_id: str) -> str:
    return f"""
import json
import sqlite3

db = "/app/backend/data/webui.db"
conn = sqlite3.connect(db, timeout=5)
cur = conn.cursor()

result = {{
    "old_chat": cur.execute("SELECT COUNT(*) FROM chat WHERE id = ?", ({old_id!r},)).fetchone()[0],
    "new_chat": cur.execute("SELECT COUNT(*) FROM chat WHERE id = ?", ({new_id!r},)).fetchone()[0],
}}

tables = {{row[0] for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}}
if "chatidtag" in tables:
    result["old_tag"] = cur.execute(
        "SELECT COUNT(*) FROM chatidtag WHERE chat_id = ?",
        ({old_id!r},),
    ).fetchone()[0]
else:
    result["old_tag"] = 0

conn.close()
print(json.dumps(result))
"""
