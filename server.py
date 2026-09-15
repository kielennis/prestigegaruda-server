import os
import json
import sqlite3
import asyncio
from pathlib import Path
from datetime import datetime
from aiohttp import web, WSMsgType

# Paths & Settings
APP_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent))
APP_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = APP_DIR / "prestigegaruda_auction.db"

PORT = int(os.environ.get("PORT", 8765))
MAX_MEMBERS = 80
CONNECTED_CLIENTS = set()

# Database Setup & Initialization
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
                "INSERT INTO league_teams (team_number, battlefield_type, slot_main_dps, slot_sub_dps, slot_utility, slot_bard, slot_fs) VALUES (?, 'Main', NULL, NULL, NULL, NULL, NULL)",
                (i,)
            )
        for i in range(9, 17):
            conn.execute(
                "INSERT INTO league_teams (team_number, battlefield_type, slot_main_dps, slot_sub_dps, slot_utility, slot_bard, slot_fs) VALUES (?, 'Sub', NULL, NULL, NULL, NULL, NULL)",
                (i,)
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
    cycle_members = query_db("SELECT * FROM cycle_members ORDER BY id DESC", fetchall=True)
    return {
        "event": "STATE_UPDATE",
        "members": members,
        "teams": teams,
        "cycles": cycles,
        "cycle_members": cycle_members
    }

async def broadcast(message):
    if CONNECTED_CLIENTS:
        payload = json.dumps(message)
        await asyncio.gather(*[client.send_str(payload) for client in CONNECTED_CLIENTS])

# Action Processing (Matches app.py Business Logic)
async def handle_action(data):
    action = data.get("action")
    payload = data.get("payload", {})

    if action == "ADD_MEMBER":
        count = query_db("SELECT COUNT(*) AS c FROM members", fetchone=True)["c"]
        if count < MAX_MEMBERS:
            gl_pos = (query_db("SELECT COALESCE(MAX(gl_queue_position), 0) AS p FROM members", fetchone=True)["p"]) + 1
            eo_pos = (query_db("SELECT COALESCE(MAX(eo_queue_position), 0) AS p FROM members", fetchone=True)["p"]) + 1
            query_db(
                """INSERT INTO members 
                (name, character_class, role, notes, gl_queue_position, eo_queue_position, created_at) 
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    payload["name"],
                    payload.get("character_class", ""),
                    payload.get("role", "Main DPS"),
                    payload.get("notes", ""),
                    gl_pos,
                    eo_pos,
                    datetime.now().strftime("%Y-%m-%d %H:%M")
                ),
                commit=True
            )

    elif action == "EDIT_MEMBER":
        query_db(
            """UPDATE members 
            SET name = ?, character_class = ?, role = ?, notes = ? 
            WHERE id = ?""",
            (
                payload["name"],
                payload.get("character_class", ""),
                payload.get("role", "Main DPS"),
                payload.get("notes", ""),
                payload["id"]
            ),
            commit=True
        )

    elif action == "DELETE_MEMBER":
        m_id = payload["id"]
        # Clear team assignments across slots
        for col in ["slot_main_dps", "slot_sub_dps", "slot_utility", "slot_bard", "slot_fs"]:
            query_db(f"UPDATE league_teams SET {col} = NULL WHERE {col} = ?", (m_id,), commit=True)
        
        # Remove member
        query_db("DELETE FROM members WHERE id = ?", (m_id,), commit=True)

        # Recalculate GL and EO positions linearly
        for queue_col in ["gl_queue_position", "eo_queue_position"]:
            rows = query_db(f"SELECT id FROM members ORDER BY {queue_col}, id", fetchall=True)
            for pos, row in enumerate(rows, start=1):
                query_db(f"UPDATE members SET {queue_col} = ? WHERE id = ?", (pos, row["id"]), commit=True)

    elif action == "RESET_ALL":
        query_db("DELETE FROM members", commit=True)
        query_db(
            """UPDATE league_teams 
            SET slot_main_dps = NULL, slot_sub_dps = NULL, slot_utility = NULL, slot_bard = NULL, slot_fs = NULL""",
            commit=True
        )

    elif action == "UPDATE_TEAM_SLOT":
        query_db(
            """UPDATE league_teams 
            SET slot_main_dps = ?, slot_sub_dps = ?, slot_utility = ?, slot_bard = ?, slot_fs = ? 
            WHERE team_number = ?""",
            (
                payload.get("slot_main_dps"),
                payload.get("slot_sub_dps"),
                payload.get("slot_utility"),
                payload.get("slot_bard"),
                payload.get("slot_fs"),
                payload["team_number"]
            ),
            commit=True
        )

    elif action == "FINALIZE_AUCTION":
        auction_type = payload["auction_type"]
        q_col = "gl_queue_position" if auction_type == "GL" else "eo_queue_position"
        
        puppet_count = payload["puppet_count"]
        lnd_total = payload["lnd_total"]
        tns_total = payload["tns_total"]
        participated_ids = set(payload["participated_ids"])
        selected_ids = set(payload["selected_ids"])
        
        participant_count = len(participated_ids)
        if participant_count > 0:
            lnd_each = lnd_total // participant_count
            lnd_leftover = lnd_total % participant_count
            tns_each = tns_total // participant_count
            tns_leftover = tns_total % participant_count
        else:
            lnd_each, lnd_leftover, tns_each, tns_leftover = 0, 0, 0, 0

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

        # Record Auction Cycle
        cycle_id = query_db(
            """INSERT INTO auction_cycles 
            (auction_type, puppet_count, lnd_total, tns_total, participant_count, lnd_each, lnd_leftover, tns_each, tns_leftover, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (auction_type, puppet_count, lnd_total, tns_total, participant_count, lnd_each, lnd_leftover, tns_each, tns_leftover, now_str),
            commit=True
        )

        current_queue = query_db(f"SELECT * FROM members ORDER BY {q_col}, id", fetchall=True)

        # Log Cycle Member Details
        selected_members = [m for m in current_queue if m["id"] in selected_ids]
        for m in selected_members:
            is_part = m["id"] in participated_ids
            query_db(
                """INSERT INTO cycle_members 
                (cycle_id, member_name, queue_position_before, participated, lnd_awarded, tns_awarded)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    cycle_id,
                    m["name"],
                    m[q_col],
                    1 if is_part else 0,
                    lnd_each if is_part else 0,
                    tns_each if is_part else 0
                ),
                commit=True
            )

        # Reorder Queue: skipped + untouched + participated
        skipped = [m for m in current_queue if m["id"] in selected_ids and m["id"] not in participated_ids]
        untouched = [m for m in current_queue if m["id"] not in selected_ids]
        participated = [m for m in current_queue if m["id"] in participated_ids]

        new_queue = skipped + untouched + participated
        for pos, m in enumerate(new_queue, start=1):
            query_db(f"UPDATE members SET {q_col} = ? WHERE id = ?", (pos, m["id"]), commit=True)

    await broadcast(get_state())

# Web Application Handlers
async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    CONNECTED_CLIENTS.add(ws)
    await ws.send_json(get_state())

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                data = json.loads(msg.data)
                await handle_action(data)
    finally:
        CONNECTED_CLIENTS.remove(ws)
    return ws

async def index_handler(request):
    return web.FileResponse(Path(__file__).parent / "index.html")

app = web.Application()
app.router.add_get("/", index_handler)
app.router.add_get("/ws", websocket_handler)
app.router.add_static("/", path=Path(__file__).parent, name="static")

if __name__ == "__main__":
    init_db()
    print(f"🚀 PrestigeGaruda Server listening on port {PORT}")
    web.run_app(app, host="0.0.0.0", port=PORT)
