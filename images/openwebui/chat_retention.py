#!/usr/bin/env python3
import datetime
import os
import sqlite3
import sys
import time


def log(message: str) -> None:
    print(f"[chat-retention] {message}", flush=True)


def main() -> int:
    retention_days = int(os.getenv("CHAT_HISTORY_RETENTION_DAYS", "180"))

    db_path = os.getenv("CHAT_HISTORY_DB_PATH", "/app/backend/data/webui.db")
    if not os.path.exists(db_path):
        log(f"DB not found at {db_path}; skipping purge.")
        return 0

    cutoff_epoch = int(time.time() - (retention_days * 86400))
    cutoff_iso = datetime.datetime.utcfromtimestamp(cutoff_epoch).strftime("%Y-%m-%d %H:%M:%S")

    try:
        conn = sqlite3.connect(db_path, timeout=5)
    except Exception as exc:
        log(f"Failed to open DB at {db_path}: {exc}")
        return 0

    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA busy_timeout = 5000;")
        cursor.execute("BEGIN;")

        params = {"cutoff_epoch": cutoff_epoch, "cutoff_iso": cutoff_iso}

        cursor.execute(
            """
            DELETE FROM chatidtag
            WHERE EXISTS (
                SELECT 1 FROM chat
                WHERE chat.id = chatidtag.chat_id AND (
                    (typeof(chat.updated_at) IN ('integer','real') AND (CASE WHEN chat.updated_at > 1000000000000 THEN chat.updated_at/1000 ELSE chat.updated_at END) < :cutoff_epoch)
                    OR (typeof(chat.updated_at) = 'text' AND chat.updated_at GLOB '[0-9]*' AND (CASE WHEN CAST(chat.updated_at AS INTEGER) > 1000000000000 THEN CAST(chat.updated_at AS INTEGER)/1000 ELSE CAST(chat.updated_at AS INTEGER) END) < :cutoff_epoch)
                    OR (typeof(chat.updated_at) = 'text' AND chat.updated_at NOT GLOB '[0-9]*' AND datetime(chat.updated_at) < :cutoff_iso)
                )
            );
            """,
            params,
        )
        cursor.execute("SELECT changes();")
        deleted_tags = cursor.fetchone()[0]

        cursor.execute(
            """
            DELETE FROM chat
            WHERE (
                (typeof(updated_at) IN ('integer','real') AND (CASE WHEN updated_at > 1000000000000 THEN updated_at/1000 ELSE updated_at END) < :cutoff_epoch)
                OR (typeof(updated_at) = 'text' AND updated_at GLOB '[0-9]*' AND (CASE WHEN CAST(updated_at AS INTEGER) > 1000000000000 THEN CAST(updated_at AS INTEGER)/1000 ELSE CAST(updated_at AS INTEGER) END) < :cutoff_epoch)
                OR (typeof(updated_at) = 'text' AND updated_at NOT GLOB '[0-9]*' AND datetime(updated_at) < :cutoff_iso)
            );
            """,
            params,
        )
        cursor.execute("SELECT changes();")
        deleted_chats = cursor.fetchone()[0]

        conn.commit()
        log(
            "Deleted "
            f"{deleted_tags} tag mappings and {deleted_chats} chats older than "
            f"{retention_days} days."
        )
    except sqlite3.OperationalError as exc:
        conn.rollback()
        log(f"Retention purge skipped (db error): {exc}")
    except Exception as exc:
        conn.rollback()
        log(f"Retention purge failed: {exc}")
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
