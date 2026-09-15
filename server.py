import sqlite3
from pathlib import Path
from datetime import datetime
from flask import Flask, render_template_string, request, redirect, url_for

app = Flask(__name__)
app.secret_key = "prestigegaruda_secret_key"

APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "prestigegaruda_web.db"
MAX_MEMBERS = 80
VALID_ROLES = ["Main DPS", "Sub DPS", "Utility", "Bard", "Priest"]

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
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

init_db()

# HTML Template with integrated styling & tab navigation
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>PrestigeGaruda Web Suite</title>
    <style>
        body {
            background-color: #0b0f19;
            color: #d1d5db;
            font-family: 'Segoe UI', Inter, sans-serif;
            margin: 0;
            padding: 20px;
        }
        h1, h2, h3 { color: #00E5FF; }
        .container { max-width: 1300px; margin: auto; }
        .nav-tabs {
            display: flex;
            list-style: none;
            padding: 0;
            border-bottom: 2px solid #1f293d;
            margin-bottom: 20px;
        }
        .nav-tabs li {
            margin-right: 5px;
        }
        .nav-tabs a {
            display: inline-block;
            padding: 10px 20px;
            background-color: #0d1322;
            color: #9ca3af;
            text-decoration: none;
            font-weight: bold;
            border-top-left-radius: 6px;
            border-top-right-radius: 6px;
            border: 1px solid #1f293d;
        }
        .nav-tabs a.active {
            background-color: #162035;
            color: #00E5FF;
            border-bottom: 2px solid #00E5FF;
        }
        .card {
            background-color: #0d1322;
            border: 1px solid #1f293d;
            border-radius: 6px;
            padding: 20px;
            margin-bottom: 20px;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            background-color: #060911;
            border: 1px solid #1f293d;
            margin-top: 10px;
        }
        th, td {
            padding: 10px;
            border: 1px solid #1f293d;
            text-align: left;
            font-size: 13px;
        }
        th {
            background-color: #0d1322;
            color: #00E5FF;
        }
        input, select, textarea {
            background-color: #060911;
            border: 1px solid #1f293d;
            border-radius: 4px;
            color: #ffffff;
            padding: 8px;
            width: 100%;
            box-sizing: border-box;
            margin-top: 5px;
            margin-bottom: 10px;
        }
        button, .btn {
            background-color: #111a2e;
            border: 1px solid #00E5FF;
            color: #00E5FF;
            padding: 8px 16px;
            font-weight: bold;
            border-radius: 4px;
            cursor: pointer;
            text-decoration: none;
            display: inline-block;
        }
        button:hover, .btn:hover {
            background-color: #00E5FF;
            color: #000000;
        }
        .btn-danger {
            border-color: #FF3B30;
            color: #FF3B30;
            background-color: #2a080c;
        }
        .btn-danger:hover {
            background-color: #FF3B30;
            color: #000000;
        }
        .row { display: flex; gap: 20px; }
        .col { flex: 1; }
    </style>
</head>
<body>
<div class="container">
    <h1>🦅 PRESTIGEGARUDA WEB SUITE</h1>
    
    <ul class="nav-tabs">
        <li><a href="/?tab=members" class="{{ 'active' if active_tab == 'members' else '' }}">Members & Queues</a></li>
        <li><a href="/?tab=teams" class="{{ 'active' if active_tab == 'teams' else '' }}">Battlefield Stratagems</a></li>
        <li><a href="/?tab=gl_auction" class="{{ 'active' if active_tab == 'gl_auction' else '' }}">GL Auction</a></li>
        <li><a href="/?tab=eo_auction" class="{{ 'active' if active_tab == 'eo_auction' else '' }}">EO Auction</a></li>
        <li><a href="/?tab=history" class="{{ 'active' if active_tab == 'history' else '' }}">Auction Archives</a></li>
    </ul>

    {% if active_tab == 'members' %}
    <div class="card">
        <h3>Register New Member (Capacity: {{ members|length }} / 80)</h3>
        <form method="POST" action="/member/add">
            <div class="row">
                <div class="col"><label>Character Name:</label><input type="text" name="name" required></div>
                <div class="col"><label>Class:</label><input type="text" name="character_class"></div>
                <div class="col"><label>Role:</label>
                    <select name="role">
                        {% for r in valid_roles %}
                        <option value="{{ r }}">{{ r }}</option>
                        {% endfor %}
                    </select>
                </div>
            </div>
            <div><label>Notes:</label><textarea name="notes" rows="2"></textarea></div>
            <button type="submit">+ Register Member</button>
        </form>
    </div>

    <div class="card">
        <h3>Prestigegaruda Roster</h3>
        <table>
            <tr>
                <th>GL Queue</th><th>EO Queue</th><th>Name</th><th>Class</th><th>Role</th><th>Notes</th><th>Actions</th>
            </tr>
            {% for m in members %}
            <tr>
                <td style="color: #00E5FF; font-weight: bold;">{{ m.gl_queue_position }}</td>
                <td style="color: #00E5FF; font-weight: bold;">{{ m.eo_queue_position }}</td>
                <td>{{ m.name }}</td>
                <td>{{ m.character_class }}</td>
                <td>{{ m.role }}</td>
                <td>{{ m.notes }}</td>
                <td>
                    <a href="/member/delete/{{ m.id }}" class="btn btn-danger" onclick="return confirm('Purge member?');">Purge</a>
                </td>
            </tr>
            {% endfor %}
        </table>
    </div>

    {% elif active_tab == 'teams' %}
    <div class="card">
        <h3>🏆 Guild League Battlefield Deployments (Teams 01-16)</h3>
        <form method="POST" action="/teams/save">
            <table>
                <tr>
                    <th>Battlefield</th>
                    <th>Team</th>
                    <th>Main DPS</th>
                    <th>Sub DPS</th>
                    <th>Utility</th>
                    <th>Bard</th>
                    <th>Priest (FS)</th>
                </tr>
                {% for t in teams %}
                <tr>
                    <td style="color: {{ '#FF3B30' if t.battlefield_type == 'Main' else '#FFD700' }}; font-weight: bold;">
                        ⚔️ {{ t.battlefield_type.upper() }} BF
                    </td>
                    <td style="color: #00E5FF; font-weight: bold;">🔥 Team {{ "%02d"|format(t.team_number) }}</td>
                    
                    <td>
                        <select name="team_{{ t.team_number }}_main_dps">
                            <option value="">--- VACANT ---</option>
                            {% for m in pools.Main_DPS %}
                            <option value="{{ m.id }}" {{ 'selected' if t.slot_main_dps == m.id else '' }}>{{ m.name }}</option>
                            {% endfor %}
                        </select>
                    </td>
                    <td>
                        <select name="team_{{ t.team_number }}_sub_dps">
                            <option value="">--- VACANT ---</option>
                            {% for m in pools.Sub_DPS %}
                            <option value="{{ m.id }}" {{ 'selected' if t.slot_sub_dps == m.id else '' }}>{{ m.name }}</option>
                            {% endfor %}
                        </select>
                    </td>
                    <td>
                        <select name="team_{{ t.team_number }}_utility">
                            <option value="">--- VACANT ---</option>
                            {% for m in pools.Utility %}
                            <option value="{{ m.id }}" {{ 'selected' if t.slot_utility == m.id else '' }}>{{ m.name }}</option>
                            {% endfor %}
                        </select>
                    </td>
                    <td>
                        <select name="team_{{ t.team_number }}_bard">
                            <option value="">--- VACANT ---</option>
                            {% for m in pools.Bard %}
                            <option value="{{ m.id }}" {{ 'selected' if t.slot_bard == m.id else '' }}>{{ m.name }}</option>
                            {% endfor %}
                        </select>
                    </td>
                    <td>
                        <select name="team_{{ t.team_number }}_fs">
                            <option value="">--- VACANT ---</option>
                            {% for m in pools.Priest %}
                            <option value="{{ m.id }}" {{ 'selected' if t.slot_fs == m.id else '' }}>{{ m.name }}</option>
                            {% endfor %}
                        </select>
                    </td>
                </tr>
                {% endfor %}
            </table>
            <br>
            <button type="submit">🔒 Commit All Deployments</button>
        </form>
    </div>

    {% elif active_tab in ['gl_auction', 'eo_auction'] %}
    {% set atype = 'GL' if active_tab == 'gl_auction' else 'EO' %}
    <div class="card">
        <h3>🦅 {{ atype }} Auction Matrix Configurator</h3>
        <form method="POST" action="/auction/preview">
            <input type="hidden" name="auction_type" value="{{ atype }}">
            <div class="row">
                <div class="col"><label>Puppet Count (Selected Members):</label><input type="number" name="puppet_count" value="10" min="1" max="80"></div>
                <div class="col"><label>Light-Dark (LND) Pool:</label><input type="number" name="lnd_total" value="0" min="0"></div>
                <div class="col"><label>Time-Space (TNS) Pool:</label><input type="number" name="tns_total" value="0" min="0"></div>
            </div>
            <button type="submit">⚡ Preview Auction Board</button>
        </form>
    </div>

    {% elif active_tab == 'history' %}
    <div class="card">
        <h3>📜 Auction Archives</h3>
        <table>
            <tr>
                <th>Cycle ID</th><th>Type</th><th>Puppet</th><th>LND Total</th><th>TNS Total</th><th>Participants</th><th>LND Each</th><th>TNS Each</th><th>Timestamp</th>
            </tr>
            {% for c in cycles %}
            <tr>
                <td>{{ c.id }}</td>
                <td style="color: #00E5FF; font-weight: bold;">{{ c.auction_type }}</td>
                <td>{{ c.puppet_count }}</td>
                <td>{{ c.lnd_total }}</td>
                <td>{{ c.tns_total }}</td>
                <td>{{ c.participant_count }}</td>
                <td>{{ c.lnd_each }}</td>
                <td>{{ c.tns_each }}</td>
                <td>{{ c.created_at }}</td>
            </tr>
            {% endfor %}
        </table>
    </div>
    {% endif %}
</div>
</body>
</html>
"""

@app.route("/")
def index():
    active_tab = request.args.get("tab", "members")
    conn = get_db()
    
    members = conn.execute("SELECT * FROM members ORDER BY gl_queue_position, id").fetchall()
    cycles = conn.execute("SELECT * FROM auction_cycles ORDER BY id DESC").fetchall()
    teams = conn.execute("SELECT * FROM league_teams ORDER BY team_number ASC").fetchall()
    
    pools = {
        "Main_DPS": conn.execute("SELECT id, name FROM members WHERE role = 'Main DPS' ORDER BY name").fetchall(),
        "Sub_DPS": conn.execute("SELECT id, name FROM members WHERE role = 'Sub DPS' ORDER BY name").fetchall(),
        "Utility": conn.execute("SELECT id, name FROM members WHERE role = 'Utility' ORDER BY name").fetchall(),
        "Bard": conn.execute("SELECT id, name FROM members WHERE role = 'Bard' ORDER BY name").fetchall(),
        "Priest": conn.execute("SELECT id, name FROM members WHERE role = 'Priest' ORDER BY name").fetchall(),
    }
    
    conn.close()
    return render_template_string(
        HTML_TEMPLATE,
        active_tab=active_tab,
        members=members,
        cycles=cycles,
        teams=teams,
        pools=pools,
        valid_roles=VALID_ROLES
    )

@app.route("/member/add", methods=["POST"])
def add_member():
    name = request.form.get("name", "").strip()
    char_class = request.form.get("character_class", "").strip()
    role = request.form.get("role", "").strip()
    notes = request.form.get("notes", "").strip()
    
    if name:
        conn = get_db()
        count = conn.execute("SELECT COUNT(*) AS c FROM members").fetchone()["c"]
        if count < MAX_MEMBERS:
            gl_pos = conn.execute("SELECT COALESCE(MAX(gl_queue_position), 0) AS p FROM members").fetchone()["p"] + 1
            eo_pos = conn.execute("SELECT COALESCE(MAX(eo_queue_position), 0) AS p FROM members").fetchone()["p"] + 1
            conn.execute(
                "INSERT INTO members (name, character_class, role, notes, gl_queue_position, eo_queue_position, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (name, char_class, role, notes, gl_pos, eo_pos, datetime.now().strftime("%Y-%m-%d %H:%M"))
            )
            conn.commit()
        conn.close()
    return redirect(url_for("index", tab="members"))

@app.route("/member/delete/<int:member_id>")
def delete_member(member_id):
    conn = get_db()
    conn.execute("UPDATE league_teams SET slot_main_dps = NULL WHERE slot_main_dps = ?", (member_id,))
    conn.execute("UPDATE league_teams SET slot_sub_dps = NULL WHERE slot_sub_dps = ?", (member_id,))
    conn.execute("UPDATE league_teams SET slot_utility = NULL WHERE slot_utility = ?", (member_id,))
    conn.execute("UPDATE league_teams SET slot_bard = NULL WHERE slot_bard = ?", (member_id,))
    conn.execute("UPDATE league_teams SET slot_fs = NULL WHERE slot_fs = ?", (member_id,))
    
    conn.execute("DELETE FROM members WHERE id = ?", (member_id,))
    
    for q_col in ["gl_queue_position", "eo_queue_position"]:
        remaining = conn.execute(f"SELECT id FROM members ORDER BY {q_col}, id").fetchall()
        for pos, row in enumerate(remaining, start=1):
            conn.execute(f"UPDATE members SET {q_col} = ? WHERE id = ?", (pos, row["id"]))
            
    conn.commit()
    conn.close()
    return redirect(url_for("index", tab="members"))

@app.route("/teams/save", methods=["POST"])
def save_teams():
    conn = get_db()
    for i in range(1, 17):
        m_dps = request.form.get(f"team_{i}_main_dps") or None
        s_dps = request.form.get(f"team_{i}_sub_dps") or None
        util = request.form.get(f"team_{i}_utility") or None
        bard = request.form.get(f"team_{i}_bard") or None
        fs = request.form.get(f"team_{i}_fs") or None
        
        conn.execute("""
            UPDATE league_teams 
            SET slot_main_dps = ?, slot_sub_dps = ?, slot_utility = ?, slot_bard = ?, slot_fs = ?
            WHERE team_number = ?
        """, (m_dps, s_dps, util, bard, fs, i))
    conn.commit()
    conn.close()
    return redirect(url_for("index", tab="teams"))

@app.route("/auction/preview", methods=["POST"])
def preview_auction():
    atype = request.form.get("auction_type")
    puppet_count = int(request.form.get("puppet_count", 1))
    lnd_total = int(request.form.get("lnd_total", 0))
    tns_total = int(request.form.get("tns_total", 0))
    
    conn = get_db()
    queue_col = "gl_queue_position" if atype == "GL" else "eo_queue_position"
    members = conn.execute(f"SELECT * FROM members ORDER BY {queue_col}, id LIMIT ?", (puppet_count,)).fetchall()
    
    p_count = len(members)
    if p_count > 0:
        lnd_each = lnd_total // p_count
        lnd_left = lnd_total % p_count
        tns_each = tns_total // p_count
        tns_left = tns_total % p_count
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        
        cycle = conn.execute(
            """INSERT INTO auction_cycles
            (auction_type, puppet_count, lnd_total, tns_total, participant_count, lnd_each, lnd_leftover, tns_each, tns_leftover, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (atype, p_count, lnd_total, tns_total, p_count, lnd_each, lnd_left, tns_each, tns_left, now_str)
        )
        cycle_id = cycle.lastrowid
        
        for m in members:
            conn.execute(
                "INSERT INTO cycle_members (cycle_id, member_name, queue_position_before, participated, lnd_awarded, tns_awarded) VALUES (?, ?, ?, 1, ?, ?)",
                (cycle_id, m["name"], m[queue_col], lnd_each, tns_each)
            )
            
        # Rotate Queue: Move participating members to the back
        all_members = conn.execute(f"SELECT * FROM members ORDER BY {queue_col}, id").fetchall()
        part_ids = {m["id"] for m in members}
        skipped = [m for m in all_members if m["id"] not in part_ids]
        participated = [m for m in all_members if m["id"] in part_ids]
        new_queue = skipped + participated
        
        for pos, m in enumerate(new_queue, start=1):
            conn.execute(f"UPDATE members SET {queue_col} = ? WHERE id = ?", (pos, m["id"]))
            
        conn.commit()
    conn.close()
    return redirect(url_for("index", tab="history"))

import os

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
