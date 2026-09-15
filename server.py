import os
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "prestigegaruda_auction.db"
MAX_MEMBERS = 80
VALID_ROLES = ["Main DPS", "Sub DPS", "Utility", "Bard", "Priest"]

class Database:
    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.create_tables()

    def create_tables(self):
        self.conn.executescript("""
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
        
        count = self.conn.execute("SELECT COUNT(*) AS c FROM league_teams").fetchone()["c"]
        if count == 0:
            for i in range(1, 9):
                self.conn.execute(
                    "INSERT INTO league_teams (team_number, battlefield_type, slot_main_dps, slot_sub_dps, slot_utility, slot_bard, slot_fs) VALUES (?, 'Main', NULL, NULL, NULL, NULL, NULL)",
                    (i,)
                )
            for i in range(9, 17):
                self.conn.execute(
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

# HTML Template with Embedded Dashboard
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PrestigeGaruda Auction & Strike-Force Suite</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body { background-color: #0b0f19; color: #d1d5db; font-family: 'Segoe UI', Inter, sans-serif; }
        .cyber-card { background-color: #0d1322; border: 1px solid #1f293d; border-radius: 6px; }
        .cyber-input { background-color: #060911; border: 1px solid #1f293d; color: #ffffff; }
        .cyber-input:focus { border-color: #00E5FF; outline: none; }
        .cyber-btn { background-color: #111a2e; border: 1px solid #00E5FF; color: #00E5FF; font-weight: bold; border-radius: 4px; transition: 0.2s; }
        .cyber-btn:hover { background-color: #00E5FF; color: #000000; }
        .tab-btn { background-color: #0d1322; color: #9ca3af; border: 1px solid #1f293d; font-weight: bold; }
        .tab-btn.active { background-color: #162035; color: #00E5FF; border-bottom: 2px solid #00E5FF; }
    </style>
</head>
<body class="p-4 md:p-8">
    <div class="max-w-7xl mx-auto">
        <header class="mb-6 flex flex-col md:flex-row justify-between items-center border-b border-gray-800 pb-4">
            <h1 class="text-2xl font-black text-cyan-400 tracking-wider">🦅 PRESTIGEGARUDA // COMMAND SUITE</h1>
            <div id="capacity-badge" class="text-sm font-semibold text-cyan-400 mt-2 md:mt-0">Loading Capacity...</div>
        </header>

        <!-- Navigation Tabs -->
        <div class="flex flex-wrap gap-2 mb-6" id="nav-tabs">
            <button onclick="switchTab('members')" class="tab-btn active px-4 py-2 rounded-t-lg">MEMBERS & QUEUES</button>
            <button onclick="switchTab('teams')" class="tab-btn px-4 py-2 rounded-t-lg">BATTLEFIELD STRATAGEMS (16)</button>
            <button onclick="switchTab('gl')" class="tab-btn px-4 py-2 rounded-t-lg">GL AUCTION</button>
            <button onclick="switchTab('eo')" class="tab-btn px-4 py-2 rounded-t-lg">EO AUCTION</button>
            <button onclick="switchTab('history')" class="tab-btn px-4 py-2 rounded-t-lg">AUCTION ARCHIVES</button>
        </div>

        <!-- TAB 1: MEMBERS -->
        <div id="tab-members" class="space-y-6 tab-content">
            <div class="cyber-card p-6">
                <h2 class="text-lg font-bold text-cyan-400 mb-4">⚔️ REGISTER ROSTER MEMBER</h2>
                <form id="member-form" onsubmit="addMember(event)" class="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                        <label class="block text-xs uppercase mb-1 text-gray-400">Character Name</label>
                        <input type="text" id="m-name" required class="cyber-input w-full p-2 rounded">
                    </div>
                    <div>
                        <label class="block text-xs uppercase mb-1 text-gray-400">Class</label>
                        <input type="text" id="m-class" class="cyber-input w-full p-2 rounded">
                    </div>
                    <div>
                        <label class="block text-xs uppercase mb-1 text-gray-400">Role</label>
                        <select id="m-role" class="cyber-input w-full p-2 rounded">
                            <option>Main DPS</option><option>Sub DPS</option><option>Utility</option><option>Bard</option><option>Priest</option>
                        </select>
                    </div>
                    <div>
                        <label class="block text-xs uppercase mb-1 text-gray-400">Notes</label>
                        <input type="text" id="m-notes" class="cyber-input w-full p-2 rounded">
                    </div>
                    <div class="md:col-span-2">
                        <button type="submit" class="cyber-btn w-full py-2">+ REGISTER MEMBER</button>
                    </div>
                </form>
            </div>

            <div class="cyber-card p-6 overflow-x-auto">
                <table class="w-full text-left border-collapse">
                    <thead>
                        <tr class="border-b border-gray-800 text-cyan-400 text-xs uppercase">
                            <th class="p-3">GLQ</th><th class="p-3">EOQ</th><th class="p-3">Name</th><th class="p-3">Class</th><th class="p-3">Role</th><th class="p-3">Notes</th><th class="p-3">Created</th><th class="p-3">Actions</th>
                        </tr>
                    </thead>
                    <tbody id="members-table-body" class="text-sm divide-y divide-gray-800"></tbody>
                </table>
            </div>
        </div>

        <!-- TAB 2: TEAMS -->
        <div id="tab-teams" class="space-y-6 tab-content hidden">
            <div class="cyber-card p-6 flex justify-between items-center">
                <p class="text-sm text-gray-400 italic">Teams 01-08 map to Main Battlefield. Teams 09-16 handle Sub Battlefield.</p>
                <button onclick="saveAllTeams()" class="cyber-btn px-6 py-2">🔒 COMMIT ALL DEPLOYMENTS</button>
            </div>
            <div class="cyber-card p-6 overflow-x-auto">
                <table class="w-full text-left border-collapse text-sm">
                    <thead>
                        <tr class="border-b border-gray-800 text-cyan-400 text-xs uppercase">
                            <th class="p-3">Battlefield</th><th class="p-3">Team</th><th class="p-3">Slot 1: Main DPS</th><th class="p-3">Slot 2: Sub DPS</th><th class="p-3">Slot 3: Utility</th><th class="p-3">Slot 4: Bard</th><th class="p-3">Slot 5: FS (Priest)</th>
                        </tr>
                    </thead>
                    <tbody id="teams-table-body" class="divide-y divide-gray-800"></tbody>
                </table>
            </div>
        </div>

        <!-- TAB 3 & 4: GL / EO AUCTION -->
        <div id="tab-gl" class="space-y-6 tab-content hidden">
            <!-- Rendered dynamically via JS -->
        </div>
        <div id="tab-eo" class="space-y-6 tab-content hidden">
            <!-- Rendered dynamically via JS -->
        </div>

        <!-- TAB 5: HISTORY -->
        <div id="tab-history" class="space-y-6 tab-content hidden">
            <div class="cyber-card p-6">
                <button onclick="loadHistory()" class="cyber-btn px-4 py-2 mb-4">🔄 REFRESH ARCHIVES</button>
                <div class="overflow-x-auto mb-4">
                    <table class="w-full text-left border-collapse text-xs">
                        <thead>
                            <tr class="border-b border-gray-800 text-cyan-400 uppercase">
                                <th class="p-2">Cycle</th><th class="p-2">Type</th><th class="p-2">Puppet</th><th class="p-2">LND</th><th class="p-2">TNS</th><th class="p-2">Participants</th><th class="p-2">LND Ea</th><th class="p-2">TNS Ea</th><th class="p-2">Timestamp</th>
                            </tr>
                        </thead>
                        <tbody id="history-table-body" class="divide-y divide-gray-800"></tbody>
                    </table>
                </div>
                <div id="history-detail" class="text-cyan-400 font-mono text-sm p-4 bg-gray-900 rounded border border-gray-800">Select an archived cycle above.</div>
            </div>
        </div>
    </div>

    <script>
        let membersData = [];
        let rolesPool = { "Main DPS": [], "Sub DPS": [], "Utility": [], "Bard": [], "Priest": [] };

        function switchTab(tabId) {
            document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
            document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
            document.getElementById('tab-' + tabId).classList.remove('hidden');
            event.target.classList.add('active');
            if(tabId === 'gl' || tabId === 'eo') setupAuctionTab(tabId.toUpperCase());
        }

        async function loadAppData() {
            const res = await fetch('/api/data');
            const data = await res.json();
            membersData = data.members;
            rolesPool = data.roles_pool;
            document.getElementById('capacity-badge').innerText = `PRESTIGEGARUDA CAPACITY: ${membersData.length} / 80`;
            renderMembers();
            renderTeams(data.teams);
        }

        async function addMember(e) {
            e.preventDefault();
            const payload = {
                name: document.getElementById('m-name').value,
                character_class: document.getElementById('m-class').value,
                role: document.getElementById('m-role').value,
                notes: document.getElementById('m-notes').value
            };
            const res = await fetch('/api/members', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
            if(res.ok) {
                document.getElementById('member-form').reset();
                loadAppData();
            } else {
                const err = await res.json();
                alert(err.detail);
            }
        }

        async function deleteMember(id) {
            if(confirm("Are you sure you want to remove this member?")) {
                await fetch(`/api/members/${id}`, { method: 'DELETE' });
                loadAppData();
            }
        }

        function renderMembers() {
            const tbody = document.getElementById('members-table-body');
            tbody.innerHTML = membersData.map(m => `
                <tr class="hover:bg-gray-900">
                    <td class="p-3 text-cyan-400 font-bold">${m.gl_queue_position}</td>
                    <td class="p-3 text-cyan-400 font-bold">${m.eo_queue_position}</td>
                    <td class="p-3 font-semibold">${m.name}</td>
                    <td class="p-3">${m.character_class || ''}</td>
                    <td class="p-3">${m.role || ''}</td>
                    <td class="p-3">${m.notes || ''}</td>
                    <td class="p-3 text-xs text-gray-500">${m.created_at}</td>
                    <td class="p-3"><button onclick="deleteMember(${m.id})" class="text-red-400 border border-red-500 px-2 py-1 rounded text-xs hover:bg-red-500 hover:text-black">PURGE</button></td>
                </tr>
            `).join('');
        }

        function renderTeams(teams) {
            const tbody = document.getElementById('teams-table-body');
            tbody.innerHTML = teams.map(t => {
                const isMain = t.battlefield_type === 'Main';
                return `<tr class="team-row" data-team="${t.team_number}">
                    <td class="p-3 font-bold ${isMain ? 'text-red-400' : 'text-yellow-400'}">⚔️ ${t.battlefield_type.toUpperCase()} BF</td>
                    <td class="p-3 text-cyan-400 font-bold">TEAM ${String(t.team_number).padStart(2, '0')}</td>
                    <td class="p-3">${roleDropdown('slot_main_dps', 'Main DPS', t.slot_main_dps)}</td>
                    <td class="p-3">${roleDropdown('slot_sub_dps', 'Sub DPS', t.slot_sub_dps)}</td>
                    <td class="p-3">${roleDropdown('slot_utility', 'Utility', t.slot_utility)}</td>
                    <td class="p-3">${roleDropdown('slot_bard', 'Bard', t.slot_bard)}</td>
                    <td class="p-3">${roleDropdown('slot_fs', 'Priest', t.slot_fs)}</td>
                </tr>`;
            }).join('');
        }

        function roleDropdown(slotName, roleKey, selectedId) {
            const pool = rolesPool[roleKey] || [];
            let opts = `<option value="">--- VACANT SLOT ---</option>`;
            pool.forEach(m => {
                opts += `<option value="${m.id}" ${m.id === selectedId ? 'selected' : ''}>${m.name}</option>`;
            });
            return `<select class="cyber-input w-full p-1 text-xs team-slot" data-slot="${slotName}">${opts}</select>`;
        }

        async function saveAllTeams() {
            const rows = document.querySelectorAll('.team-row');
            const payload = [];
            rows.forEach(r => {
                const teamNum = parseInt(r.getAttribute('data-team'));
                const selects = r.querySelectorAll('.team-slot');
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

        // Auction tab builder
        function setupAuctionTab(type) {
            const container = document.getElementById(`tab-${type.toLowerCase()}`);
            container.innerHTML = `
                <div class="cyber-card p-6 space-y-4">
                    <h2 class="text-lg font-bold text-cyan-400">🦅 ${type} AUCTION MATRIX CONFIGURATOR</h2>
                    <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                        <div><label class="block text-xs uppercase mb-1">Puppet (Count)</label><input type="number" id="${type}-puppet" value="10" min="1" max="80" class="cyber-input w-full p-2 rounded"></div>
                        <div><label class="block text-xs uppercase mb-1">LND Pool</label><input type="number" id="${type}-lnd" value="0" min="0" class="cyber-input w-full p-2 rounded"></div>
                        <div><label class="block text-xs uppercase mb-1">TNS Pool</label><input type="number" id="${type}-tns" value="0" min="0" class="cyber-input w-full p-2 rounded"></div>
                    </div>
                    <button onclick="previewAuction('${type}')" class="cyber-btn w-full py-2">⚡ PREVIEW ${type} BOARD</button>
                    <div id="${type}-result" class="text-cyan-400 font-mono text-sm"></div>
                </div>
                <div class="cyber-card p-6 space-y-4">
                    <h3 class="font-bold text-cyan-400">📋 PARTICIPATION MONITOR — UNCHECK SKIPPED MEMBERS</h3>
                    <div class="overflow-x-auto"><table class="w-full text-left border-collapse text-sm" id="${type}-preview-table"></table></div>
                    <button onclick="commitAuction('${type}')" class="cyber-btn w-full py-2">🔒 COMMIT ${type} CYCLE & ROTATE QUEUE</button>
                </div>
            `;
        }

        async function previewAuction(type) {
            const puppet = document.getElementById(`${type}-puppet`).value;
            const lnd = document.getElementById(`${type}-lnd`).value;
            const tns = document.getElementById(`${type}-tns`).value;
            const res = await fetch(`/api/auction/preview?type=${type}&puppet=${puppet}&lnd=${lnd}&tns=${tns}`);
            const data = await res.json();
            
            document.getElementById(`${type}-result`).innerHTML = `<b>${type} AUCTION PREVIEW: ${data.rows.length} ACTIVE MEMBER(S)</b><br>LND Each: ${data.lnd_each} | TNS Each: ${data.tns_each}`;
            
            const table = document.getElementById(`${type}-preview-table`);
            table.innerHTML = `<thead><tr class="border-b border-gray-800 text-cyan-400 text-xs uppercase"><th class="p-2">Queue</th><th class="p-2">Member</th><th class="p-2">Participated</th><th class="p-2">LND</th><th class="p-2">TNS</th></tr></thead>` +
            data.rows.map(r => `
                <tr class="border-b border-gray-900">
                    <td class="p-2 text-cyan-400">${r.queue_pos}</td>
                    <td class="p-2 font-semibold">${r.name}</td>
                    <td class="p-2"><input type="checkbox" checked class="auction-chk-${type}" data-id="${r.id}" onchange="recalcAuction('${type}')"></td>
                    <td class="p-2 lnd-val">${data.lnd_each}</td>
                    <td class="p-2 tns-val">${data.tns_each}</td>
                </tr>
            `).join('');
        }

        function recalcAuction(type) {
            const chks = document.querySelectorAll(`.auction-chk-${type}`);
            let count = 0;
            chks.forEach(c => { if(c.checked) count++; });
            const lndTotal = parseInt(document.getElementById(`${type}-lnd`).value) || 0;
            const tnsTotal = parseInt(document.getElementById(`${type}-tns`).value) || 0;
            const lndEach = count > 0 ? Math.floor(lndTotal / count) : 0;
            const tnsEach = count > 0 ? Math.floor(tnsTotal / count) : 0;
            
            const rows = document.querySelectorAll(`#${type}-preview-table tbody tr`);
            rows.forEach((r, idx) => {
                const isChecked = chks[idx].checked;
                r.querySelector('.lnd-val').innerText = isChecked ? lndEach : 0;
                r.querySelector('.tns-val').innerText = isChecked ? tnsEach : 0;
            });
        }

        async function commitAuction(type) {
            const chks = document.querySelectorAll(`.auction-chk-${type}`);
            const participants = [];
            chks.forEach(c => {
                if(c.checked) participants.push(parseInt(c.getAttribute('data-id')));
            });

            const payload = {
                auction_type: type,
                puppet_count: parseInt(document.getElementById(`${type}-puppet`).value),
                lnd_total: parseInt(document.getElementById(`${type}-lnd`).value) || 0,
                tns_total: parseInt(document.getElementById(`${type}-tns`).value) || 0,
                participant_ids: participants
            };

            const res = await fetch('/api/auction/commit', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
            if(res.ok) {
                alert(`${type} Auction cycle committed successfully.`);
                loadAppData();
            }
        }

        async function loadHistory() {
            const res = await fetch('/api/history');
            const data = await res.json();
            const tbody = document.getElementById('history-table-body');
            tbody.innerHTML = data.map(c => `
                <tr onclick="loadCycleDetail(${c.id})" class="cursor-pointer hover:bg-gray-900">
                    <td class="p-2 text-cyan-400 font-bold">${c.id}</td>
                    <td class="p-2">${c.auction_type}</td>
                    <td class="p-2">${c.puppet_count}</td>
                    <td class="p-2">${c.lnd_total}</td>
                    <td class="p-2">${c.tns_total}</td>
                    <td class="p-2">${c.participant_count}</td>
                    <td class="p-2">${c.lnd_each}</td>
                    <td class="p-2">${c.tns_each}</td>
                    <td class="p-2 text-gray-500">${c.created_at}</td>
                </tr>
            `).join('');
        }

        async function loadCycleDetail(id) {
            const res = await fetch(`/api/history/${id}`);
            const data = await res.json();
            const part = data.filter(m => m.participated).map(m => `${m.member_name} (LND:${m.lnd_awarded}, TNS:${m.tns_awarded})`).join(', ');
            const skipped = data.filter(m => !m.participated).map(m => m.member_name).join(', ');
            document.getElementById('history-detail').innerHTML = `<b>PARTICIPATED:</b> <span class="text-green-400">${part || 'None'}</span><br><b>SKIPPED:</b> <span class="text-red-400">${skipped || 'None'}</span>`;
        }

        window.onload = loadAppData;
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def serve_frontend():
    return HTML_TEMPLATE

@app.get("/api/data")
def get_app_data():
    members = db.fetchall("SELECT * FROM members ORDER BY gl_queue_position, id")
    teams = db.fetchall("SELECT * FROM league_teams ORDER BY team_number ASC")
    roles_pool = {}
    for role in VALID_ROLES:
        roles_pool[role] = db.fetchall("SELECT id, name FROM members WHERE role = ? ORDER BY name", (role,))
    return {"members": members, "teams": teams, "roles_pool": roles_pool}

@app.post("/api/members")
def add_member(data: dict):
    name = data.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Character name is required.")
    
    count = db.fetchone("SELECT COUNT(*) AS c FROM members")["c"]
    if count >= MAX_MEMBERS:
        raise HTTPException(status_code=400, detail=f"Roster limit of {MAX_MEMBERS} members reached.")

    gl_pos = db.fetchone("SELECT COALESCE(MAX(gl_queue_position), 0) AS p FROM members")["p"] + 1
    eo_pos = db.fetchone("SELECT COALESCE(MAX(eo_queue_position), 0) AS p FROM members")["p"] + 1
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    db.execute(
        "INSERT INTO members (name, character_class, role, notes, gl_queue_position, eo_queue_position, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (name, data.get("character_class", "").strip(), data.get("role", "Main DPS"), data.get("notes", "").strip(), gl_pos, eo_pos, now_str)
    )
    return {"status": "success"}

@app.delete("/api/members/{member_id}")
def delete_member(member_id: int):
    for slot in ["slot_main_dps", "slot_sub_dps", "slot_utility", "slot_bard", "slot_fs"]:
        db.execute(f"UPDATE league_teams SET {slot} = NULL WHERE {slot} = ?", (member_id,))
    
    db.execute("DELETE FROM members WHERE id = ?", (member_id,))
    
    for queue_col in ["gl_queue_position", "eo_queue_position"]:
        remaining = db.fetchall(f"SELECT id FROM members ORDER BY {queue_col}, id")
        for pos, row in enumerate(remaining, start=1):
            db.execute(f"UPDATE members SET {queue_col} = ? WHERE id = ?", (pos, row["id"]))
            
    return {"status": "success"}

@app.post("/api/teams")
def save_teams(teams: List[dict]):
    for t in teams:
        db.execute("""
            UPDATE league_teams 
            SET slot_main_dps = ?, slot_sub_dps = ?, slot_utility = ?, slot_bard = ?, slot_fs = ?
            WHERE team_number = ?
        """, (t.get("slot_main_dps"), t.get("slot_sub_dps"), t.get("slot_utility"), t.get("slot_bard"), t.get("slot_fs"), t.get("team_number")))
    return {"status": "success"}

@app.get("/api/auction/preview")
def preview_auction(type: str, puppet: int, lnd: int, tns: int):
    queue_col = "gl_queue_position" if type == "GL" else "eo_queue_position"
    available = db.fetchone("SELECT COUNT(*) AS c FROM members")["c"]
    if available == 0:
        return {"rows": [], "lnd_each": 0, "tns_each": 0}
    
    puppet_count = min(puppet, available)
    rows = db.fetchall(f"SELECT id, name, {queue_col} as queue_pos FROM members ORDER BY {queue_col}, id LIMIT ?", (puppet_count,))
    
    lnd_each = lnd // puppet_count if puppet_count > 0 else 0
    tns_each = tns // puppet_count if puppet_count > 0 else 0
    return {"rows": rows, "lnd_each": lnd_each, "tns_each": tns_each}

@app.post("/api/auction/commit")
def commit_auction(data: dict):
    auction_type = data.get("auction_type")
    queue_col = "gl_queue_position" if auction_type == "GL" else "eo_queue_position"
    puppet_count = data.get("puppet_count")
    lnd_total = data.get("lnd_total", 0)
    tns_total = data.get("tns_total", 0)
    participant_ids = set(data.get("participant_ids", []))
    
    participants_count = len(participant_ids)
    if participants_count == 0:
        raise HTTPException(status_code=400, detail="At least one participant is required.")
        
    lnd_each = lnd_total // participants_count
    lnd_left = lnd_total % participants_count
    tns_each = tns_total // participants_count
    tns_left = tns_total % participants_count
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    cycle = db.execute(
        """INSERT INTO auction_cycles (auction_type, puppet_count, lnd_total, tns_total, participant_count, lnd_each, lnd_leftover, tns_each, tns_leftover, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (auction_type, puppet_count, lnd_total, tns_total, participants_count, lnd_each, lnd_left, tns_each, tns_left, now_str)
    )
    cycle_id = cycle.lastrowid

    current_queue = db.fetchall(f"SELECT * FROM members ORDER BY {queue_col}, id")
    selected_rows = current_queue[:puppet_count]
    selected_ids = {m["id"] for m in selected_rows}

    for row in selected_rows:
        is_part = row["id"] in participant_ids
        db.execute(
            "INSERT INTO cycle_members (cycle_id, member_name, queue_position_before, participated, lnd_awarded, tns_awarded) VALUES (?, ?, ?, ?, ?, ?)",
            (cycle_id, row["name"], row[queue_col], 1 if is_part else 0, lnd_each if is_part else 0, tns_each if is_part else 0)
        )

    skipped = [m for m in current_queue if m["id"] in selected_ids and m["id"] not in participant_ids]
    untouched = [m for m in current_queue if m["id"] not in selected_ids]
    participated = [m for m in current_queue if m["id"] in participant_ids]

    new_queue = skipped + untouched + participated
    for pos, member in enumerate(new_queue, start=1):
        db.execute(f"UPDATE members SET {queue_col} = ? WHERE id = ?", (pos, member["id"]))

    return {"status": "success"}

@app.get("/api/history")
def get_history():
    return db.fetchall("SELECT * FROM auction_cycles ORDER BY id DESC")

@app.get("/api/history/{cycle_id}")
def get_cycle_detail(cycle_id: int):
    return db.fetchall("SELECT * FROM cycle_members WHERE cycle_id = ? ORDER BY queue_position_before", (cycle_id,))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("server:app", host="0.0.0.0", port=port)
