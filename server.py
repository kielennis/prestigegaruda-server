import os
import sqlite3
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import List, Optional

from fastapi import FastAPI, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "prestigegaruda_auction.db"
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
            FOREIGN KEY(cycle_id) REFERENCES auction_cycles(id) ON DELETE CASCADE
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
            <div>
                <h1 class="text-2xl font-black text-cyan-400 tracking-wider">🦅 PRESTIGEGARUDA // COMMAND SUITE</h1>
                <div id="capacity-badge" class="text-xs font-semibold text-gray-400 mt-1">Loading Capacity...</div>
            </div>
            <div class="flex items-center gap-4 mt-4 md:mt-0">
                <div id="auth-status" class="text-sm font-mono text-cyan-300">Status: Viewer</div>
                <button onclick="openAuthModal()" id="auth-btn" class="cyber-btn px-3 py-1 text-xs">LOGIN / REGISTER</button>
            </div>
        </header>

        <!-- Navigation Tabs -->
        <div class="flex flex-wrap gap-2 mb-6" id="nav-tabs">
            <button onclick="switchTab('members')" class="tab-btn active px-4 py-2 rounded-t-lg">MEMBERS & QUEUES</button>
            <button onclick="switchTab('teams')" class="tab-btn px-4 py-2 rounded-t-lg">BATTLEFIELD STRATAGEMS (16)</button>
            <button onclick="switchTab('gl')" class="tab-btn px-4 py-2 rounded-t-lg">GL AUCTION</button>
            <button onclick="switchTab('eo')" class="tab-btn px-4 py-2 rounded-t-lg">EO AUCTION</button>
            <button onclick="switchTab('history')" class="tab-btn px-4 py-2 rounded-t-lg">AUCTION ARCHIVES</button>
            <button onclick="switchTab('admin')" id="admin-tab-btn" class="tab-btn px-4 py-2 rounded-t-lg hidden">ADMIN PANEL</button>
        </div>

        <!-- TAB 1: MEMBERS -->
        <div id="tab-members" class="space-y-6 tab-content">
            <div class="cyber-card p-6 auth-restricted">
                <h2 class="text-lg font-bold text-cyan-400 mb-4">⚔️ REGISTER ROSTER MEMBER</h2>
                <form id="member-form" onsubmit="addMember(event)" class="grid grid-cols-1 md:grid-cols-3 gap-4">
                    <div>
                        <label class="block text-xs uppercase mb-1 text-gray-400">Character Name (Unique)</label>
                        <input type="text" id="m-name" required class="cyber-input w-full p-2 rounded">
                    </div>
                    <div>
                        <label class="block text-xs uppercase mb-1 text-gray-400">Role</label>
                        <select id="m-role" onchange="updateClassOptions('m-role', 'm-class')" class="cyber-input w-full p-2 rounded">
                            <option>Main DPS</option><option>Sub DPS</option><option>Utility</option><option>Healer</option><option>Support</option>
                        </select>
                    </div>
                    <div>
                        <label class="block text-xs uppercase mb-1 text-gray-400">Class</label>
                        <select id="m-class" class="cyber-input w-full p-2 rounded"></select>
                    </div>
                    <div class="md:col-span-3">
                        <button type="submit" class="cyber-btn w-full py-2">+ REGISTER MEMBER</button>
                    </div>
                </form>
            </div>

            <div class="cyber-card p-6 overflow-x-auto">
                <table class="w-full text-left border-collapse">
                    <thead>
                        <tr class="border-b border-gray-800 text-cyan-400 text-xs uppercase">
                            <th class="p-3">GLQ</th><th class="p-3">EOQ</th><th class="p-3">Name</th><th class="p-3">Role</th><th class="p-3">Class</th><th class="p-3">Created</th><th class="p-3 auth-restricted-col">Actions</th>
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
                                    <th class="p-2">Member</th><th class="p-2">Queue Pos</th><th class="p-2">Participated</th><th class="p-2">LND Awarded</th><th class="p-2">TNS Awarded</th><th class="p-2 auth-restricted-col">Action</th>
                                </tr>
                            </thead>
                            <tbody id="archive-members-body" class="divide-y divide-gray-800"></tbody>
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

        // Establish Real-time WebSocket connection
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const ws = new WebSocket(`${protocol}//${window.location.host}/ws`);

        ws.onmessage = function(event) {
            if (event.data === "refresh") {
                console.log("Real-time update received from server. Refreshing view...");
                loadAppData();
                loadHistory();
                if (activeArchiveCycleId !== null) {
                    loadCycleDetail(activeArchiveCycleId);
                }
                const adminTab = document.getElementById('tab-admin');
                if (adminTab && !adminTab.classList.contains('hidden')) {
                    loadAdminUsers();
                }
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
            document.getElementById('capacity-badge').innerText = `PRESTIGEGARUDA CAPACITY: ${membersData.length} / 80 | Time (WIB): ${data.server_time}`;
            renderMembers();
            renderTeams(data.teams);
            updateAuthUI();
            updateClassOptions('m-role', 'm-class');
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
                    <td class="p-3 text-cyan-400 font-bold">${m.gl_queue_position}</td>
                    <td class="p-3 text-cyan-400 font-bold">${m.eo_queue_position}</td>
                    <td class="p-3 font-semibold">${m.name}</td>
                    <td class="p-3">${m.role}</td>
                    <td class="p-3 text-cyan-200">${m.class_name}</td>
                    <td class="p-3 text-xs text-gray-500">${m.created_at}</td>
                    <td class="p-3 auth-restricted-col" style="display: ${isAdminOrOfficer ? 'table-cell' : 'none'};">
                        <button onclick="openEditModal(${m.id}, '${m.name.replace(/'/g, "\\'")}', '${m.role}', '${m.class_name}')" class="text-cyan-400 border border-cyan-500 px-2 py-1 rounded text-xs hover:bg-cyan-500 hover:text-black mr-1">EDIT</button>
                        <button onclick="deleteMember(${m.id})" class="text-red-400 border border-red-500 px-2 py-1 rounded text-xs hover:bg-red-500 hover:text-black">PURGE</button>
                    </td>
                </tr>
            `).join('');
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
                opts += `<option value="${m.id}" ${m.id === selectedId ? 'selected' : ''}>${m.name} (${m.class_name})</option>`;
            });
            return `<select class="cyber-input w-full p-1 text-xs team-slot" data-slot="${slotName}">${opts}</select>`;
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
            container.innerHTML = `
                <div class="cyber-card p-6 space-y-4">
                    <h2 class="text-lg font-bold text-cyan-400">🦅 ${type} AUCTION MATRIX CONFIGURATOR</h2>
                    <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                        <div><label class="block text-xs uppercase mb-1">Puppet (Count)</label><input type="number" id="${type}-puppet" value="10" min="1" max="80" class="cyber-input w-full p-2 rounded" onchange="previewAuction('${type}')"></div>
                        <div><label class="block text-xs uppercase mb-1">LND Pool</label><input type="number" id="${type}-lnd" value="0" min="0" class="cyber-input w-full p-2 rounded" onchange="recalcAuction('${type}')"></div>
                        <div><label class="block text-xs uppercase mb-1">TNS Pool</label><input type="number" id="${type}-tns" value="0" min="0" class="cyber-input w-full p-2 rounded" onchange="recalcAuction('${type}')"></div>
                    </div>
                    <button onclick="previewAuction('${type}')" class="cyber-btn w-full py-2">⚡ PREVIEW ${type} BOARD & AUTO-FILL SKIP</button>
                    <div id="${type}-result" class="text-cyan-400 font-mono text-sm"></div>
                </div>
                <div class="cyber-card p-6 space-y-4 auth-restricted">
                    <h3 class="font-bold text-cyan-400">📋 PARTICIPATION MONITOR — UNCHECK SKIPPED (Auto-pulls next queue member to match Puppet)</h3>
                    <div class="overflow-x-auto"><table class="w-full text-left border-collapse text-sm" id="${type}-preview-table"></table></div>
                    <button onclick="commitAuction('${type}')" class="cyber-btn w-full py-2">🔒 COMMIT ${type} CYCLE & ROTATE QUEUE</button>
                </div>
            `;
            previewAuction(type);
            updateAuthUI();
        }

        async function previewAuction(type) {
            const puppet = parseInt(document.getElementById(`${type}-puppet`).value) || 10;
            const res = await fetch(`/api/auction/preview?type=${type}&puppet=${puppet}`);
            const data = await res.json();
            
            const lndTotal = parseInt(document.getElementById(`${type}-lnd`).value) || 0;
            const tnsTotal = parseInt(document.getElementById(`${type}-tns`).value) || 0;
            const lndEach = puppet > 0 ? Math.floor(lndTotal / puppet) : 0;
            const tnsEach = puppet > 0 ? Math.floor(tnsTotal / puppet) : 0;

            document.getElementById(`${type}-result`).innerHTML = `<b>${type} AUCTION PREVIEW: ${data.rows.length} ACTIVE PARTICIPANTS (Puppet: ${puppet})</b><br>LND Each: ${lndEach} | TNS Each: ${tnsEach}`;
            
            const table = document.getElementById(`${type}-preview-table`);
            table.innerHTML = `<thead><tr class="border-b border-gray-800 text-cyan-400 text-xs uppercase"><th class="p-2">Queue</th><th class="p-2">Member</th><th class="p-2">Class</th><th class="p-2">Participated</th><th class="p-2">LND</th><th class="p-2">TNS</th></tr></thead>` +
            data.rows.map(r => `
                <tr class="border-b border-gray-950">
                    <td class="p-2 text-cyan-400 font-bold">${r.queue_pos}</td>
                    <td class="p-2 font-semibold">${r.name}</td>
                    <td class="p-2 text-cyan-200 text-xs">${r.class_name}</td>
                    <td class="p-2"><input type="checkbox" checked class="auction-chk-${type}" data-id="${r.id}" onchange="recalcAuction('${type}')"></td>
                    <td class="p-2 lnd-val">${lndEach}</td>
                    <td class="p-2 tns-val">${tnsEach}</td>
                </tr>
            `).join('');
        }

        function recalcAuction(type) {
            const puppet = parseInt(document.getElementById(`${type}-puppet`).value) || 10;
            const lndTotal = parseInt(document.getElementById(`${type}-lnd`).value) || 0;
            const tnsTotal = parseInt(document.getElementById(`${type}-tns`).value) || 0;
            const lndEach = puppet > 0 ? Math.floor(lndTotal / puppet) : 0;
            const tnsEach = puppet > 0 ? Math.floor(tnsTotal / puppet) : 0;
            
            const rows = document.querySelectorAll(`#${type}-preview-table tbody tr`);
            rows.forEach((r, idx) => {
                const chk = r.querySelector(`.auction-chk-${type}`);
                const isChecked = chk ? chk.checked : true;
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
                switchTab('history');
            } else {
                const err = await res.json();
                alert(err.detail);
            }
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
            document.getElementById('archive-detail-title').innerText = `Archive #${id} — Participating Players & Bids`;
            document.getElementById('history-detail-container').classList.remove('hidden');
            
            const isAdminOrOfficer = currentUser.role === 'Admin' || currentUser.role === 'Officer';
            const tbody = document.getElementById('archive-members-body');
            tbody.innerHTML = data.map(m => `
                <tr class="border-b border-gray-900" data-cm-id="${m.id}">
                    <td class="p-2 font-semibold">${m.member_name}</td>
                    <td class="p-2 text-cyan-400">${m.queue_position_before}</td>
                    <td class="p-2">${m.participated ? 'Yes' : 'No'}</td>
                    <td class="p-2"><input type="number" value="${m.lnd_awarded}" class="cyber-input w-20 p-1 rounded text-xs archive-lnd" ${!isAdminOrOfficer ? 'disabled' : ''}></td>
                    <td class="p-2"><input type="number" value="${m.tns_awarded}" class="cyber-input w-20 p-1 rounded text-xs archive-tns" ${!isAdminOrOfficer ? 'disabled' : ''}></td>
                    <td class="p-2 auth-restricted-col" style="display: ${isAdminOrOfficer ? 'table-cell' : 'none'};">
                        <button onclick="updateCycleMember(${m.id})" class="text-cyan-400 border border-cyan-500 px-2 py-0.5 rounded text-xs hover:bg-cyan-500 hover:text-black">SAVE</button>
                    </td>
                </tr>
            `).join('');
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

    # Check duplicate name exclusion current member
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

@app.get("/api/auction/preview")
def preview_auction(type: str, puppet: int):
    queue_col = "gl_queue_position" if type == "GL" else "eo_queue_position"
    available = db.fetchone("SELECT COUNT(*) AS c FROM members")["c"]
    if available == 0:
        return {"rows": []}
    
    puppet_count = min(max(puppet, 1), available)
    rows = db.fetchall(f"SELECT id, name, class_name, {queue_col} as queue_pos FROM members ORDER BY {queue_col}, id LIMIT ?", (puppet_count,))
    return {"rows": rows}

@app.post("/api/auction/commit")
async def commit_auction(data: dict):
    auction_type = data.get("auction_type")
    queue_col = "gl_queue_position" if auction_type == "GL" else "eo_queue_position"
    puppet_count = data.get("puppet_count", 10)
    lnd_total = data.get("lnd_total", 0)
    tns_total = data.get("tns_total", 0)
    participant_ids = set(data.get("participant_ids", []))
    
    available = db.fetchone("SELECT COUNT(*) AS c FROM members")["c"]
    if available == 0:
        raise HTTPException(status_code=400, detail="No members available for auction.")

    target_puppet = min(puppet_count, available)
    if len(participant_ids) != target_puppet:
        raise HTTPException(status_code=400, detail=f"Bidding participants count ({len(participant_ids)}) must match the exact Puppet count ({target_puppet}).")

    lnd_each = lnd_total // target_puppet if target_puppet > 0 else 0
    lnd_left = lnd_total % target_puppet if target_puppet > 0 else 0
    tns_each = tns_total // target_puppet if target_puppet > 0 else 0
    tns_left = tns_total % target_puppet if target_puppet > 0 else 0
    now_str = datetime.now(JAKARTA_TZ).strftime("%Y-%m-%d %H:%M")

    cycle = db.execute(
        """INSERT INTO auction_cycles (auction_type, puppet_count, lnd_total, tns_total, participant_count, lnd_each, lnd_leftover, tns_each, tns_leftover, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (auction_type, target_puppet, lnd_total, tns_total, target_puppet, lnd_each, lnd_left, tns_each, tns_left, now_str)
    )
    cycle_id = cycle.lastrowid

    current_queue = db.fetchall(f"SELECT * FROM members ORDER BY {queue_col}, id")
    
    # Identify who was evaluated in the initial puppet window vs skipped
    initial_window = current_queue[:target_puppet]
    initial_window_ids = {m["id"] for m in initial_window}
    
    skipped_members = [m for m in initial_window if m["id"] not in participant_ids]
    participating_members = [m for m in current_queue if m["id"] in participant_ids]
    untouched_members = [m for m in current_queue if m["id"] not in initial_window_ids and m["id"] not in participant_ids]

    for m in initial_window:
        is_part = m["id"] in participant_ids
        db.execute(
            "INSERT INTO cycle_members (cycle_id, member_name, queue_position_before, participated, lnd_awarded, tns_awarded) VALUES (?, ?, ?, ?, ?, ?)",
            (cycle_id, m["name"], m[queue_col], 1 if is_part else 0, lnd_each if is_part else 0, tns_each if is_part else 0)
        )

    # Reorder queue: Skipped members go to the back, followed by untouched, followed by participants
    new_queue = untouched_members + skipped_members + participating_members
    for pos, member in enumerate(new_queue, start=1):
        db.execute(f"UPDATE members SET {queue_col} = ? WHERE id = ?", (pos, member["id"]))

    await manager.broadcast("refresh")
    return {"status": "success"}

@app.get("/api/history")
def get_history():
    return db.fetchall("SELECT * FROM auction_cycles ORDER BY id DESC")

@app.get("/api/history/{cycle_id}")
def get_cycle_detail(cycle_id: int):
    return db.fetchall("SELECT * FROM cycle_members WHERE cycle_id = ? ORDER BY queue_position_before", (cycle_id,))

@app.put("/api/history/member/{cm_id}")
async def update_cycle_member(cm_id: int, data: dict):
    lnd = data.get("lnd_awarded", 0)
    tns = data.get("tns_awarded", 0)
    db.execute("UPDATE cycle_members SET lnd_awarded = ?, tns_awarded = ? WHERE id = ?", (lnd, tns, cm_id))
    await manager.broadcast("refresh")
    return {"status": "success"}

@app.delete("/api/history/{cycle_id}")
async def delete_cycle(cycle_id: int):
    # Rule 2: Note that deletion endpoint can be protected or verified if needed. 
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
