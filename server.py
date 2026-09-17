import os
import sqlite3
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import List, Optional
import json

from fastapi import FastAPI, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

APP_DIR = Path(__file__).resolve().parent

# Railway persistent Volume database path.
# Railway sets RAILWAY_VOLUME_MOUNT_PATH when a Volume is mounted.
# The database remains on the persistent Volume across deployments.
# Local fallback keeps development behavior unchanged.
VOLUME_PATH = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
DB_PATH = (Path(VOLUME_PATH) / "prestigegaruda_auction.db") if VOLUME_PATH else (APP_DIR / "prestigegaruda_auction.db")

MAX_MEMBERS = 80
JAKARTA_TZ = ZoneInfo("Asia/Jakarta")

ROLE_CLASSES = {
    "Main DPS": ["Lord Knight", "High Wizard", "Doram", "Sniper", "Rebellion", "SinX", "Stalker", "Paladin", "Champion", "Professor", "Mastersmith"],
    "Sub DPS": ["Lord Knight", "High Wizard", "Doram", "Sniper", "Rebellion", "SinX", "Stalker", "Paladin", "Champion", "Professor", "Mastersmith"],
    "Utility": ["Doram", "Bio", "Professor"],
    "Healer": ["Bard", "Dancer"],
    "Support": ["Priest"]
}

# WebSocket Connection Manager for Real-Time Updates
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except:
                pass

manager = ConnectionManager()

