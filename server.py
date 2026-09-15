import os
import sys
import json
import asyncio
import sqlite3
from pathlib import Path
from datetime import datetime
import websockets

# Railway Cloud Storage & Network Settings
DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "prestigegaruda_auction.db"

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", 8765))

CONNECTED_CLIENTS = set()

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS members (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        character_class TEXT,
        role TEXT,
        notes TEXT,
        gl_queue_position INTEGER NOT NULL DEFAULT 0,
        eo_queue_position INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS auction_cycles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        auction_type TEXT NOT NULL,
        puppet_count INTEGER NOT NULL,
        lnd_total INTEGER NOT NULL,
        tns_total INTEGER NOT NULL,
        participant_count INTEGER NOT NULL,
        lnd_each INTEGER NOT NULL,
        lnd_leftover INTEGER NOT NULL,
        tns_each INTEGER NOT NULL,
        tns_leftover INTEGER NOT NULL,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS cycle_members (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cycle_id INTEGER NOT NULL,
        member_name TEXT NOT NULL,
        queue_position_before INTEGER NOT NULL,
        participated INTEGER NOT NULL,
        lnd_awarded INTEGER NOT NULL DEFAULT 0,
        tns_awarded INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY(cycle_id) REFERENCES auction_cycles(id)
    );

    CREATE TABLE IF NOT EXISTS league_teams (
        team_number INTEGER PRIMARY KEY,
        battlefield_type TEXT NOT NULL,
        slot_main_dps INTEGER,
        slot_sub_dps INTEGER,
        slot_utility INTEGER,
        slot_bard INTEGER,
        slot_fs INTEGER,
        FOREIGN KEY(slot_main_dps) REFERENCES members(id),
        FOREIGN KEY(slot_sub_dps) REFERENCES members(id),
        FOREIGN KEY(slot_utility) REFERENCES members(id),
        FOREIGN KEY(slot_bard) REFERENCES members(id),
        FOREIGN KEY(slot_fs) REFERENCES members(id)
    );
    """)
    count = conn.execute("SELECT COUNT(*) AS c FROM league_teams").fetchone()["c"]
    if count == 0:
        for i in range(1, 9):
            conn.execute(
                "INSERT INTO league_teams (team_number, battlefield_type) VALUES (?, 'Main')", (i,)
            )
        for i in range(9, 17):
            conn.execute(
                "INSERT INTO league_teams (team_number, battlefield_type) VALUES (?, 'Sub')", (i,)
            )
    conn.commit()
    conn.close()

def query_db(query, params=(), fetchone=False, fetchall=False, commit=False):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(query, params)
    res = None
    if commit:
        conn.commit()
        res = cur.lastrowid
    elif fetchone:
        row = cur.fetchone()
        res = dict(row) if row else None
    elif fetchall:
        res = [dict(row) for row in cur.fetchall()]
    conn.close()
    return res

def get_state():
    members = query_db("SELECT * FROM members ORDER BY gl_queue_position, id", fetchall=True)
    teams = query_db("SELECT * FROM league_teams ORDER BY team_number ASC", fetchall=True)
    cycles = query_db("SELECT * FROM auction_cycles ORDER BY id DESC", fetchall=True)
    return {
        "event": "STATE_UPDATE",
        "members": members,
        "teams": teams,
        "cycles": cycles
    }

async def broadcast(message):
    if CONNECTED_CLIENTS:
        payload = json.dumps(message)
        await asyncio.gather(*[client.send(payload) for client in CONNECTED_CLIENTS])

async def handle_action(data):
    action = data.get("action")
    payload = data.get("payload", {})

    if action == "ADD_MEMBER":
        count = query_db("SELECT COUNT(*) AS c FROM members", fetchone=True)["c"]
        if count >= 80:
            return
        gl_pos = (query_db("SELECT COALESCE(MAX(gl_queue_position), 0) AS p FROM members", fetchone=True)["p"]) + 1
        eo_pos = (query_db("SELECT COALESCE(MAX(eo_queue_position), 0) AS p FROM members", fetchone=True)["p"]) + 1

        query_db(
            """INSERT INTO members (name, character_class, role, notes, gl_queue_position, eo_queue_position, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (payload["name"], payload["character_class"], payload["role"], payload["notes"], gl_pos, eo_pos,
             datetime.now().strftime("%Y-%m-%d %H:%M")),
            commit=True
        )

    elif action == "EDIT_MEMBER":
        query_db(
            "UPDATE members SET name = ?, character_class = ?, role = ?, notes = ? WHERE id = ?",
            (payload["name"], payload["character_class"], payload["role"], payload["notes"], payload["id"]),
            commit=True
        )

    elif action == "DELETE_MEMBER":
        m_id = payload["id"]
        query_db("UPDATE league_teams SET slot_main_dps = NULL WHERE slot_main_dps = ?", (m_id,), commit=True)
        query_db("UPDATE league_teams SET slot_sub_dps = NULL WHERE slot_sub_dps = ?", (m_id,), commit=True)
        query_db("UPDATE league_teams SET slot_utility = NULL WHERE slot_utility = ?", (m_id,), commit=True)
        query_db("UPDATE league_teams SET slot_bard = NULL WHERE slot_bard = ?", (m_id,), commit=True)
        query_db("UPDATE league_teams SET slot_fs = NULL WHERE slot_fs = ?", (m_id,), commit=True)
        query_db("DELETE FROM members WHERE id = ?", (m_id,), commit=True)

        gl_rem = query_db("SELECT id FROM members ORDER BY gl_queue_position, id", fetchall=True)
        for pos, row in enumerate(gl_rem, start=1):
            query_db("UPDATE members SET gl_queue_position = ? WHERE id = ?", (pos, row["id"]), commit=True)

        eo_rem = query_db("SELECT id FROM members ORDER BY eo_queue_position, id", fetchall=True)
        for pos, row in enumerate(eo_rem, start=1):
            query_db("UPDATE members SET eo_queue_position = ? WHERE id = ?", (pos, row["id"]), commit=True)

    elif action == "RESET_ALL":
        query_db("DELETE FROM members", commit=True)
        query_db("UPDATE league_teams SET slot_main_dps=NULL, slot_sub_dps=NULL, slot_utility=NULL, slot_bard=NULL, slot_fs=NULL", commit=True)

    elif action == "UPDATE_TEAM_SLOT":
        query_db(
            """UPDATE league_teams 
            SET slot_main_dps = ?, slot_sub_dps = ?, slot_utility = ?, slot_bard = ?, slot_fs = ?
            WHERE team_number = ?""",
            (payload["m_dps"], payload["s_dps"], payload["util"], payload["bard"], payload["fs"], payload["team_number"]),
            commit=True
        )

    elif action == "FINALIZE_AUCTION":
        auction_type = payload["auction_type"]
        q_col = "gl_queue_position" if auction_type == "GL" else "eo_queue_position"
        cycle_id = query_db(
            """INSERT INTO auction_cycles
            (auction_type, puppet_count, lnd_total, tns_total, participant_count,
             lnd_each, lnd_leftover, tns_each, tns_leftover, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (auction_type, payload["puppet"], payload["lnd_total"], payload["tns_total"],
             payload["participant_count"], payload["lnd_each"], payload["lnd_leftover"],
             payload["tns_each"], payload["tns_leftover"], datetime.now().strftime("%Y-%m-%d %H:%M")),
            commit=True
        )

        for cm in payload["cycle_members"]:
            query_db(
                """INSERT INTO cycle_members
                (cycle_id, member_name, queue_position_before, participated, lnd_awarded, tns_awarded)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (cycle_id, cm["name"], cm["queue_before"], cm["participated"], cm["lnd_awarded"], cm["tns_awarded"]),
                commit=True
            )

        current_q = query_db(f"SELECT * FROM members ORDER BY {q_col}, id", fetchall=True)
        sel_ids = set(payload["selected_ids"])
        part_ids = set(payload["participated_ids"])

        skipped = [m for m in current_q if m["id"] in sel_ids and m["id"] not in part_ids]
        untouched = [m for m in current_q if m["id"] not in sel_ids]
        participated = [m for m in current_q if m["id"] in part_ids]
        new_q = skipped + untouched + participated

        for pos, m in enumerate(new_q, start=1):
            query_db(f"UPDATE members SET {q_col} = ? WHERE id = ?", (pos, m["id"]), commit=True)

    await broadcast(get_state())

async def handler(websocket):
    CONNECTED_CLIENTS.add(websocket)
    try:
        await websocket.send(json.dumps(get_state()))
        async for message in websocket:
            data = json.loads(message)
            await handle_action(data)
    except websockets.exceptions.ConnectionClosedError:
        pass
    finally:
        CONNECTED_CLIENTS.remove(websocket)

async def main():
    init_db()
    print(f"🚀 Server Online | DB Path: {DB_PATH} | Listening on {HOST}:{PORT}")
    async with websockets.serve(handler, HOST, PORT):
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