class Database:
    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.create_tables()

    def create_tables(self):
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            role TEXT NOT NULL,
            class_name TEXT NOT NULL,
            gl_queue_position INTEGER NOT NULL DEFAULT 0,
            eo_queue_position INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'Pending',
            email TEXT NOT NULL,
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
            status TEXT NOT NULL DEFAULT 'Waiting',
            proof_checked INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(cycle_id) REFERENCES auction_cycles(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS cycle_officer_leftovers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_id INTEGER NOT NULL,
            officer_user_id INTEGER NOT NULL,
            officer_username TEXT NOT NULL,
            lnd_awarded INTEGER NOT NULL DEFAULT 0,
            tns_awarded INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(cycle_id) REFERENCES auction_cycles(id) ON DELETE CASCADE,
            FOREIGN KEY(officer_user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS cycle_leftover_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_id INTEGER NOT NULL, member_id INTEGER NOT NULL, member_name TEXT NOT NULL,
            lnd_awarded INTEGER NOT NULL DEFAULT 0, tns_awarded INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(cycle_id) REFERENCES auction_cycles(id) ON DELETE CASCADE,
            FOREIGN KEY(member_id) REFERENCES members(id)
        );

        CREATE TABLE IF NOT EXISTS auction_draft_state (
            auction_type TEXT PRIMARY KEY,
            state_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
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
        
        # Backward-compatible migration for databases created before the status column.
        try:
            self.conn.execute("ALTER TABLE cycle_members ADD COLUMN status TEXT NOT NULL DEFAULT 'Waiting'")
            self.conn.commit()
        except sqlite3.OperationalError:
            pass

        # Backward-compatible migration for auction proof checkbox.
        try:
            self.conn.execute("ALTER TABLE cycle_members ADD COLUMN proof_checked INTEGER NOT NULL DEFAULT 0")
            self.conn.commit()
        except sqlite3.OperationalError:
            pass

        admin_exists = self.fetchone("SELECT COUNT(*) AS c FROM users WHERE role = 'Admin'")["c"]
        if admin_exists == 0:
            now_str = datetime.now(JAKARTA_TZ).strftime("%Y-%m-%d %H:%M")
            self.execute(
                "INSERT INTO users (username, password, role, email, created_at) VALUES (?, ?, ?, ?, ?)",
                ("admin", "admin123", "Admin", "drethan.game@gmail.com", now_str)
            )

        count = self.fetchone("SELECT COUNT(*) AS c FROM league_teams")["c"]
        if count == 0:
            for i in range(1, 9):
                self.execute(
                    "INSERT INTO league_teams (team_number, battlefield_type, slot_main_dps, slot_sub_dps, slot_utility, slot_bard, slot_fs) VALUES (?, 'Main', NULL, NULL, NULL, NULL, NULL)",
                    (i,)
                )
            for i in range(9, 17):
                self.execute(
                    "INSERT INTO league_teams (team_number, battlefield_type, slot_main_dps, slot_sub_dps, slot_utility, slot_bard, slot_fs) VALUES (?, 'Sub', NULL, NULL, NULL, NULL, NULL)",
                    (i,)
                )
        self.conn.commit()

    def execute(self, sql, params=()):
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur

    def fetchall(self, sql, params=()):
        return [dict(row) for row in self.conn.execute(sql, params).fetchall()]

    def fetchone(self, sql, params=()):
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else None

db = Database()
app = FastAPI(title="PrestigeGaruda Web Suite")

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PrestigeGaruda Auction & Strike-Force Suite</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        :root { --bg:#070b12; --panel:#0d1420; --panel2:#111b2a; --line:#1d2a3d; --cyan:#22d3ee; --cyan2:#0891b2; --gold:#fbbf24; --muted:#8290a5; }
        * { box-sizing:border-box; scrollbar-width:thin; scrollbar-color:#244158 #080d15; }
        body { margin:0; min-height:100vh; background:radial-gradient(circle at 15% 0%,rgba(34,211,238,.08),transparent 28%),radial-gradient(circle at 90% 10%,rgba(251,191,36,.05),transparent 24%),var(--bg); color:#dbe7f3; font-family:Inter,'Segoe UI',sans-serif; }
        body::before { content:""; position:fixed; inset:0; pointer-events:none; opacity:.025; background-image:linear-gradient(rgba(255,255,255,.7) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.7) 1px,transparent 1px); background-size:36px 36px; }
        .app-shell { display:flex; min-height:100vh; position:relative; z-index:1; }
        .sidebar { width:250px; flex:0 0 250px; background:rgba(8,13,22,.94); border-right:1px solid var(--line); padding:22px 14px; position:sticky; top:0; height:100vh; display:flex; flex-direction:column; }
        .brand { padding:8px 10px 22px; border-bottom:1px solid var(--line); margin-bottom:18px; }
        .brand-mark { width:42px;height:42px;border:1px solid rgba(34,211,238,.5);border-radius:12px;display:grid;place-items:center;background:rgba(34,211,238,.08);box-shadow:0 0 24px rgba(34,211,238,.1);font-size:22px; }
        .brand h1 { font-size:15px; letter-spacing:.14em; margin:12px 0 3px; color:#eafaff; }
        .brand p { font-size:9px; color:#627187; letter-spacing:.16em; margin:0; }
        .nav-label { font-size:9px; letter-spacing:.18em; color:#536277; padding:0 10px 8px; font-weight:800; }
        .tab-btn { width:100%; text-align:left; background:transparent; color:#8090a5; border:1px solid transparent; font-weight:700; font-size:11px; letter-spacing:.04em; border-radius:10px; padding:11px 12px; margin:3px 0; transition:.2s; }
        .tab-btn:hover { background:#0e1827; color:#d9faff; border-color:#17283b; transform:translateX(2px); }
        .tab-btn.active { background:linear-gradient(90deg,rgba(34,211,238,.13),rgba(34,211,238,.035)); color:var(--cyan); border-color:rgba(34,211,238,.22); box-shadow:inset 3px 0 var(--cyan),0 6px 18px rgba(0,0,0,.16); }
        .sidebar-footer { margin-top:auto; padding:12px 10px; border-top:1px solid var(--line); color:#64748b; font-size:10px; }
        .main-area { flex:1; min-width:0; padding:18px 22px 28px; }
        .topbar { display:flex; justify-content:space-between; align-items:center; gap:18px; padding:6px 0 14px; border-bottom:1px solid var(--line); margin-bottom:16px; }
        .topbar-title { font-size:22px; font-weight:900; letter-spacing:.04em; color:#f1f7fb; }
        .topbar-sub { color:#718198; font-size:11px; margin-top:4px; }
        .status-pill { display:inline-flex; align-items:center; gap:7px; padding:8px 11px; border:1px solid var(--line); background:#0b121d; border-radius:999px; font-size:10px; color:#9cafc4; }
        .status-dot { width:7px;height:7px;border-radius:50%;background:#22c55e;box-shadow:0 0 10px #22c55e; }
        .cyber-card { background:linear-gradient(145deg,rgba(15,24,38,.97),rgba(8,14,24,.98)); border:1px solid var(--line); border-radius:12px; box-shadow:0 8px 24px rgba(0,0,0,.22),inset 0 1px rgba(255,255,255,.025); position:relative; overflow:hidden; }
        .cyber-card::before { content:""; position:absolute; left:0; right:0; top:0; height:1px; background:linear-gradient(90deg,transparent,rgba(34,211,238,.38),transparent); opacity:.7; }
        .cyber-card:hover { border-color:#2b4059; box-shadow:0 10px 28px rgba(0,0,0,.28),0 0 18px rgba(34,211,238,.035); }
        .section-title { color:#e8faff; font-weight:900; letter-spacing:.07em; font-size:13px; text-transform:uppercase; }
        .section-kicker { color:#5f7289; font-size:9px; letter-spacing:.16em; text-transform:uppercase; font-weight:800; }
        .glow-cyan { text-shadow:0 0 14px rgba(34,211,238,.28); }
        .gold-accent { color:var(--gold); }
        .compact-panel { padding:12px !important; }
        /* Compact roster + queue layout */
        #tab-members { gap:10px !important; }
        #tab-members .cyber-card { padding:12px !important; }
        #tab-members h2 { font-size:14px !important; margin-bottom:10px !important; }
        #tab-members form, #tab-members .space-y-4 { gap:8px !important; }
        #tab-members .grid { gap:8px !important; }
        #tab-members label { font-size:9px !important; margin-bottom:3px !important; letter-spacing:.04em; }
        #tab-members .cyber-input { min-height:31px; height:31px; padding:5px 8px !important; font-size:12px !important; }
        #tab-members textarea.cyber-input { height:auto; min-height:76px; line-height:1.35; }
        #tab-members .cyber-btn { min-height:31px; padding:6px 10px !important; font-size:10px; }
        #tab-members table { font-size:11px !important; }
        #tab-members thead th { padding:6px 7px !important; font-size:9px !important; white-space:nowrap; }
        #tab-members tbody td { padding:5px 7px !important; line-height:1.15; white-space:nowrap; }
        #tab-members .member-status { padding:2px 6px; font-size:8px; }
        #tab-members .auth-restricted-col button { padding:3px 6px !important; font-size:9px !important; }
        #tab-members .text-lg { font-size:14px !important; }
        #tab-members .text-sm { font-size:11px !important; }
        #tab-members .text-xs { font-size:9px !important; }
        #tab-members .mb-4 { margin-bottom:8px !important; }
        #tab-members .mb-1 { margin-bottom:3px !important; }
        #tab-members .p-6 { padding:12px !important; }
        .team-card { border-radius:10px; }
        .team-card .cyber-input { min-height:31px; }
        select.cyber-input option { background:#0b121d; color:#e5eef7; }
        .cyber-input::placeholder { color:#4f6074; }
        .cyber-btn:active { transform:translateY(0); }
        .danger-btn { border-color:rgba(248,113,113,.45); color:#fca5a5; }
        .danger-btn:hover { background:#ef4444; color:white; box-shadow:0 0 18px rgba(239,68,68,.2); }
        .queue-item { background:rgba(8,14,24,.62); border:1px solid #1b293b; border-radius:9px; }
        .queue-item:hover { border-color:#28435b; background:rgba(12,24,38,.8); }
        .cyber-input { background:#080e17; border:1px solid #223149; color:#f8fafc; border-radius:9px; transition:.2s; }
        .cyber-input:focus { border-color:var(--cyan); outline:none; box-shadow:0 0 0 3px rgba(34,211,238,.08); }
        .cyber-btn { background:linear-gradient(135deg,#102538,#0c1826); border:1px solid rgba(34,211,238,.55); color:var(--cyan); font-weight:800; border-radius:9px; transition:.2s; letter-spacing:.04em; }
        .cyber-btn:hover { background:var(--cyan); color:#041018; box-shadow:0 0 22px rgba(34,211,238,.22); transform:translateY(-1px); }
        .stat-card { padding:17px; position:relative; overflow:hidden; }
        .stat-card::after { content:""; position:absolute; width:90px;height:90px;right:-35px;top:-35px;border-radius:50%;background:rgba(34,211,238,.07); }
        .stat-label { font-size:9px; text-transform:uppercase; letter-spacing:.16em; color:#718198; font-weight:800; }
        .stat-value { font-size:27px; font-weight:900; color:#effaff; margin-top:5px; }
        .stat-meta { font-size:10px; color:#5f7188; margin-top:3px; }
        .member-status { display:inline-flex; align-items:center; padding:3px 7px; border-radius:999px; font-size:9px; font-weight:800; border:1px solid; }
        .member-status.ready { color:#67e8f9; border-color:#155e75; background:#082f3b; }
        .member-status.assigned { color:#fbbf24; border-color:#854d0e; background:#3a2505; }
        table tbody tr { transition:.15s; } table tbody tr:hover { background:rgba(34,211,238,.035); }
        .auction-monitor-tables table { min-width: 0; }
        .auction-monitor-tables th, .auction-monitor-tables td { white-space: nowrap; }
        @media(max-width:900px){ .sidebar{width:205px;flex-basis:205px}.main-area{padding:18px}.topbar-title{font-size:18px} }
        @media(max-width:700px){
            .app-shell{display:block}
            .sidebar{position:relative;width:100%;height:auto;border-right:0;border-bottom:1px solid var(--line);padding:12px}
            .brand{display:flex;align-items:center;gap:10px;padding:4px 6px 12px;margin-bottom:10px}
            .brand h1{margin:0}.brand p{display:none}.brand-mark{width:34px;height:34px}
            .nav-label{display:none}.nav-scroll{display:flex;overflow-x:auto;gap:5px}.tab-btn{width:auto;white-space:nowrap}
            .sidebar-footer{display:none}.main-area{padding:10px}
            .auction-layout{display:flex !important;flex-direction:column !important;width:100% !important;gap:8px !important}
            .auction-left,.auction-monitor{width:100% !important;min-width:0 !important}
            .auction-monitor{display:block !important;order:2 !important;visibility:visible !important}
            .auction-monitor .overflow-x-auto{width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch}
            .auction-monitor table{min-width:620px}
            .auction-monitor h3{font-size:12px;white-space:normal}
        }
    </style>
</head>
<body>
<div class="app-shell">
    <aside class="sidebar">
        <div class="brand"><div class="brand-mark">🦅</div><div><h1>PRESTIGEGARUDA</h1><p>GUILD COMMAND CENTER</p></div></div>
        <div class="nav-label">COMMAND MENU</div>
        <div class="nav-scroll" id="nav-tabs">
            <button onclick="switchTab('members')" class="tab-btn active">◈ ROSTER & QUEUES</button>
            <button onclick="switchTab('teams')" class="tab-btn">◫ BATTLEFIELD TEAMS</button>
            <button onclick="switchTab('gl')" class="tab-btn">⚡ GL AUCTION</button>
            <button onclick="switchTab('eo')" class="tab-btn">⚡ EO AUCTION</button>
            <button onclick="switchTab('history')" class="tab-btn">▤ AUCTION ARCHIVES</button>
            <button onclick="switchTab('admin')" id="admin-tab-btn" class="tab-btn hidden">⚙ ADMIN PANEL</button>
        </div>
        <div class="sidebar-footer">AUCTION ENGINE v2.0<br><span id="capacity-badge">Loading Capacity...</span></div>
    </aside>
    <main class="main-area">
        <header class="topbar">
            <div><div class="topbar-title">Guild Operations</div><div class="topbar-sub">Roster, deployments, auction cycles & history</div></div>
            <div class="flex items-center gap-2"><div class="status-pill"><span class="status-dot"></span><span id="auth-status">Viewer</span></div><button onclick="openAuthModal()" id="auth-btn" class="cyber-btn px-3 py-2 text-xs">LOGIN / REGISTER</button></div>
        </header>

        <section id="command-stats" class="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-6">
            <div class="cyber-card stat-card"><div class="stat-label">Registered Members</div><div id="stat-members" class="stat-value">0</div><div class="stat-meta">of 80 capacity</div></div>
            <div class="cyber-card stat-card"><div class="stat-label">Team Slots</div><div id="stat-slots" class="stat-value">0/80</div><div class="stat-meta">5 slots × 16 teams</div></div>
            <div class="cyber-card stat-card"><div class="stat-label">GL Queue</div><div id="stat-gl" class="stat-value">0</div><div class="stat-meta">members waiting</div></div>
            <div class="cyber-card stat-card"><div class="stat-label">EO Queue</div><div id="stat-eo" class="stat-value">0</div><div class="stat-meta">members waiting</div></div>
        </section>

        <!-- TAB 1: MEMBERS -->
        <div id="tab-members" class="space-y-2 tab-content">
            <div class="grid grid-cols-1 xl:grid-cols-2 gap-2">
            <div class="cyber-card p-4 auth-restricted">
                <h2 class="text-base font-bold text-cyan-400 mb-3">⚔️ REGISTER ROSTER MEMBER</h2>
                <form id="member-form" onsubmit="addMember(event)" class="grid grid-cols-1 md:grid-cols-3 gap-2">
                    <div>
                        <label class="block text-xs uppercase mb-1 text-gray-400">Character Name (Unique)</label>
                        <input type="text" id="m-name" required class="cyber-input w-full p-1.5 rounded text-sm">
                    </div>
                    <div>
                        <label class="block text-xs uppercase mb-1 text-gray-400">Role</label>
                        <select id="m-role" onchange="updateClassOptions('m-role', 'm-class')" class="cyber-input w-full p-1.5 rounded text-sm">
                            <option>Main DPS</option><option>Sub DPS</option><option>Utility</option><option>Healer</option><option>Support</option>
                        </select>
                    </div>
                    <div>
                        <label class="block text-xs uppercase mb-1 text-gray-400">Class</label>
                        <select id="m-class" class="cyber-input w-full p-1.5 rounded text-sm"></select>
                    </div>
                    <div class="md:col-span-3">
                        <button type="submit" class="cyber-btn w-full py-1.5 text-sm">+ REGISTER MEMBER</button>
                    </div>
                </form>
            </div>

            <!-- BATCH ADD MEMBER SECTION -->
            <div class="cyber-card p-4 auth-restricted">
                <h2 class="text-lg font-bold text-cyan-400 mb-4">⚡ BATCH MEMBER ADD</h2>
                <div class="space-y-4">
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div>
                            <label class="block text-xs uppercase mb-1 text-gray-400">Default Role</label>
                            <select id="batch-role" onchange="updateClassOptions('batch-role', 'batch-class')" class="cyber-input w-full p-2 rounded text-sm">
                                <option>Main DPS</option><option>Sub DPS</option><option>Utility</option><option>Healer</option><option>Support</option>
                            </select>
                        </div>
                        <div>
                            <label class="block text-xs uppercase mb-1 text-gray-400">Default Class</label>
                            <select id="batch-class" class="cyber-input w-full p-2 rounded text-sm"></select>
                        </div>
                    </div>
                    <div>
                        <div class="flex justify-between items-center mb-1">
                            <label class="block text-xs uppercase text-gray-400">Member Names (One per line)</label>
                        </div>
                        <textarea id="batch-names" rows="5" class="cyber-input w-full p-2 rounded text-sm font-mono" placeholder="Paste names here, one per line..."></textarea>
                    </div>
                    <button onclick="submitBatchMembers()" class="cyber-btn w-full py-2">⚡ EXECUTE BATCH REGISTRATION</button>
                </div>
            </div>
            </div>

            <div class="cyber-card p-4 overflow-x-auto">
                <table class="w-full text-left border-collapse">
                    <thead>
                        <tr class="border-b border-gray-800 text-cyan-400 text-xs uppercase">
                            <th class="p-3">GLQ</th><th class="p-3">EOQ</th><th class="p-3">Name</th><th class="p-3">Role</th><th class="p-3">Class</th><th class="p-3">Status</th><th class="p-3">Created</th><th class="p-3 auth-restricted-col">Actions</th>
                        </tr>
                    </thead>
                    <tbody id="members-table-body" class="text-sm divide-y divide-gray-800"></tbody>
                </table>
            </div>
        </div>

        <!-- TAB 2: TEAMS -->
        <div id="tab-teams" class="space-y-6 tab-content hidden">
            <div class="cyber-card p-6 flex flex-col md:flex-row justify-between items-center gap-4 auth-restricted">
                <p class="text-sm text-gray-400 italic">Teams 01-08 map to Main Battlefield. Teams 09-16 handle Sub Battlefield.</p>
                <button onclick="saveAllTeams()" class="cyber-btn px-6 py-2">🔒 COMMIT ALL DEPLOYMENTS</button>
            </div>
            <div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4" id="teams-grid-container"></div>
        </div>

        <!-- TAB 3 & 4: GL / EO AUCTION -->
        <div id="tab-gl" class="space-y-6 tab-content hidden"></div>
        <div id="tab-eo" class="space-y-6 tab-content hidden"></div>

        <!-- TAB 5: HISTORY -->
        <div id="tab-history" class="space-y-6 tab-content hidden">
            <div class="cyber-card p-6 space-y-4">
                <button onclick="loadHistory()" class="cyber-btn px-4 py-2 mb-2">🔄 REFRESH ARCHIVES</button>
                <div class="overflow-x-auto mb-4">
                    <table class="w-full text-left border-collapse text-xs">
                        <thead>
                            <tr class="border-b border-gray-800 text-cyan-400 uppercase">
                                <th class="p-2">Cycle</th><th class="p-2">Type</th><th class="p-2">Puppet</th><th class="p-2">LND</th><th class="p-2">TNS</th><th class="p-2">Participants</th><th class="p-2">Timestamp</th><th class="p-2">Action</th>
                            </tr>
                        </thead>
                        <tbody id="history-table-body" class="divide-y divide-gray-800"></tbody>
                    </table>
                </div>
                <div id="history-detail-container" class="space-y-4 hidden">
                    <h3 class="font-bold text-cyan-400" id="archive-detail-title">Archive Details & Bid Editing</h3>
                    <div class="overflow-x-auto">
                        <table class="w-full text-left border-collapse text-xs">
                            <thead>
                                <tr class="border-b border-gray-800 text-cyan-400 uppercase">
                                    <th class="p-2">Member</th><th class="p-2">Queue Pos</th><th class="p-2">Participated</th><th class="p-2">Proof</th><th class="p-2">LND Awarded</th><th class="p-2">TNS Awarded</th><th class="p-2 auth-restricted-col">Action</th>
                                </tr>
                            </thead>
                            <tbody id="archive-members-body" class="divide-y divide-gray-800"></tbody>
                        </table>
                    </div>
                    <div class="overflow-x-auto">
                        <h3 class="font-bold text-amber-400 mt-4 mb-2">Leftover Contribution — Selected Recipients Only</h3>
                        <table class="w-full text-left border-collapse text-xs">
                            <thead><tr class="border-b border-gray-800 text-amber-400 uppercase"><th class="p-2">Member</th><th class="p-2">LND Leftover</th><th class="p-2">TNS Leftover</th></tr></thead>
                            <tbody id="archive-leftover-members-body" class="divide-y divide-gray-800"></tbody>
                        </table>
                    </div>
                    <div class="overflow-x-auto">
                        <h3 class="font-bold text-amber-400 mt-4 mb-2">Officer Leftover Distribution</h3>
                        <table class="w-full text-left border-collapse text-xs">
                            <thead><tr class="border-b border-gray-800 text-amber-400 uppercase"><th class="p-2">Officer</th><th class="p-2">LND Leftover</th><th class="p-2">TNS Leftover</th></tr></thead>
                            <tbody id="archive-officers-body" class="divide-y divide-gray-800"></tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>

        <!-- TAB 6: ADMIN PANEL -->
        <div id="tab-admin" class="space-y-6 tab-content hidden">
            <div class="cyber-card p-6">
                <h2 class="text-lg font-bold text-cyan-400 mb-4">🛡️ USER ROLE MANAGEMENT (Admin Only)</h2>
                <div class="overflow-x-auto">
                    <table class="w-full text-left border-collapse text-sm">
                        <thead>
                            <tr class="border-b border-gray-800 text-cyan-400 text-xs uppercase">
                                <th class="p-3">Username</th><th class="p-3">Email</th><th class="p-3">Role</th><th class="p-3">Registered</th><th class="p-3">Actions</th>
                            </tr>
                        </thead>
                        <tbody id="admin-users-body" class="divide-y divide-gray-800"></tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>

    </main>
</div>

    <!-- AUTH MODAL -->
    <div id="auth-modal" class="fixed inset-0 bg-black/80 flex items-center justify-center hidden z-50 p-4">
        <div class="cyber-card p-6 max-w-md w-full space-y-4 relative">
            <button onclick="closeAuthModal()" class="absolute top-3 right-3 text-gray-400 hover:text-white font-bold">✕</button>
            <h2 id="auth-modal-title" class="text-lg font-bold text-cyan-400">LOGIN TO COMMAND SUITE</h2>
            <div id="auth-error" class="text-red-400 text-xs hidden"></div>
            <form id="auth-form" onsubmit="handleAuthSubmit(event)" class="space-y-3">
                <div>
                    <label class="block text-xs uppercase mb-1 text-gray-400">Username</label>
                    <input type="text" id="auth-user" required class="cyber-input w-full p-2 rounded text-sm">
                </div>
                <div>
                    <label class="block text-xs uppercase mb-1 text-gray-400">Password</label>
                    <input type="password" id="auth-pass" required class="cyber-input w-full p-2 rounded text-sm">
                </div>
                <div id="auth-email-field" class="hidden">
                    <label class="block text-xs uppercase mb-1 text-gray-400">Email (Sends registration notice to drethan.game@gmail.com)</label>
                    <input type="email" id="auth-email" class="cyber-input w-full p-2 rounded text-sm">
                </div>
                <button type="submit" id="auth-submit-btn" class="cyber-btn w-full py-2">LOGIN</button>
            </form>
            <div class="text-center text-xs pt-2">
                <span id="auth-toggle-text" class="text-gray-400">Need an account?</span> 
                <button onclick="toggleAuthMode()" class="text-cyan-400 font-bold ml-1 underline">Register</button>
            </div>
        </div>
    </div>

    <!-- EDIT MEMBER MODAL -->
    <div id="edit-member-modal" class="fixed inset-0 bg-black/80 flex items-center justify-center hidden z-50 p-4">
        <div class="cyber-card p-6 max-w-md w-full space-y-4 relative">
            <button onclick="closeEditModal()" class="absolute top-3 right-3 text-gray-400 hover:text-white font-bold">✕</button>
            <h2 class="text-lg font-bold text-cyan-400">EDIT ROSTER MEMBER</h2>
            <form onsubmit="updateMember(event)" class="space-y-3">
                <input type="hidden" id="edit-m-id">
                <div>
                    <label class="block text-xs uppercase mb-1 text-gray-400">Character Name (Unique)</label>
                    <input type="text" id="edit-m-name" required class="cyber-input w-full p-2 rounded text-sm">
                </div>
                <div>
                    <label class="block text-xs uppercase mb-1 text-gray-400">Role</label>
                    <select id="edit-m-role" onchange="updateClassOptions('edit-m-role', 'edit-m-class')" class="cyber-input w-full p-2 rounded text-sm">
                        <option>Main DPS</option><option>Sub DPS</option><option>Utility</option><option>Healer</option><option>Support</option>
                    </select>
                </div>
                <div>
                    <label class="block text-xs uppercase mb-1 text-gray-400">Class</label>
                    <select id="edit-m-class" class="cyber-input w-full p-2 rounded text-sm"></select>
                </div>
                <button type="submit" class="cyber-btn w-full py-2">SAVE CHANGES</button>
            </form>
        </div>
    </div>

    <script>
        const roleClasses = {
            "Main DPS": ["Lord Knight", "High Wizard", "Doram", "Sniper", "Rebellion", "SinX", "Stalker", "Paladin", "Champion", "Professor", "Mastersmith"],
            "Sub DPS": ["Lord Knight", "High Wizard", "Doram", "Sniper", "Rebellion", "SinX", "Stalker", "Paladin", "Champion", "Professor", "Mastersmith"],
            "Utility": ["Doram", "Bio", "Professor"],
            "Healer": ["Bard", "Dancer"],
            "Support": ["Priest"]
        };

        let membersData = [];
        let rolesPool = { "Main DPS": [], "Sub DPS": [], "Utility": [], "Healer": [], "Support": [] };
        let currentUser = JSON.parse(localStorage.getItem('pg_user')) || { username: 'Guest', role: 'Viewer' };
        let isRegisterMode = false;
        let activeArchiveCycleId = null;

        let auctionSessions = {
            GL: { allMembers: [], skippedIds: new Set(), leftoverMembers: [], statuses: {}, proof: {}, allocations: {}, allocationManual: {}, statusesInitialized: false },
            EO: { allMembers: [], skippedIds: new Set(), leftoverMembers: [], statuses: {}, proof: {}, allocations: {}, allocationManual: {}, statusesInitialized: false }
        };

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const ws = new WebSocket(`${protocol}//${window.location.host}/ws`);

        ws.onmessage = function(event) {
            if (event.data === "refresh") {
                loadAppData();
                loadHistory();
                if (activeArchiveCycleId !== null) {
                    loadCycleDetail(activeArchiveCycleId);
                }
                // GL/EO auction state lives in a separate draft endpoint, so the
                // normal roster/team refresh must also refresh any already-open
                // auction tabs. This makes changes from another browser visible
                // without requiring a tab switch or manual refresh.
                if (auctionSessions.GL.tabInitialized) refreshAuctionFromServer('GL');
                if (auctionSessions.EO.tabInitialized) refreshAuctionFromServer('EO');
                const adminTab = document.getElementById('tab-admin');
                if (adminTab && !adminTab.classList.contains('hidden')) {
                    loadAdminUsers();
                }
            } else if (event.data === "auction_refresh:GL") {
                if (auctionSessions.GL.tabInitialized) refreshAuctionFromServer('GL');
            } else if (event.data === "auction_refresh:EO") {
                if (auctionSessions.EO.tabInitialized) refreshAuctionFromServer('EO');
            }
        };

        function updateClassOptions(roleSelectId, classSelectId, selectedClass = '') {
            const role = document.getElementById(roleSelectId).value;
            const classSelect = document.getElementById(classSelectId);
            const classes = roleClasses[role] || [];
            classSelect.innerHTML = classes.map(c => `<option value="${c}" ${c === selectedClass ? 'selected' : ''}>${c}</option>`).join('');
        }

        function updateAuthUI() {
            const isAdminOrOfficer = currentUser.role === 'Admin' || currentUser.role === 'Officer';
            const isAdmin = currentUser.role === 'Admin';
            
            document.getElementById('auth-status').innerText = `User: ${currentUser.username} (${currentUser.role})`;
            document.getElementById('auth-btn').innerText = currentUser.username === 'Guest' ? 'LOGIN / REGISTER' : 'LOGOUT';
            
            document.querySelectorAll('.auth-restricted').forEach(el => {
                el.style.display = isAdminOrOfficer ? 'block' : 'none';
            });
            document.querySelectorAll('.auth-restricted-col').forEach(el => {
                el.style.display = isAdminOrOfficer ? 'table-cell' : 'none';
            });
            
            const adminTab = document.getElementById('admin-tab-btn');
            if(isAdmin) {
                adminTab.classList.remove('hidden');
            } else {
                adminTab.classList.add('hidden');
            }
        }

        function openAuthModal() {
            if(currentUser.username !== 'Guest') {
                if(confirm("Log out of current account?")) {
                    localStorage.removeItem('pg_user');
                    currentUser = { username: 'Guest', role: 'Viewer' };
                    updateAuthUI();
                    loadAppData();
                }
                return;
            }
            isRegisterMode = false;
            document.getElementById('auth-modal-title').innerText = 'LOGIN TO COMMAND SUITE';
            document.getElementById('auth-email-field').classList.add('hidden');
            document.getElementById('auth-submit-btn').innerText = 'LOGIN';
            document.getElementById('auth-toggle-text').innerText = 'Need an account?';
            document.getElementById('auth-modal').classList.remove('hidden');
        }

        function closeAuthModal() {
            document.getElementById('auth-modal').classList.add('hidden');
            document.getElementById('auth-error').classList.add('hidden');
            document.getElementById('auth-form').reset();
        }

        function toggleAuthMode() {
            isRegisterMode = !isRegisterMode;
            if(isRegisterMode) {
                document.getElementById('auth-modal-title').innerText = 'REGISTER ACCOUNT (Notifies drethan.game@gmail.com)';
                document.getElementById('auth-email-field').classList.remove('hidden');
                document.getElementById('auth-submit-btn').innerText = 'REGISTER';
                document.getElementById('auth-toggle-text').innerText = 'Already have an account?';
            } else {
                document.getElementById('auth-modal-title').innerText = 'LOGIN TO COMMAND SUITE';
                document.getElementById('auth-email-field').classList.add('hidden');
                document.getElementById('auth-submit-btn').innerText = 'LOGIN';
                document.getElementById('auth-toggle-text').innerText = 'Need an account?';
            }
        }

        async function handleAuthSubmit(e) {
            e.preventDefault();
            const username = document.getElementById('auth-user').value;
            const password = document.getElementById('auth-pass').value;
            const email = document.getElementById('auth-email').value;
            const errBox = document.getElementById('auth-error');
            errBox.classList.add('hidden');

            const endpoint = isRegisterMode ? '/api/register' : '/api/login';
            const payload = isRegisterMode ? { username, password, email } : { username, password };

            const res = await fetch(endpoint, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            if(res.ok) {
                if(isRegisterMode) {
                    alert("Registration successful! Notice queued for drethan.game@gmail.com. Please wait for Admin approval to edit.");
                    toggleAuthMode();
                } else {
                    currentUser = data;
                    localStorage.setItem('pg_user', JSON.stringify(currentUser));
                    closeAuthModal();
                    updateAuthUI();
                    loadAppData();
                }
            } else {
                errBox.innerText = data.detail;
                errBox.classList.remove('hidden');
            }
        }

        function switchTab(tabId) {
            document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
            document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
            document.getElementById('tab-' + tabId).classList.remove('hidden');
            event.target.classList.add('active');
            if(tabId === 'gl' || tabId === 'eo') setupAuctionTab(tabId.toUpperCase());
            if(tabId === 'history') loadHistory();
            if(tabId === 'admin') loadAdminUsers();
        }

        async function loadAppData() {
            const res = await fetch('/api/data');
            const data = await res.json();
            membersData = data.members;
            rolesPool = data.roles_pool;
            document.getElementById('capacity-badge').innerText = `${membersData.length} / 80 MEMBERS | ${data.server_time}`;
            document.getElementById('stat-members').innerText = membersData.length;
            document.getElementById('stat-gl').innerText = membersData.length;
            document.getElementById('stat-eo').innerText = membersData.length;
            const assignedSlots = (data.teams || []).reduce((n, t) => n + ['slot_main_dps','slot_sub_dps','slot_utility','slot_bard','slot_fs'].filter(k => t[k]).length, 0);
            document.getElementById('stat-slots').innerText = `${assignedSlots}/80`;
            renderMembers();
            renderTeams(data.teams);
            enforceUniqueTeamMembers();
            updateAuthUI();
            updateClassOptions('m-role', 'm-class');
            updateClassOptions('batch-role', 'batch-class');
        }

        async function addMember(e) {
            e.preventDefault();
            const payload = {
                name: document.getElementById('m-name').value.trim(),
                role: document.getElementById('m-role').value,
                class_name: document.getElementById('m-class').value
            };
            const res = await fetch('/api/members', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
            if(res.ok) {
                document.getElementById('member-form').reset();
                updateClassOptions('m-role', 'm-class');
            } else {
                const err = await res.json();
                alert(err.detail);
            }
        }

        async function submitBatchMembers() {
            const rawText = document.getElementById('batch-names').value;
            const names = rawText.split('\\n').map(n => n.trim()).filter(n => n.length > 0);
            if(names.length === 0) {
                alert("Please enter at least one member name.");
                return;
            }
            const payload = {
                names: names,
                role: document.getElementById('batch-role').value,
                class_name: document.getElementById('batch-class').value
            };
            const res = await fetch('/api/members/batch', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            if(res.ok) {
                alert(`Successfully registered ${data.added_count} members in batch!`);
                document.getElementById('batch-names').value = '';
            } else {
                alert(data.detail);
            }
        }

        function openEditModal(id, name, role, className) {
            document.getElementById('edit-m-id').value = id;
            document.getElementById('edit-m-name').value = name;
            document.getElementById('edit-m-role').value = role;
            updateClassOptions('edit-m-role', 'edit-m-class', className);
            document.getElementById('edit-member-modal').classList.remove('hidden');
        }

        function closeEditModal() {
            document.getElementById('edit-member-modal').classList.add('hidden');
        }

        async function updateMember(e) {
            e.preventDefault();
            const id = document.getElementById('edit-m-id').value;
            const payload = {
                name: document.getElementById('edit-m-name').value.trim(),
                role: document.getElementById('edit-m-role').value,
                class_name: document.getElementById('edit-m-class').value
            };
            const res = await fetch(`/api/members/${id}`, { method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
            if(res.ok) {
                closeEditModal();
            } else {
                const err = await res.json();
                alert(err.detail);
            }
        }

        async function deleteMember(id) {
            if(confirm("Are you sure you want to remove this member?")) {
                await fetch(`/api/members/${id}`, { method: 'DELETE' });
            }
        }

        function renderMembers() {
            const isAdminOrOfficer = currentUser.role === 'Admin' || currentUser.role === 'Officer';
            const tbody = document.getElementById('members-table-body');
            tbody.innerHTML = membersData.map(m => `
                <tr class="hover:bg-gray-900">
                    <td class="p-2"><input type="number" min="1" value="${m.gl_queue_position}" data-member-id="${m.id}" data-queue="GL" onchange="updateQueuePosition(${m.id}, 'GL', this.value)" class="cyber-input w-16 p-1 text-center text-xs text-cyan-400 font-bold"></td>
                    <td class="p-2"><input type="number" min="1" value="${m.eo_queue_position}" data-member-id="${m.id}" data-queue="EO" onchange="updateQueuePosition(${m.id}, 'EO', this.value)" class="cyber-input w-16 p-1 text-center text-xs text-cyan-400 font-bold"></td>
                    <td class="p-3 font-semibold">${m.name}</td>
                    <td class="p-3">${m.role}</td>
                    <td class="p-3 text-cyan-200">${m.class_name}</td>
                    <td class="p-3"><span class="member-status ready">ACTIVE</span></td>
                    <td class="p-3 text-xs text-gray-500">${m.created_at}</td>
                    <td class="p-3 auth-restricted-col" style="display: ${isAdminOrOfficer ? 'table-cell' : 'none'};">
                        <button onclick="openEditModal(${m.id}, '${m.name.replace(/'/g, "\\'")}', '${m.role}', '${m.class_name}')" class="text-cyan-400 border border-cyan-500 px-2 py-1 rounded text-xs hover:bg-cyan-500 hover:text-black mr-1">EDIT</button>
                        <button onclick="deleteMember(${m.id})" class="text-red-400 border border-red-500 px-2 py-1 rounded text-xs hover:bg-red-500 hover:text-black">PURGE</button>
                    </td>
                </tr>
            `).join('');
        }

        async function updateQueuePosition(memberId, queueType, value) {
            const position = parseInt(value);
            if (!Number.isInteger(position) || position < 1) {
                alert('Queue number must be 1 or higher.');
                await loadData();
                return;
            }
            try {
                const res = await fetch(`/api/members/${memberId}/queue`, {
                    method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({queue: queueType, position: position})
                });
                if (!res.ok) {
                    const err = await res.json();
                    throw new Error(err.detail || 'Failed to update queue position.');
                }
            } catch (e) {
                alert(e.message);
                await loadData();
            }
        }

        function renderTeams(teams) {
            const container = document.getElementById('teams-grid-container');
            container.innerHTML = teams.map(t => {
                const isMain = t.battlefield_type === 'Main';
                return `
                <div class="cyber-card p-4 team-card flex flex-col space-y-3" data-team="${t.team_number}">
                    <div class="flex justify-between items-center border-b border-gray-800 pb-2">
                        <span class="font-black text-cyan-400">Team ${t.team_number}</span>
                        <span class="text-xs px-2 py-0.5 rounded font-bold ${isMain ? 'bg-red-950 text-red-400 border border-red-800' : 'bg-yellow-950 text-yellow-400 border border-yellow-800'}">${t.battlefield_type.toUpperCase()} BF</span>
                    </div>
                    <div class="space-y-2 text-xs">
                        <div><label class="text-gray-400 font-semibold block mb-0.5">Main DPS</label>${roleDropdown('slot_main_dps', 'Main DPS', t.slot_main_dps)}</div>
                        <div><label class="text-gray-400 font-semibold block mb-0.5">Sub DPS</label>${roleDropdown('slot_sub_dps', 'Sub DPS', t.slot_sub_dps)}</div>
                        <div><label class="text-gray-400 font-semibold block mb-0.5">Utility</label>${roleDropdown('slot_utility', 'Utility', t.slot_utility)}</div>
                        <div><label class="text-gray-400 font-semibold block mb-0.5">Healer</label>${roleDropdown('slot_bard', 'Healer', t.slot_bard)}</div>
                        <div><label class="text-gray-400 font-semibold block mb-0.5">Support</label>${roleDropdown('slot_fs', 'Support', t.slot_fs)}</div>
                    </div>
                </div>`;
            }).join('');
        }

        function roleDropdown(slotName, roleKey, selectedId) {
            const pool = rolesPool[roleKey] || [];
            let opts = `<option value="">--- VACANT SLOT ---</option>`;
            pool.forEach(m => {
                const id = String(m.id);
                const selected = id === String(selectedId || '') ? 'selected' : '';
                opts += `<option value="${m.id}" ${selected}>${m.name} (${m.class_name})</option>`;
            });
            return `<select class="cyber-input w-full p-1 text-xs team-slot" data-slot="${slotName}" data-role="${roleKey}" onchange="enforceUniqueTeamMembers()">${opts}</select>`;
        }

        function enforceUniqueTeamMembers() {
            const selects = Array.from(document.querySelectorAll('.team-slot'));
            const selectedIds = new Set(selects.map(s => String(s.value || '')).filter(Boolean));
            selects.forEach(select => {
                const currentValue = String(select.value || '');
                const roleKey = select.dataset.role;
                const pool = rolesPool[roleKey] || [];
                let html = `<option value="">--- VACANT SLOT ---</option>`;
                pool.forEach(m => {
                    const id = String(m.id);
                    if (id !== currentValue && selectedIds.has(id)) return;
                    html += `<option value="${m.id}" ${id === currentValue ? 'selected' : ''}>${m.name} (${m.class_name})</option>`;
                });
                select.innerHTML = html;
            });
        }

        async function saveAllTeams() {
            const cards = document.querySelectorAll('.team-card');
            const payload = [];
            cards.forEach(c => {
                const teamNum = parseInt(c.getAttribute('data-team'));
                const selects = c.querySelectorAll('.team-slot');
                payload.push({
                    team_number: teamNum,
                    slot_main_dps: selects[0].value ? parseInt(selects[0].value) : null,
                    slot_sub_dps: selects[1].value ? parseInt(selects[1].value) : null,
                    slot_utility: selects[2].value ? parseInt(selects[2].value) : null,
                    slot_bard: selects[3].value ? parseInt(selects[3].value) : null,
                    slot_fs: selects[4].value ? parseInt(selects[4].value) : null,
                });
            });
            const res = await fetch('/api/teams', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
            if(res.ok) alert("All Battlefield fireteams synced successfully.");
        }

        async function setupAuctionTab(type) {
            const container = document.getElementById(`tab-${type.toLowerCase()}`);
            const session = auctionSessions[type];

            // Do not rebuild the auction tab every time the user switches tabs.
            // Rebuilding it resets the input fields, selected leftover recipients,
            // statuses, allocations, and other in-progress auction state.
            // Refresh the member queue data only while keeping the existing UI/state.
            if (session.tabInitialized && container.innerHTML.trim()) {
                await previewAuction(type);
                updateAuthUI();
                return;
            }

            container.innerHTML = `
                <div class="auction-layout grid grid-cols-1 xl:grid-cols-2 gap-2 items-start">
                <div class="auction-matrix cyber-card p-4 space-y-3">
                    <h2 class="text-lg font-bold text-cyan-400">🦅 ${type} AUCTION MATRIX CONFIGURATOR</h2>
                    <div class="grid grid-cols-1 md:grid-cols-3 gap-2">
                        <div><label class="block text-xs uppercase mb-1">Puppet (Count)</label><input type="number" id="${type}-puppet" value="10" min="1" max="80" class="cyber-input w-full p-1.5 rounded text-sm" onchange="previewAuction('${type}')"></div>
                        <div><label class="block text-xs uppercase mb-1">LND Pool</label><input type="number" id="${type}-lnd" value="0" min="0" class="cyber-input w-full p-1.5 rounded text-sm" onchange="recalcAuction('${type}')"></div>
                        <div><label class="block text-xs uppercase mb-1">TNS Pool</label><input type="number" id="${type}-tns" value="0" min="0" class="cyber-input w-full p-1.5 rounded text-sm" onchange="recalcAuction('${type}')"></div>
                    </div>
                    <button onclick="previewAuction('${type}')" class="cyber-btn w-full py-1.5 text-sm">⚡ FETCH & PREVIEW ${type} BOARD</button>
                    <div id="${type}-result" class="text-cyan-400 font-mono text-sm"></div>
                </div>
                <div class="leftover-panel cyber-card p-3 space-y-2">
                    <div class="flex items-center justify-between gap-2">
                        <h3 class="text-sm font-bold text-amber-400">🛡️ LEFTOVER CONTRIBUTION — SELECT RECIPIENTS ONLY</h3>
                        <span class="text-[10px] text-gray-500">MAX 10 RECIPIENTS</span>
                    </div>
                    <div class="flex flex-col sm:flex-row gap-2">
                        <select id="${type}-leftover-member-select" class="cyber-input flex-1 p-1.5 text-xs" onchange="addLeftoverMember('${type}', this.value); this.value='';">
                            <option value="">+ ADD LEFTOVER RECIPIENT</option>
                        </select>
                        <div id="${type}-leftover-selected" class="flex flex-wrap gap-1 items-center"></div>
                    </div>
                    <div id="${type}-leftover-preview" class="text-xs font-mono text-amber-300"></div>
                    <button onclick="commitAuction('${type}')" class="cyber-btn w-full py-1.5 text-sm">🔒 COMMIT ${type} CYCLE & ROTATE QUEUE</button>
                </div>
                <div class="auction-monitor cyber-card p-4 space-y-3 auth-restricted xl:col-span-2">
                    <div class="flex items-center justify-between gap-2">
                        <h3 class="text-sm font-bold text-cyan-400">📋 PARTICIPATION MONITOR — CURRENT BIDDERS</h3>
                        <span id="${type}-split-summary" class="text-[10px] text-gray-500"></span>
                    </div>
                    <div class="auction-monitor-tables grid grid-cols-2 gap-2">
                        <div class="monitor-half overflow-x-auto">
                            <div class="text-[10px] text-cyan-400 font-bold uppercase mb-1">LEFT HALF</div>
                            <table class="w-full text-left border-collapse text-sm" id="${type}-preview-table-left"></table>
                        </div>
                        <div class="monitor-half overflow-x-auto">
                            <div class="text-[10px] text-cyan-400 font-bold uppercase mb-1">RIGHT HALF</div>
                            <table class="w-full text-left border-collapse text-sm" id="${type}-preview-table-right"></table>
                        </div>
                    </div>
                </div></div>`;
            // Restore the in-progress auction from the server/database.
            // Never reset an unfinished auction merely because the browser was reopened.
            try {
                const draftRes = await fetch(`/api/auction/draft?type=${type}`, {cache:'no-store'});
                const draftData = draftRes.ok ? await draftRes.json() : {state:null};
                const saved = draftData.state;
                if (saved) {
                    document.getElementById(`${type}-puppet`).value = saved.puppet ?? 10;
                    document.getElementById(`${type}-lnd`).value = saved.lnd ?? 0;
                    document.getElementById(`${type}-tns`).value = saved.tns ?? 0;
                    auctionSessions[type].skippedIds = new Set((saved.skippedIds || []).map(Number));
                    auctionSessions[type].statuses = saved.statuses || {};
                    auctionSessions[type].proof = saved.proof || {};
                    auctionSessions[type].allocations = saved.allocations || {};
                    auctionSessions[type].allocationManual = saved.allocationManual || {};
                    auctionSessions[type].leftoverSelectedIds = (saved.leftoverSelectedIds || []).map(Number);
                    auctionSessions[type].statusesInitialized = true;
                } else {
                    auctionSessions[type].skippedIds = new Set();
                    auctionSessions[type].statuses = {};
                    auctionSessions[type].proof = {};
                    auctionSessions[type].allocations = {};
                    auctionSessions[type].allocationManual = {};
                    auctionSessions[type].leftoverSelectedIds = [];
                    auctionSessions[type].statusesInitialized = false;
                }
            } catch (e) {
                console.warn('Auction draft restore failed', e);
            }
            await loadLeftoverMembers(type);
            auctionSessions[type].tabInitialized = true;
            await previewAuction(type);
            updateAuthUI();
        }

        async function loadLeftoverMembers(type) {
            const res = await fetch('/api/members');
            const members = await res.json();
            auctionSessions[type].leftoverMembers = members;
            auctionSessions[type].leftoverSelectedIds = auctionSessions[type].leftoverSelectedIds || [];
            renderLeftoverMemberPicker(type);
            recalcLeftoverMembers(type);
        }
        function getSelectedLeftoverMembers(type) {
            return auctionSessions[type].leftoverSelectedIds || [];
        }
        function renderLeftoverMemberPicker(type) {
            const session = auctionSessions[type];
            session.leftoverSelectedIds = session.leftoverSelectedIds || [];
            const select = document.getElementById(`${type}-leftover-member-select`);
            const selectedBox = document.getElementById(`${type}-leftover-selected`);
            if (!select || !selectedBox) return;
            const selectedSet = new Set(session.leftoverSelectedIds.map(Number));
            select.innerHTML = `<option value="">+ ADD LEFTOVER RECIPIENT</option>` +
                (session.leftoverMembers || []).filter(m => !selectedSet.has(Number(m.id))).map(m =>
                    `<option value="${m.id}">${m.name} — ${m.class_name}</option>`
                ).join('');
            selectedBox.innerHTML = session.leftoverSelectedIds.map(id => {
                const m = (session.leftoverMembers || []).find(x => Number(x.id) === Number(id));
                if (!m) return '';
                const puppet = parseInt(document.getElementById(`${type}-puppet`).value) || 0;
                const lndTotal = parseInt(document.getElementById(`${type}-lnd`).value) || 0;
                const tnsTotal = parseInt(document.getElementById(`${type}-tns`).value) || 0;
                const all = session.allMembers || [];
                const statuses = session.statuses || {};
                const allocations = session.allocations || {};
                const doneRows = all.filter(x => statuses[x.id] === 'Done');
                const lndUsed = doneRows.reduce((n,x) => n + (parseInt(allocations[x.id]?.lnd) || 0), 0);
                const tnsUsed = doneRows.reduce((n,x) => n + (parseInt(allocations[x.id]?.tns) || 0), 0);
                const lndLeft = Math.max(0, lndTotal - lndUsed);
                const tnsLeft = Math.max(0, tnsTotal - tnsUsed);
                const n = session.leftoverSelectedIds.length;
                const index = session.leftoverSelectedIds.findIndex(x => Number(x) === Number(id));
                // Reward as evenly as possible: every selected member gets the
                // same base amount. Any indivisible remainder stays in the
                // leftover pool instead of being given to one member.
                const lndReward = Math.floor(lndLeft / n);
                const tnsReward = Math.floor(tnsLeft / n);
                return `<details class="queue-item px-2 py-1 text-[10px] min-w-[170px]">
                    <summary class="cursor-pointer flex items-center justify-between gap-2 font-semibold">
                        <span>${m.name}</span><span class="text-cyan-400">REWARD ▾</span>
                    </summary>
                    <div class="mt-1 pt-1 border-t border-gray-800 text-amber-300 font-mono">LND: ${lndReward} &nbsp;|&nbsp; TNS: ${tnsReward}</div>
                    <button type="button" onclick="removeLeftoverMember('${type}', ${m.id})" class="mt-1 text-red-400">REMOVE</button>
                </details>`;
            }).join('');
        }
        function addLeftoverMember(type, value) {
            const id = parseInt(value);
            if (!id) return;
            const session = auctionSessions[type];
            session.leftoverSelectedIds = session.leftoverSelectedIds || [];
            if (session.leftoverSelectedIds.length >= 10) { alert('Maximum 10 members can receive leftovers at a time.'); return; }
            if (!session.leftoverSelectedIds.includes(id)) session.leftoverSelectedIds.push(id);
            renderLeftoverMemberPicker(type);
            recalcLeftoverMembers(type);
            saveAuctionDraft(type);
        }
        function removeLeftoverMember(type, id) {
            const session = auctionSessions[type];
            session.leftoverSelectedIds = (session.leftoverSelectedIds || []).filter(x => Number(x) !== Number(id));
            renderLeftoverMemberPicker(type);
            recalcLeftoverMembers(type);
            saveAuctionDraft(type);
        }
        function recalcLeftoverMembers(type) {
            const session = auctionSessions[type];
            const puppet = parseInt(document.getElementById(`${type}-puppet`).value) || 0;
            const lndTotal = parseInt(document.getElementById(`${type}-lnd`).value) || 0;
            const tnsTotal = parseInt(document.getElementById(`${type}-tns`).value) || 0;
            const all = session.allMembers || [];
            const statuses = session.statuses || {};
            const allocations = session.allocations || {};
            const doneRows = all.filter(m => statuses[m.id] === 'Done');
            const lndUsed = doneRows.reduce((n,m) => n + (parseInt(allocations[m.id]?.lnd) || 0), 0);
            const tnsUsed = doneRows.reduce((n,m) => n + (parseInt(allocations[m.id]?.tns) || 0), 0);
            const lndLeft = Math.max(0, lndTotal - lndUsed);
            const tnsLeft = Math.max(0, tnsTotal - tnsUsed);
            // IMPORTANT: leftover distribution is completely separate from auction participants.
            // ONLY members explicitly added in LEFTOVER CONTRIBUTION receive lndLeft/tnsLeft.
            const selected = getSelectedLeftoverMembers(type);
            const n = selected.length;
            let html = `Done participants: ${doneRows.length}/${puppet} | Participant LND ${lndUsed}/${lndTotal} | Participant TNS ${tnsUsed}/${tnsTotal}<br><b>LEFTOVER POOL: LND ${lndLeft} | TNS ${tnsLeft}</b>`;
            if (n) {
                const names = selected.map(id => (session.leftoverMembers || []).find(m => Number(m.id) === Number(id))?.name || `Member #${id}`);
                const lndEachLeftover = Math.floor(lndLeft / n);
                const tnsEachLeftover = Math.floor(tnsLeft / n);
                const lndRemainder = lndLeft % n;
                const tnsRemainder = tnsLeft % n;
                html += '<br><span class="text-gray-400">LEFTOVER RECIPIENTS (selected only):</span> ' + names.map((name) => {
                    return `${name} (${lndEachLeftover} LND / ${tnsEachLeftover} TNS)`;
                }).join(' • ');
                html += `<br><span class="text-amber-300">AFTER DISTRIBUTION: LND ${lndRemainder} | TNS ${tnsRemainder}</span>`;
            } else if (lndLeft || tnsLeft) {
                html += '<br><span class="text-red-400">Add member(s) to LEFTOVER CONTRIBUTION before committing.</span>';
            }
            const box = document.getElementById(`${type}-leftover-preview`);
            if (box) box.innerHTML = html;
        }

        async function previewAuction(type) {
            const puppet = parseInt(document.getElementById(`${type}-puppet`).value) || 10;
            const res = await fetch(`/api/auction/preview?type=${type}`, {cache:'no-store'});
            const data = await res.json();
            
            auctionSessions[type].allMembers = data.members;
            renderAuctionTable(type, puppet);
            recalcLeftoverMembers(type);
        }

        // Pull the current auction draft from the server and redraw only the
        // auction UI. We intentionally do not rebuild the tab DOM, because that
        // would reset controls and in-progress selections.
        async function refreshAuctionFromServer(type) {
            const session = auctionSessions[type];
            const puppetEl = document.getElementById(`${type}-puppet`);
            if (!session.tabInitialized || !puppetEl) return;
            try {
                const res = await fetch(`/api/auction/draft?type=${type}`, {cache:'no-store'});
                if (!res.ok) return;
                const data = await res.json();
                const saved = data.state;
                if (saved) {
                    puppetEl.value = saved.puppet ?? 10;
                    document.getElementById(`${type}-lnd`).value = saved.lnd ?? 0;
                    document.getElementById(`${type}-tns`).value = saved.tns ?? 0;
                    session.skippedIds = new Set((saved.skippedIds || []).map(Number));
                    session.statuses = saved.statuses || {};
                    session.proof = saved.proof || {};
                    session.allocations = saved.allocations || {};
                    session.allocationManual = saved.allocationManual || {};
                    session.leftoverSelectedIds = (saved.leftoverSelectedIds || []).map(Number);
                    session.statusesInitialized = true;
                } else {
                    session.skippedIds = new Set();
                    session.statuses = {};
                    session.proof = {};
                    session.allocations = {};
                    session.allocationManual = {};
                    session.leftoverSelectedIds = [];
                    session.statusesInitialized = false;
                }
                await loadLeftoverMembers(type);
                await previewAuction(type);
            } catch (e) {
                console.warn(`Realtime ${type} auction refresh failed`, e);
            }
        }

        function renderAuctionTable(type, puppet) {
            const session = auctionSessions[type];
            const all = session.allMembers || [];
            const statuses = session.statuses || (session.statuses = {});
            const allocations = session.allocations || (session.allocations = {});

            // Keep DONE members in the current bidding group. DONE means they won
            // the current auction; it must NOT behave like SKIP or remove them.
            // When someone is SKIPped, only then is their slot replaced by the next member.
            const currentRows = [];
            const doneRowsForGroup = all.filter(member =>
                !session.skippedIds.has(member.id) && statuses[member.id] === 'Done'
            ).slice(0, puppet);

            doneRowsForGroup.forEach(member => currentRows.push(member));

            // Fill the remaining slots with members who are not skipped and not Done.
            for (const member of all) {
                if (currentRows.length >= puppet) break;
                if (session.skippedIds.has(member.id)) continue;
                if (statuses[member.id] === 'Done') continue;
                currentRows.push(member);
            }

            currentRows.sort((a,b) => a.queue_pos - b.queue_pos);

            // Compact the visible queue numbers whenever a member is skipped.
            // Database queue positions are updated only when the cycle is committed.
            currentRows.forEach((member, index) => { member.auction_queue_pos = index + 1; });

            const lndTotal = parseInt(document.getElementById(`${type}-lnd`).value) || 0;
            const tnsTotal = parseInt(document.getElementById(`${type}-tns`).value) || 0;
            // Automatically distribute the configured LND/TNS pool to ALL current bidders.
            // Manual edits are preserved until the user changes the pool or edits that field.
            const lndEach = puppet > 0 ? Math.floor(lndTotal / puppet) : 0;
            const tnsEach = puppet > 0 ? Math.floor(tnsTotal / puppet) : 0;
            session.allocationManual = session.allocationManual || {};
            currentRows.forEach(m => {
                if (!allocations[m.id]) allocations[m.id] = {lnd:lndEach, tns:tnsEach};
                if (!session.allocationManual[m.id]?.lnd) allocations[m.id].lnd = lndEach;
                if (!session.allocationManual[m.id]?.tns) allocations[m.id].tns = tnsEach;
            });
            const doneRows = currentRows.filter(m => statuses[m.id] === 'Done');

            document.getElementById(`${type}-result`).innerHTML = `<b>${type} PARTICIPATION MONITOR</b><br>Current bidders: ${currentRows.length} / ${puppet} | Done: ${doneRows.length} | Waiting: ${currentRows.filter(m => (statuses[m.id]||'Waiting')==='Waiting').length}`;
            const leftTable = document.getElementById(`${type}-preview-table-left`);
            const rightTable = document.getElementById(`${type}-preview-table-right`);
            const splitPoint = Math.ceil(currentRows.length / 2);
            const leftRows = currentRows.slice(0, splitPoint);
            const rightRows = currentRows.slice(splitPoint);
            const summary = document.getElementById(`${type}-split-summary`);
            if (summary) summary.innerText = `${leftRows.length} LEFT / ${rightRows.length} RIGHT`;

            const renderMonitorTable = rows => {
                let html = `<thead><tr class="border-b border-gray-800 text-cyan-400 text-xs uppercase"><th class="p-2">Queue</th><th class="p-2">Member</th><th class="p-2">Class</th><th class="p-2">Status</th><th class="p-2">LND</th><th class="p-2">TNS</th></tr></thead><tbody>`;
                rows.forEach(r => {
                    const status = statuses[r.id] || 'Waiting';
                    const a = allocations[r.id] || {lnd:0, tns:0};
                    const disabled = '';
                    html += `<tr class="border-b border-gray-950 ${status === 'Done' ? 'bg-cyan-950/20' : ''}">
                        <td class="p-2 text-cyan-400 font-bold">${r.auction_queue_pos ?? r.queue_pos}</td><td class="p-2 font-semibold">${r.name}</td><td class="p-2 text-cyan-200 text-xs">${r.class_name}</td>
                        <td class="p-2"><div class="flex gap-1 flex-wrap">
                            <button onclick="setAuctionStatus('${type}', ${r.id}, 'Done')" class="px-2 py-1 rounded text-xs border ${status==='Done'?'bg-cyan-500 text-black':'border-cyan-500 text-cyan-400'}">Done</button>
                            <button onclick="setAuctionStatus('${type}', ${r.id}, 'Waiting')" class="px-2 py-1 rounded text-xs border ${status==='Waiting'?'bg-yellow-500 text-black':'border-yellow-500 text-yellow-400'}">Waiting</button>
                            <button onclick="setAuctionStatus('${type}', ${r.id}, 'Skip')" class="px-2 py-1 rounded text-xs border border-red-500 text-red-400">Skip</button>
                        </div></td>
                        <td class="p-2"><input type="number" min="0" value="${a.lnd??0}" ${disabled} onchange="setAuctionAllocation('${type}',${r.id},'lnd',this.value)" class="cyber-input w-20 p-1 rounded text-xs"></td>
                        <td class="p-2"><input type="number" min="0" value="${a.tns??0}" ${disabled} onchange="setAuctionAllocation('${type}',${r.id},'tns',this.value)" class="cyber-input w-20 p-1 rounded text-xs"></td>
                    </tr>`;
                });
                return html + '</tbody>';
            };
            if (leftTable) leftTable.innerHTML = renderMonitorTable(leftRows);
            if (rightTable) rightTable.innerHTML = renderMonitorTable(rightRows);
        }

        let auctionSaveTimers = {};
        function buildAuctionDraft(type) {
            const session = auctionSessions[type];
            const puppetEl = document.getElementById(`${type}-puppet`);
            const lndEl = document.getElementById(`${type}-lnd`);
            const tnsEl = document.getElementById(`${type}-tns`);
            if (!puppetEl || !lndEl || !tnsEl) return null;
            return {
                puppet: parseInt(puppetEl.value) || 10,
                lnd: parseInt(lndEl.value) || 0,
                tns: parseInt(tnsEl.value) || 0,
                skippedIds: Array.from(session.skippedIds || []),
                statuses: session.statuses || {},
                proof: session.proof || {},
                allocations: session.allocations || {},
                allocationManual: session.allocationManual || {},
                leftoverSelectedIds: getSelectedLeftoverMembers(type)
            };
        }
        function saveAuctionDraft(type, immediate=false) {
            const state = buildAuctionDraft(type);
            if (!state) return;
            const send = () => fetch('/api/auction/draft', {
                method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({auction_type:type,state}), keepalive:true
            }).catch(e => console.warn('Auction draft save failed', e));
            if (immediate) { clearTimeout(auctionSaveTimers[type]); send(); }
            else { clearTimeout(auctionSaveTimers[type]); auctionSaveTimers[type]=setTimeout(send, 100); }
        }
        window.addEventListener('pagehide', () => { saveAuctionDraft('GL', true); saveAuctionDraft('EO', true); });
        window.addEventListener('beforeunload', () => { saveAuctionDraft('GL', true); saveAuctionDraft('EO', true); });

        function setAuctionStatus(type, memberId, status) {
            const session = auctionSessions[type];
            session.statuses[memberId] = status;
            if (status === 'Skip') { session.skippedIds.add(memberId); session.allocations[memberId] = {lnd:0, tns:0}; }
            else if (status === 'Waiting') { session.skippedIds.delete(memberId); session.allocations[memberId] = session.allocations[memberId] || {lnd:0, tns:0}; }
            else { session.skippedIds.delete(memberId); session.allocations[memberId] = session.allocations[memberId] || {lnd:0, tns:0}; }
            renderAuctionTable(type, parseInt(document.getElementById(`${type}-puppet`).value) || 10);
            recalcLeftoverMembers(type);
            saveAuctionDraft(type);
        }

        function setAuctionProof(type, memberId, checked) {
            const session = auctionSessions[type];
            session.proof = session.proof || {};
            session.proof[memberId] = !!checked;
            saveAuctionDraft(type);
        }

        function setAuctionAllocation(type, memberId, item, value) {
            const session = auctionSessions[type];
            session.allocations[memberId] = session.allocations[memberId] || {lnd: 0, tns: 0};
            session.allocations[memberId][item] = Math.max(0, parseInt(value) || 0);
            session.allocationManual = session.allocationManual || {};
            session.allocationManual[memberId] = session.allocationManual[memberId] || {};
            session.allocationManual[memberId][item] = true;
            recalcLeftoverMembers(type);
            saveAuctionDraft(type);
        }

        function recalcAuction(type) {
            const puppet = parseInt(document.getElementById(`${type}-puppet`).value) || 10;
            renderAuctionTable(type, puppet);
            recalcLeftoverMembers(type);
            saveAuctionDraft(type);
        }

        async function commitAuction(type) {
            const puppet = parseInt(document.getElementById(`${type}-puppet`).value) || 10;
            const session = auctionSessions[type];
            const all = session.allMembers || [];
            const statuses = session.statuses || {};
            const allocations = session.allocations || {};
            const doneRows = all.filter(m => statuses[m.id] === 'Done');
            const skippedRows = all.filter(m => statuses[m.id] === 'Skip');

            if (doneRows.length !== puppet) {
                alert(`You need exactly ${puppet} members marked Done. Currently: ${doneRows.length}.`);
                return;
            }
            const lndTotal = parseInt(document.getElementById(`${type}-lnd`).value) || 0;
            const tnsTotal = parseInt(document.getElementById(`${type}-tns`).value) || 0;
            const awards = doneRows.map(m => ({
                member_id: m.id,
                lnd_awarded: Math.max(0, parseInt((allocations[m.id] || {}).lnd) || 0),
                tns_awarded: Math.max(0, parseInt((allocations[m.id] || {}).tns) || 0)
            }));
            const payload = {
                auction_type: type, puppet_count: puppet, lnd_total: lndTotal, tns_total: tnsTotal,
                participant_ids: doneRows.map(r => r.id), skipped_ids: skippedRows.map(r => r.id),
                proof: Object.fromEntries(doneRows.map(r => [r.id, !!session.proof?.[r.id]])), awards,
                recipient_member_ids: getSelectedLeftoverMembers(type)
            };
            const res = await fetch('/api/auction/commit', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
            if(res.ok) {
                // Commit succeeded: the backend has rotated the queue.
                // Clear only this auction's in-browser cycle state and reload the
                // same auction screen so the next cycle is immediately visible.
                await fetch(`/api/auction/draft/${type}`, {method:'DELETE'});
                session.skippedIds = new Set();
                session.statuses = {};
                session.proof = {};
                session.allocations = {};
                session.allocationManual = {};
                session.leftoverSelectedIds = [];
                session.statusesInitialized = false;
                await loadLeftoverMembers(type);
                await previewAuction(type);
                alert(`${type} Auction cycle committed successfully. Next cycle is now loaded.`);
            }
            else { const err = await res.json(); alert(err.detail || 'Commit failed.'); }
        }

        async function loadHistory() {
            const res = await fetch('/api/history');
            const data = await res.json();
            const isAdmin = currentUser.role === 'Admin';
            const tbody = document.getElementById('history-table-body');
            tbody.innerHTML = data.map(c => `
                <tr class="hover:bg-gray-900">
                    <td class="p-2 text-cyan-400 font-bold">${c.id}</td>
                    <td class="p-2">${c.auction_type}</td>
                    <td class="p-2">${c.puppet_count}</td>
                    <td class="p-2">${c.lnd_total}</td>
                    <td class="p-2">${c.tns_total}</td>
                    <td class="p-2">${c.participant_count}</td>
                    <td class="p-2 text-gray-500">${c.created_at}</td>
                    <td class="p-2 flex gap-1">
                        <button onclick="loadCycleDetail(${c.id})" class="text-cyan-400 border border-cyan-500 px-2 py-0.5 rounded text-xs hover:bg-cyan-500 hover:text-black">VIEW BIDDERS</button>
                        ${isAdmin ? `<button onclick="deleteCycle(${c.id})" class="text-red-400 border border-red-500 px-2 py-0.5 rounded text-xs hover:bg-red-500 hover:text-black">DELETE</button>` : ''}
                    </td>
                </tr>
            `).join('');
        }

        async function loadCycleDetail(id) {
            activeArchiveCycleId = id;
            const res = await fetch(`/api/history/${id}`);
            const data = await res.json();
            const officerRes = await fetch(`/api/history/${id}/officers`);
            const officerData = await officerRes.json();
            const leftoverRes = await fetch(`/api/history/${id}/leftover-members`);
            const leftoverData = await leftoverRes.json();
            document.getElementById('archive-detail-title').innerText = `Archive #${id} — Participating Players & Bids`;
            document.getElementById('history-detail-container').classList.remove('hidden');
            
            const isAdminOrOfficer = currentUser.role === 'Admin' || currentUser.role === 'Officer';
            const tbody = document.getElementById('archive-members-body');
            tbody.innerHTML = data.map(m => `
                <tr class="border-b border-gray-900 ${m.participated ? 'bg-emerald-950/25' : 'bg-red-950/20'}" data-cm-id="${m.id}">
                    <td class="p-2 font-semibold ${m.participated ? 'text-emerald-300' : 'text-red-300'}">${m.member_name}</td>
                    <td class="p-2 text-cyan-400">${m.queue_position_before}</td>
                    <td class="p-2"><span class="px-2 py-0.5 rounded text-xs font-bold ${m.participated ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/50' : 'bg-red-500/20 text-red-300 border border-red-500/50'}">${m.participated ? 'YES' : 'NO'}</span></td>
                    <td class="p-2"><input type="checkbox" ${m.proof_checked ? 'checked' : ''} onchange="updateArchiveProof(${m.id}, this.checked)" class="w-4 h-4 accent-cyan-400" ${!isAdminOrOfficer ? 'disabled' : ''} title="Proof verified during bidding"></td>
                    <td class="p-2"><input type="number" value="${m.lnd_awarded}" class="cyber-input w-20 p-1 rounded text-xs archive-lnd" ${!isAdminOrOfficer ? 'disabled' : ''}></td>
                    <td class="p-2"><input type="number" value="${m.tns_awarded}" class="cyber-input w-20 p-1 rounded text-xs archive-tns" ${!isAdminOrOfficer ? 'disabled' : ''}></td>
                    <td class="p-2 auth-restricted-col" style="display: ${isAdminOrOfficer ? 'table-cell' : 'none'};">
                        <button onclick="updateCycleMember(${m.id})" class="text-cyan-400 border border-cyan-500 px-2 py-0.5 rounded text-xs hover:bg-cyan-500 hover:text-black">SAVE</button>
                    </td>
                </tr>
            `).join('');
            const leftoverBody = document.getElementById('archive-leftover-members-body');
            leftoverBody.innerHTML = leftoverData.map(m => `
                <tr><td class="p-2 font-semibold">${m.member_name}</td><td class="p-2">${m.lnd_awarded}</td><td class="p-2">${m.tns_awarded}</td></tr>
            `).join('') || '<tr><td colspan="3" class="p-2 text-gray-500">No member leftovers recorded.</td></tr>';
            const officerBody = document.getElementById('archive-officers-body');
            officerBody.innerHTML = officerData.map(o => `
                <tr><td class="p-2 font-semibold">${o.officer_username}</td><td class="p-2">${o.lnd_awarded}</td><td class="p-2">${o.tns_awarded}</td></tr>
            `).join('') || '<tr><td colspan="3" class="p-2 text-gray-500">No officer leftovers recorded.</td></tr>';
        }

        async function updateArchiveProof(cmId, checked) {
            const res = await fetch(`/api/history/member/${cmId}`, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({proof_checked: !!checked})
            });
            if (!res.ok) alert('Failed to update proof status.');
        }

        async function updateCycleMember(cmId) {
            const row = document.querySelector(`tr[data-cm-id="${cmId}"]`);
            const lnd = parseInt(row.querySelector('.archive-lnd').value) || 0;
            const tns = parseInt(row.querySelector('.archive-tns').value) || 0;

            const res = await fetch(`/api/history/member/${cmId}`, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ lnd_awarded: lnd, tns_awarded: tns })
            });
            if(res.ok) alert("Bid / Award updated successfully.");
        }

        async function deleteCycle(id) {
            if(currentUser.role !== 'Admin') {
                alert("Only Admin can delete archive cycles.");
                return;
            }
            if(confirm(`Are you sure you want to delete archive cycle #${id}?`)) {
                const res = await fetch(`/api/history/${id}`, { method: 'DELETE' });
                if(res.ok) document.getElementById('history-detail-container').classList.add('hidden');
            }
        }

        async function loadAdminUsers() {
            if(currentUser.role !== 'Admin') return;
            const res = await fetch('/api/admin/users');
            const data = await res.json();
            const tbody = document.getElementById('admin-users-body');
            tbody.innerHTML = data.map(u => `
                <tr class="hover:bg-gray-900">
                    <td class="p-3 font-semibold text-cyan-400">${u.username}</td>
                    <td class="p-3">${u.email}</td>
                    <td class="p-3">
                        <select onchange="changeUserRole(${u.id}, this.value)" class="cyber-input p-1 rounded text-xs">
                            <option value="Pending" ${u.role === 'Pending' ? 'selected' : ''}>Pending</option>
                            <option value="Viewer" ${u.role === 'Viewer' ? 'selected' : ''}>Viewer</option>
                            <option value="Officer" ${u.role === 'Officer' ? 'selected' : ''}>Officer</option>
                            <option value="Admin" ${u.role === 'Admin' ? 'selected' : ''}>Admin</option>
                        </select>
                    </td>
                    <td class="p-3 text-xs text-gray-500">${u.created_at}</td>
                    <td class="p-3"><button onclick="deleteUser(${u.id})" class="text-red-400 border border-red-500 px-2 py-1 rounded text-xs hover:bg-red-500 hover:text-black">REMOVE</button></td>
                </tr>
            `).join('');
        }

        async function changeUserRole(userId, newRole) {
            await fetch(`/api/admin/users/${userId}/role`, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ role: newRole })
            });
        }

        async function deleteUser(userId) {
            if(confirm("Delete this user account?")) {
                await fetch(`/api/admin/users/${userId}`, { method: 'DELETE' });
            }
        }

        window.onload = loadAppData;
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def serve_frontend():
    return HTML_TEMPLATE

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

@app.get("/api/data")
def get_app_data():
    members = db.fetchall("SELECT * FROM members ORDER BY gl_queue_position, id")
    teams = db.fetchall("SELECT * FROM league_teams ORDER BY team_number ASC")
    roles_pool = {}
    for role in ROLE_CLASSES.keys():
        roles_pool[role] = db.fetchall("SELECT id, name, class_name FROM members WHERE role = ? ORDER BY name", (role,))
    
    jakarta_time = datetime.now(JAKARTA_TZ).strftime("%Y-%m-%d %H:%M:%S")
    return {"members": members, "teams": teams, "roles_pool": roles_pool, "server_time": jakarta_time}

@app.post("/api/register")
async def register_user(data: dict):
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    email = data.get("email", "").strip()
    
    if not username or not password or not email:
        raise HTTPException(status_code=400, detail="Username, password, and email are required.")
    
    existing = db.fetchone("SELECT id FROM users WHERE username = ?", (username,))
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists.")
        
    now_str = datetime.now(JAKARTA_TZ).strftime("%Y-%m-%d %H:%M")
    db.execute(
        "INSERT INTO users (username, password, role, email, created_at) VALUES (?, ?, 'Pending', ?, ?)",
        (username, password, email, now_str)
    )
    
    print(f"[EMAIL NOTIFICATION] New registration request for user '{username}' ({email}). Sent to drethan.game@gmail.com")
    await manager.broadcast("refresh")
    return {"status": "success"}

@app.post("/api/login")
def login_user(data: dict):
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    
    user = db.fetchone("SELECT * FROM users WHERE username = ? AND password = ?", (username, password))
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password.")
        
    return {"id": user["id"], "username": user["username"], "role": user["role"], "email": user["email"]}

@app.post("/api/members")
async def add_member(data: dict):
    name = data.get("name", "").strip()
    role = data.get("role", "").strip()
    class_name = data.get("class_name", "").strip()

    if not name:
        raise HTTPException(status_code=400, detail="Character name is required.")
    if role not in ROLE_CLASSES:
        raise HTTPException(status_code=400, detail="Invalid role selected.")
    if class_name not in ROLE_CLASSES[role]:
        raise HTTPException(status_code=400, detail=f"Class '{class_name}' is not allowed for role '{role}'.")
    
    count = db.fetchone("SELECT COUNT(*) AS c FROM members")["c"]
    if count >= MAX_MEMBERS:
        raise HTTPException(status_code=400, detail=f"Roster limit of {MAX_MEMBERS} members reached.")

    existing_name = db.fetchone("SELECT id FROM members WHERE name = ?", (name,))
    if existing_name:
        raise HTTPException(status_code=400, detail=f"Member name '{name}' already exists.")

    gl_pos = db.fetchone("SELECT COALESCE(MAX(gl_queue_position), 0) AS p FROM members")["p"] + 1
    eo_pos = db.fetchone("SELECT COALESCE(MAX(eo_queue_position), 0) AS p FROM members")["p"] + 1
    now_str = datetime.now(JAKARTA_TZ).strftime("%Y-%m-%d %H:%M")

    try:
        db.execute(
            "INSERT INTO members (name, role, class_name, gl_queue_position, eo_queue_position, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (name, role, class_name, gl_pos, eo_pos, now_str)
        )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail=f"Member name '{name}' already exists.")

    await manager.broadcast("refresh")
    return {"status": "success"}

@app.post("/api/members/batch")
async def add_batch_members(data: dict):
    names = data.get("names", [])
    role = data.get("role", "Main DPS")
    class_name = data.get("class_name", "Lord Knight")

    if not names:
        raise HTTPException(status_code=400, detail="No names provided for batch add.")
    if role not in ROLE_CLASSES:
        raise HTTPException(status_code=400, detail="Invalid role selected.")
    if class_name not in ROLE_CLASSES[role]:
        raise HTTPException(status_code=400, detail=f"Class '{class_name}' is not allowed for role '{role}'.")

    current_count = db.fetchone("SELECT COUNT(*) AS c FROM members")["c"]
    available_slots = MAX_MEMBERS - current_count
    if available_slots <= 0:
        raise HTTPException(status_code=400, detail=f"Roster limit of {MAX_MEMBERS} members already reached.")

    added_count = 0
    now_str = datetime.now(JAKARTA_TZ).strftime("%Y-%m-%d %H:%M")

    for raw_name in names:
        name = str(raw_name).strip()
        if not name:
            continue
        if added_count >= available_slots:
            break
        
        existing = db.fetchone("SELECT id FROM members WHERE name = ?", (name,))
        if existing:
            continue

        gl_pos = db.fetchone("SELECT COALESCE(MAX(gl_queue_position), 0) AS p FROM members")["p"] + 1
        eo_pos = db.fetchone("SELECT COALESCE(MAX(eo_queue_position), 0) AS p FROM members")["p"] + 1

        try:
            db.execute(
                "INSERT INTO members (name, role, class_name, gl_queue_position, eo_queue_position, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (name, role, class_name, gl_pos, eo_pos, now_str)
            )
            added_count += 1
        except sqlite3.IntegrityError:
            continue

    await manager.broadcast("refresh")
    return {"status": "success", "added_count": added_count}

@app.put("/api/members/{member_id}")
async def edit_member(member_id: int, data: dict):
    name = data.get("name", "").strip()
    role = data.get("role", "").strip()
    class_name = data.get("class_name", "").strip()

    if not name:
        raise HTTPException(status_code=400, detail="Character name is required.")
    if role not in ROLE_CLASSES:
        raise HTTPException(status_code=400, detail="Invalid role selected.")
    if class_name not in ROLE_CLASSES[role]:
        raise HTTPException(status_code=400, detail=f"Class '{class_name}' is not allowed for role '{role}'.")

    dup = db.fetchone("SELECT id FROM members WHERE name = ? AND id != ?", (name, member_id))
    if dup:
        raise HTTPException(status_code=400, detail=f"Member name '{name}' already exists.")

    try:
        db.execute(
            "UPDATE members SET name = ?, role = ?, class_name = ? WHERE id = ?",
            (name, role, class_name, member_id)
        )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail=f"Member name '{name}' already exists.")

    await manager.broadcast("refresh")
    return {"status": "success"}

@app.delete("/api/members/{member_id}")
async def delete_member(member_id: int):
    for slot in ["slot_main_dps", "slot_sub_dps", "slot_utility", "slot_bard", "slot_fs"]:
        db.execute(f"UPDATE league_teams SET {slot} = NULL WHERE {slot} = ?", (member_id,))
    
    db.execute("DELETE FROM members WHERE id = ?", (member_id,))
    
    for queue_col in ["gl_queue_position", "eo_queue_position"]:
        remaining = db.fetchall(f"SELECT id FROM members ORDER BY {queue_col}, id")
        for pos, row in enumerate(remaining, start=1):
            db.execute(f"UPDATE members SET {queue_col} = ? WHERE id = ?", (pos, row["id"]))
            
    await manager.broadcast("refresh")
    return {"status": "success"}

@app.put("/api/members/{member_id}/queue")
async def update_member_queue(member_id: int, data: dict):
    queue_type = str(data.get("queue", "")).upper()
    position = int(data.get("position", 0) or 0)
    if queue_type not in ("GL", "EO"):
        raise HTTPException(status_code=400, detail="Queue must be GL or EO.")
    if position < 1:
        raise HTTPException(status_code=400, detail="Queue position must be 1 or higher.")
    queue_col = "gl_queue_position" if queue_type == "GL" else "eo_queue_position"
    member = db.fetchone("SELECT id FROM members WHERE id = ?", (member_id,))
    if not member:
        raise HTTPException(status_code=404, detail="Member not found.")
    max_pos = db.fetchone(f"SELECT COUNT(*) AS c FROM members")['c']
    position = min(position, max_pos)
    current = db.fetchone(f"SELECT {queue_col} AS p FROM members WHERE id = ?", (member_id,))["p"]
    if current != position:
        if position < current:
            db.execute(f"UPDATE members SET {queue_col} = {queue_col} + 1 WHERE {queue_col} >= ? AND {queue_col} < ? AND id != ?", (position, current, member_id))
        else:
            db.execute(f"UPDATE members SET {queue_col} = {queue_col} - 1 WHERE {queue_col} <= ? AND {queue_col} > ? AND id != ?", (position, current, member_id))
        db.execute(f"UPDATE members SET {queue_col} = ? WHERE id = ?", (position, member_id))
    await manager.broadcast("refresh")
    return {"status": "success", "queue": queue_type, "position": position}

@app.post("/api/teams")
async def save_teams(teams: List[dict]):
    for t in teams:
        db.execute("""
            UPDATE league_teams 
            SET slot_main_dps = ?, slot_sub_dps = ?, slot_utility = ?, slot_bard = ?, slot_fs = ?
            WHERE team_number = ?
        """, (t.get("slot_main_dps"), t.get("slot_sub_dps"), t.get("slot_utility"), t.get("slot_bard"), t.get("slot_fs"), t.get("team_number")))
    await manager.broadcast("refresh")
    return {"status": "success"}

@app.get("/api/officers")
def get_officers():
    return db.fetchall("SELECT id, username, email FROM users WHERE role = 'Officer' ORDER BY username")

@app.get("/api/members")
def get_members_for_leftovers():
    return db.fetchall("SELECT id, name, class_name FROM members ORDER BY gl_queue_position, id")

@app.get("/api/auction/draft")
def get_auction_draft(type: str):
    if type not in ("GL", "EO"):
        raise HTTPException(status_code=400, detail="Invalid auction type.")
    row = db.fetchone("SELECT state_json FROM auction_draft_state WHERE auction_type = ?", (type,))
    return {"state": json.loads(row["state_json"]) if row else None}

@app.put("/api/auction/draft")
async def save_auction_draft(data: dict):
    auction_type = data.get("auction_type")
    if auction_type not in ("GL", "EO"):
        raise HTTPException(status_code=400, detail="Invalid auction type.")
    state = data.get("state") or {}
    now_str = datetime.now(JAKARTA_TZ).strftime("%Y-%m-%d %H:%M:%S")
    db.execute("""INSERT INTO auction_draft_state (auction_type, state_json, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(auction_type) DO UPDATE SET state_json=excluded.state_json, updated_at=excluded.updated_at""",
              (auction_type, json.dumps(state), now_str))
    # Notify every connected browser that this specific auction changed.
    # The client refreshes the draft without rebuilding the auction tab, so
    # in-progress controls remain clickable and the latest state is visible.
    await manager.broadcast(f"auction_refresh:{auction_type}")
    return {"status":"saved"}

@app.delete("/api/auction/draft/{auction_type}")
async def delete_auction_draft(auction_type: str):
    if auction_type not in ("GL", "EO"):
        raise HTTPException(status_code=400, detail="Invalid auction type.")
    db.execute("DELETE FROM auction_draft_state WHERE auction_type = ?", (auction_type,))
    await manager.broadcast(f"auction_refresh:{auction_type}")
    return {"status":"cleared"}

@app.get("/api/auction/preview")
def preview_auction(type: str):
    queue_col = "gl_queue_position" if type == "GL" else "eo_queue_position"
    members = db.fetchall(f"SELECT id, name, class_name, {queue_col} as queue_pos FROM members ORDER BY {queue_col}, id")
    return {"members": members}

@app.post("/api/auction/commit")
async def commit_auction(data: dict):
    auction_type = data.get("auction_type")
    if auction_type not in ("GL", "EO"):
        raise HTTPException(status_code=400, detail="Invalid auction type.")

    queue_col = "gl_queue_position" if auction_type == "GL" else "eo_queue_position"
    puppet_count = int(data.get("puppet_count", 10) or 10)
    lnd_total = int(data.get("lnd_total", 0) or 0)
    tns_total = int(data.get("tns_total", 0) or 0)
    participant_ids = {int(x) for x in (data.get("participant_ids") or [])}
    skipped_ids = {int(x) for x in (data.get("skipped_ids") or [])}
    proof_by_member = {int(k): bool(v) for k, v in (data.get("proof") or {}).items()}
    awards_by_member = {int(a["member_id"]): (max(0, int(a.get("lnd_awarded", 0) or 0)), max(0, int(a.get("tns_awarded", 0) or 0))) for a in (data.get("awards") or []) if a.get("member_id") is not None}

    available = db.fetchone("SELECT COUNT(*) AS c FROM members")["c"]
    if available == 0:
        raise HTTPException(status_code=400, detail="No members available for auction.")

    target_puppet = min(puppet_count, available)
    if len(participant_ids) != target_puppet:
        raise HTTPException(
            status_code=400,
            detail=f"Bidding participants count ({len(participant_ids)}) must match the exact Puppet count ({target_puppet})."
        )
    if participant_ids & skipped_ids:
        raise HTTPException(status_code=400, detail="A skipped member cannot also be a participant.")

    current_queue = db.fetchall(f"SELECT * FROM members ORDER BY {queue_col}, id")
    member_ids = {m["id"] for m in current_queue}
    if not participant_ids.issubset(member_ids) or not skipped_ids.issubset(member_ids):
        raise HTTPException(status_code=400, detail="One or more auction members no longer exist.")

    lnd_each = lnd_total // target_puppet
    tns_each = tns_total // target_puppet
    # Custom amounts from Participation Monitor are allowed for members
    # who cannot afford the default LND/TNS amount. Unawarded units become leftovers.
    total_lnd_awarded = sum(awards_by_member.get(mid, (lnd_each, tns_each))[0] for mid in participant_ids)
    total_tns_awarded = sum(awards_by_member.get(mid, (lnd_each, tns_each))[1] for mid in participant_ids)
    if total_lnd_awarded > lnd_total or total_tns_awarded > tns_total:
        raise HTTPException(status_code=400, detail="Edited LND/TNS awards cannot exceed the configured auction pool.")
    lnd_left = lnd_total - total_lnd_awarded
    tns_left = tns_total - total_tns_awarded

    # LEFTOVER RULE: only explicitly selected leftover recipients may receive leftovers.
    # Auction participants are NOT recipients unless they were explicitly added here.
    recipient_ids = {int(x) for x in (data.get("recipient_member_ids") or [])}
    if len(recipient_ids) > 10:
        raise HTTPException(status_code=400, detail="A maximum of 10 members can receive leftovers at a time.")
    recipient_rows = []
    if recipient_ids:
        recipient_rows = db.fetchall("SELECT id, name FROM members WHERE id IN (%s) ORDER BY name" % ",".join("?" for _ in recipient_ids), tuple(recipient_ids))
        if len(recipient_rows) != len(recipient_ids):
            raise HTTPException(status_code=400, detail="One or more selected members no longer exist.")
    if (lnd_left or tns_left) and not recipient_rows:
        raise HTTPException(status_code=400, detail="Select at least one member to receive LND/TNS leftovers.")
    now_str = datetime.now(JAKARTA_TZ).strftime("%Y-%m-%d %H:%M")

    cycle = db.execute(
        """INSERT INTO auction_cycles (auction_type, puppet_count, lnd_total, tns_total, participant_count, lnd_each, lnd_leftover, tns_each, tns_leftover, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (auction_type, target_puppet, lnd_total, tns_total, target_puppet, lnd_each, lnd_left, tns_each, tns_left, now_str)
    )
    cycle_id = cycle.lastrowid

    # Distribute the leftover pool ONLY across the explicitly selected leftover recipients.
    # Reward everyone as evenly as possible. If the pool cannot be divided evenly,
    # the remainder stays as another leftover and is NOT assigned to anyone.
    lnd_remaining_after_distribution = lnd_left
    tns_remaining_after_distribution = tns_left
    if recipient_rows:
        n = len(recipient_rows)
        lnd_each_leftover = lnd_left // n
        tns_each_leftover = tns_left // n
        lnd_remaining_after_distribution = lnd_left % n
        tns_remaining_after_distribution = tns_left % n
        for member in recipient_rows:
            db.execute("INSERT INTO cycle_leftover_members (cycle_id, member_id, member_name, lnd_awarded, tns_awarded) VALUES (?, ?, ?, ?, ?)", (cycle_id, member["id"], member["name"], lnd_each_leftover, tns_each_leftover))

        # Keep the undistributed remainder in the cycle record as the new leftover.
        db.execute(
            "UPDATE auction_cycles SET lnd_leftover = ?, tns_leftover = ? WHERE id = ?",
            (lnd_remaining_after_distribution, tns_remaining_after_distribution, cycle_id)
        )

    # Keep the original queue order for every group.
    skipped_members = [m for m in current_queue if m["id"] in skipped_ids]
    participating_members = [m for m in current_queue if m["id"] in participant_ids]
    untouched_members = [m for m in current_queue if m["id"] not in skipped_ids and m["id"] not in participant_ids]

    # Record every member explicitly involved in this cycle.
    involved_members = sorted(
        skipped_members + participating_members,
        key=lambda m: (m[queue_col], m["id"])
    )
    for m in involved_members:
        is_part = m["id"] in participant_ids
        status = "Done" if is_part else ("Skip" if m["id"] in skipped_ids else "Waiting")
        lnd_award, tns_award = awards_by_member.get(m["id"], (lnd_each if is_part else 0, tns_each if is_part else 0))
        proof_checked = 1 if is_part and proof_by_member.get(m["id"], False) else 0
        db.execute(
            "INSERT INTO cycle_members (cycle_id, member_name, queue_position_before, participated, lnd_awarded, tns_awarded, status, proof_checked) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (cycle_id, m["name"], m[queue_col], 1 if is_part else 0, lnd_award, tns_award, status, proof_checked)
        )

    # IMPORTANT QUEUE RULE:
    # 1. Skipped members go FIRST so they have priority on the next bidding cycle.
    # 2. Members who were not involved keep their relative order.
    # 3. Participants go to the back after their turn.
    new_queue = skipped_members + untouched_members + participating_members
    for pos, member in enumerate(new_queue, start=1):
        db.execute(f"UPDATE members SET {queue_col} = ? WHERE id = ?", (pos, member["id"]))

    await manager.broadcast("refresh")
    return {
        "status": "success",
        "skipped_priority_count": len(skipped_members),
        "next_priority_ids": [m["id"] for m in skipped_members]
    }

@app.get("/api/history")
def get_history():
    return db.fetchall("SELECT * FROM auction_cycles ORDER BY id DESC")

@app.get("/api/history/{cycle_id}")
def get_cycle_detail(cycle_id: int):
    return db.fetchall("SELECT * FROM cycle_members WHERE cycle_id = ? ORDER BY queue_position_before", (cycle_id,))

@app.get("/api/history/{cycle_id}/officers")
def get_cycle_officers(cycle_id: int):
    return db.fetchall("SELECT officer_username, lnd_awarded, tns_awarded FROM cycle_officer_leftovers WHERE cycle_id = ? ORDER BY officer_username", (cycle_id,))

@app.get("/api/history/{cycle_id}/leftover-members")
def get_cycle_leftover_members(cycle_id: int):
    return db.fetchall("SELECT member_name, lnd_awarded, tns_awarded FROM cycle_leftover_members WHERE cycle_id = ? ORDER BY member_name", (cycle_id,))

@app.put("/api/history/member/{cm_id}")
async def update_cycle_member(cm_id: int, data: dict):
    if "proof_checked" in data and set(data.keys()).issubset({"proof_checked"}):
        db.execute("UPDATE cycle_members SET proof_checked = ? WHERE id = ?", (1 if data.get("proof_checked") else 0, cm_id))
        await manager.broadcast("refresh")
        return {"status": "success"}

    lnd = data.get("lnd_awarded", 0)
    tns = data.get("tns_awarded", 0)
    status = data.get("status")
    if status not in (None, "Done", "Waiting", "Skip"):
        raise HTTPException(status_code=400, detail="Invalid status.")
    if status is None:
        db.execute("UPDATE cycle_members SET lnd_awarded = ?, tns_awarded = ? WHERE id = ?", (lnd, tns, cm_id))
    else:
        db.execute("UPDATE cycle_members SET lnd_awarded = ?, tns_awarded = ?, status = ?, participated = ? WHERE id = ?", (lnd, tns, status, 1 if status == "Done" else 0, cm_id))
    await manager.broadcast("refresh")
    return {"status": "success"}

@app.delete("/api/history/{cycle_id}")
async def delete_cycle(cycle_id: int):
    db.execute("DELETE FROM cycle_officer_leftovers WHERE cycle_id = ?", (cycle_id,))
    db.execute("DELETE FROM cycle_leftover_members WHERE cycle_id = ?", (cycle_id,))
    db.execute("DELETE FROM cycle_members WHERE cycle_id = ?", (cycle_id,))
    db.execute("DELETE FROM auction_cycles WHERE id = ?", (cycle_id,))
    await manager.broadcast("refresh")
    return {"status": "success"}

@app.get("/api/admin/users")
def get_users():
    return db.fetchall("SELECT id, username, email, role, created_at FROM users ORDER BY id")

@app.put("/api/admin/users/{user_id}/role")
async def update_user_role(user_id: int, data: dict):
    role = data.get("role")
    if role not in ["Pending", "Viewer", "Officer", "Admin"]:
        raise HTTPException(status_code=400, detail="Invalid role.")
    db.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
    await manager.broadcast("refresh")
    return {"status": "success"}

@app.delete("/api/admin/users/{user_id}")
async def delete_user(user_id: int):
    db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    await manager.broadcast("refresh")
    return {"status": "success"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("server:app", host="0.0.0.0", port=port)
