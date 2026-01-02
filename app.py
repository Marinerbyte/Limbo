import threading
import sqlite3
import json
import random
import time
import ssl
import os
import psycopg2
import websocket # pip install websocket-client
from flask import Flask, render_template_string, jsonify

# ==============================================================================
# 1. CONFIGURATION (ISKO EDIT KARO)
# ==============================================================================
CONFIG = {
    'WS_URL': "wss://chatp.net:5333/server",
    'BOT_NAME': "delvina",      # <-- Apna Bot Name Yahan Likho
    'BOT_PASS': "p99665",      # <-- Apna Bot Password Yahan Likho
    'ROOM': "قهوهـ_🇸🇦_السعـوديـه",         # <-- Room Name (Spelling Sahi Rakhna)
    'DB_FILE': "limbo_game.db"     # Local testing ke liye
}

# IMAGES (Maine sample daal diye hain, baad mein change kar lena)
ASSETS = {
    'start': "https://i.imgur.com/HuK5jC7.gif",   # Game Start
    'jump': "https://i.imgur.com/5wQJv8x.gif",    # Jump Action
    'duck': "https://i.imgur.com/lJtT1sD.gif",    # Duck Action
    'crash': "https://i.imgur.com/pXv9q1r.gif",   # Fail/Crash
    'win':  "https://i.imgur.com/Hw7qj9W.gif"     # Win
}

app = Flask(__name__)
db_lock = threading.Lock()

# ==============================================================================
# 2. DATABASE SYSTEM (Render Compatible)
# ==============================================================================
def get_db_connection():
    # Render ka URL check karega
    database_url = os.environ.get('DATABASE_URL')
    if database_url:
        try:
            conn = psycopg2.connect(database_url, sslmode='require')
            return conn, "pg"
        except:
            return sqlite3.connect(CONFIG['DB_FILE']), "sqlite"
    else:
        return sqlite3.connect(CONFIG['DB_FILE']), "sqlite"

def init_db():
    conn, db_type = get_db_connection()
    c = conn.cursor()
    ph = "SERIAL" if db_type == "pg" else "INTEGER"
    
    # Table banao agar nahi hai
    query = f'''CREATE TABLE IF NOT EXISTS users 
               (username TEXT PRIMARY KEY, score INTEGER DEFAULT 1000, avatar TEXT, wins INTEGER DEFAULT 0)'''
    c.execute(query)
    conn.commit()
    conn.close()

def get_user_data(username, avatar_url=None):
    with db_lock:
        conn, db_type = get_db_connection()
        c = conn.cursor()
        ph = "%s" if db_type == "pg" else "?"
        
        c.execute(f"SELECT score, wins FROM users WHERE username={ph}", (username,))
        row = c.fetchone()
        
        if not row:
            c.execute(f"INSERT INTO users (username, score, avatar) VALUES ({ph}, {ph}, {ph})", 
                      (username, 1000, avatar_url))
            conn.commit()
            val = (1000, 0)
        else:
            if avatar_url:
                c.execute(f"UPDATE users SET avatar={ph} WHERE username={ph}", (avatar_url, username))
                conn.commit()
            val = (row[0], row[1])
        conn.close()
        return val

def update_score(username, amount, win=False):
    with db_lock:
        conn, db_type = get_db_connection()
        c = conn.cursor()
        ph = "%s" if db_type == "pg" else "?"
        win_inc = 1 if win else 0
        
        c.execute(f"UPDATE users SET score = score + {ph}, wins = wins + {ph} WHERE username={ph}", 
                  (amount, win_inc, username))
        conn.commit()
        
        c.execute(f"SELECT score FROM users WHERE username={ph}", (username,))
        new_score = c.fetchone()[0]
        conn.close()
        return new_score

def get_leaderboard_data():
    with db_lock:
        conn, db_type = get_db_connection()
        if db_type == "sqlite": conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        c.execute("SELECT username, score, avatar, wins FROM users ORDER BY score DESC LIMIT 50")
        
        if db_type == "pg":
            cols = [desc[0] for desc in c.description]
            results = [dict(zip(cols, row)) for row in c.fetchall()]
        else:
            results = [dict(row) for row in c.fetchall()]
        conn.close()
        return results

# ==============================================================================
# 3. GAME LOGIC
# ==============================================================================
class LimboSession:
    def __init__(self, user, bet_amount=0):
        self.user = user
        self.bet = int(bet_amount)
        self.mode = 'bet' if self.bet > 0 else 'normal'
        self.round = 1
        self.height = 0
        self.obstacle = None 
    
    def next_round(self):
        self.height = random.randint(20, 120)
        if self.height < 60:
            self.obstacle = 'low'
            return f"⚠️ BAR IS LOW ({self.height}cm)! SPIKES ON FLOOR! (Type: jump)"
        else:
            self.obstacle = 'high'
            return f"⚠️ BAR IS HIGH ({self.height}cm)! LASER OVERHEAD! (Type: duck)"

    def check(self, action):
        action = action.lower()
        survived = False
        if self.obstacle == 'low' and 'jump' in action: survived = True
        if self.obstacle == 'high' and 'duck' in action: survived = True
        
        reward = self.bet if self.mode == 'bet' else (10 * self.round)
        if not survived: 
            loss = -self.bet if self.mode == 'bet' else 0
            return False, loss
        return True, reward

active_sessions = {}

# ==============================================================================
# 4. BOT CLIENT
# ==============================================================================
class GameBot:
    def __init__(self):
        self.ws = None
    
    def send(self, txt, img=None):
        payload = {"handler": "message", "message": txt}
        if img: payload['image'] = img
        if self.ws: 
            try: self.ws.send(json.dumps(payload))
            except: pass

    def run(self):
        while True:
            try:
                self.ws = websocket.WebSocketApp(
                    CONFIG['WS_URL'],
                    on_open=self.on_open,
                    on_message=self.on_message,
                    on_error=self.on_error
                )
                self.ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE})
            except Exception as e:
                print(f"Reconnecting... {e}")
                time.sleep(5)

    def on_open(self, ws):
        print(">> BOT CONNECTED")
        ws.send(json.dumps({"handler": "login", "username": CONFIG['BOT_NAME'], "password": CONFIG['BOT_PASS']}))
        threading.Thread(target=self.ping, args=(ws,), daemon=True).start()

    def ping(self, ws):
        while ws.sock and ws.sock.connected:
            try:
                ws.send(json.dumps({"handler": "ping"}))
                time.sleep(20)
            except: break

    def on_message(self, ws, message):
        try:
            data = json.loads(message)
            if data.get('handler') == "login_event" and data.get('type') == "success":
                ws.send(json.dumps({"handler": "room_join", "name": CONFIG['ROOM']}))
            
            if data.get('type') == "text":
                self.handle_chat(data)
        except: pass

    def on_error(self, ws, error):
        print("Error:", error)

    def handle_chat(self, data):
        user = data.get('username') or data.get('from')
        msg = data.get('body', '').strip().lower()
        avatar = data.get('icon') or data.get('avatar_url')
        
        if not user: return

        # Avatar Update
        if avatar: get_user_data(user, avatar)

        # --- GAME COMMANDS ---
        if msg.startswith("limbo") or msg.startswith("!limbo"):
            if user in active_sessions:
                self.send(f"@{user} finish current game first! (Type 'jump' or 'duck')")
                return
            
            bet = 0
            if "#" in msg:
                try: bet = int(msg.split("#")[1])
                except: return self.send(f"@{user} Invalid format! Use: limbo#50")
            
            bal, _ = get_user_data(user)
            if bet > bal:
                return self.send(f"@{user} ❌ BROKE! You have {bal} pts. Earn first!")
            if bet < 0: return 

            sess = LimboSession(user, bet)
            active_sessions[user] = sess
            desc = sess.next_round()
            mode_str = f"RISK: {bet} PTS" if bet > 0 else "NORMAL MODE"
            
            self.send(f"🎮 START: @{user} | {mode_str}\n{desc}", ASSETS['start'])

        elif user in active_sessions and (msg == "jump" or msg == "duck"):
            sess = active_sessions[user]
            alive, points = sess.check(msg)
            
            new_score = update_score(user, points, win=alive)
            
            if alive:
                sess.round += 1
                desc = sess.next_round()
                pic = ASSETS['jump'] if msg == 'jump' else ASSETS['duck']
                self.send(f"✅ ROUND {sess.round-1} CLEARED! (+{points})\n{desc}", pic)
            else:
                del active_sessions[user]
                res = f"LOST {abs(points)}" if points < 0 else "GAME OVER"
                self.send(f"💥 CRASH! @{user} {res}. Total: {new_score}", ASSETS['crash'])

# ==============================================================================
# 5. UI LEADERBOARD (NEON THEME)
# ==============================================================================
HTML_UI = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>TITAN RANKINGS</title>
    <link href="https://fonts.googleapis.com/css2?family=Rajdhani:wght@500;700&family=Orbitron:wght@500;700;900&display=swap" rel="stylesheet">
    <style>
        :root { --bg: #030305; --card-bg: rgba(20, 25, 35, 0.6); --neon-blue: #00f3ff; --gold: #ffd700; --silver: #e0e0e0; --bronze: #cd7f32; }
        body { background: var(--bg); margin: 0; padding: 0; font-family: 'Rajdhani', sans-serif; color: #fff; height: 100vh; overflow: hidden; display: flex; flex-direction: column; }
        .grid-bg { position: fixed; inset: 0; z-index: -1; background-image: linear-gradient(rgba(0, 243, 255, 0.03) 1px, transparent 1px), linear-gradient(90deg, rgba(0, 243, 255, 0.03) 1px, transparent 1px); background-size: 40px 40px; transform: perspective(500px) rotateX(20deg); }
        .header { padding: 20px; text-align: center; border-bottom: 1px solid rgba(0, 243, 255, 0.2); background: rgba(0,0,0,0.8); backdrop-filter: blur(10px); z-index: 10; }
        .title { font-family: 'Orbitron'; font-size: 28px; margin: 0; letter-spacing: 4px; background: linear-gradient(to right, #fff, var(--neon-blue)); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        .lb-container { flex: 1; position: relative; max-width: 600px; width: 100%; margin: 0 auto; overflow-y: auto; padding: 20px 10px; }
        .player-card { position: absolute; left: 10px; right: 10px; height: 70px; background: var(--card-bg); border: 1px solid rgba(255,255,255,0.1); border-left: 4px solid #444; border-radius: 8px; display: flex; align-items: center; padding: 0 15px; transition: top 0.6s cubic-bezier(0.34, 1.56, 0.64, 1); backdrop-filter: blur(10px); }
        .rank-box { width: 40px; font-size: 24px; font-weight: bold; font-family: 'Orbitron'; color: #666; text-align: center; }
        .avatar { width: 45px; height: 45px; border-radius: 50%; border: 2px solid rgba(255,255,255,0.2); margin: 0 15px; object-fit: cover; }
        .crown { position: absolute; top: -12px; left: 50%; transform: translateX(-50%); font-size: 18px; display: none; filter: drop-shadow(0 0 5px gold); }
        .p-info { flex: 1; } .p-name { font-size: 18px; font-weight: 700; } .p-stat { font-size: 12px; color: var(--neon-blue); }
        .p-score { font-family: 'Orbitron'; font-size: 22px; font-weight: bold; }
        .rank-1 { border-left-color: var(--gold); background: linear-gradient(90deg, rgba(255, 215, 0, 0.1), transparent); }
        .rank-1 .rank-box { color: var(--gold); text-shadow: 0 0 10px var(--gold); }
        .rank-1 .crown { display: block; animation: float 2s infinite ease-in-out; }
        .rank-2 { border-left-color: var(--silver); } .rank-2 .rank-box { color: var(--silver); }
        .rank-3 { border-left-color: var(--bronze); } .rank-3 .rank-box { color: var(--bronze); }
        @keyframes float { 0%, 100% { transform: translate(-50%, 0); } 50% { transform: translate(-50%, -5px); } }
    </style>
</head>
<body>
    <div class="grid-bg"></div>
    <div class="header"><h1 class="title">LIMBO LEGENDS</h1></div>
    <div class="lb-container" id="board"><div style="text-align:center; margin-top:50px; color:#00f3ff">CONNECTING...</div></div>
    <script>
        const CARD_HEIGHT = 80; const board = document.getElementById('board'); let currentData = {};
        async function update() {
            try {
                const res = await fetch('/api/scores'); const users = await res.json();
                if(document.querySelector('.lb-container div').innerText === "CONNECTING...") board.innerHTML = "";
                users.forEach((u, i) => {
                    let card = currentData[u.username];
                    let rClass = i===0?'rank-1':i===1?'rank-2':i===2?'rank-3':'';
                    let av = u.avatar || `https://ui-avatars.com/api/?name=${u.username}&background=0D8ABC&color=fff`;
                    if (!card) {
                        card = document.createElement('div'); card.className = `player-card ${rClass}`;
                        card.style.top = `${i * CARD_HEIGHT}px`;
                        card.innerHTML = `<div class="rank-box">#${i+1}</div><div style="position:relative; margin:0 15px;"><div class="crown">👑</div><img src="${av}" class="avatar"></div><div class="p-info"><div class="p-name">${u.username}</div><div class="p-stat">Wins: ${u.wins}</div></div><div class="p-score">${u.score}</div>`;
                        board.appendChild(card); currentData[u.username] = card;
                    } else {
                        card.style.top = `${i * CARD_HEIGHT}px`; card.className = `player-card ${rClass}`;
                        card.querySelector('.p-score').innerText = u.score;
                        card.querySelector('.rank-box').innerText = `#${i+1}`;
                        card.querySelector('.p-stat').innerText = `Wins: ${u.wins}`;
                        if(u.avatar) card.querySelector('.avatar').src = u.avatar;
                    }
                });
                board.style.height = `${users.length * CARD_HEIGHT + 50}px`;
            } catch(e) {}
        }
        setInterval(update, 2000); update();
    </script>
</body>
</html>
"""

@app.route('/')
def index(): return render_template_string(HTML_UI)

@app.route('/api/scores')
def scores(): return jsonify(get_leaderboard_data())

# ==============================================================================
# 6. SERVER START (YAHAN SE MAGIC SHURU HOTA HAI)
# ==============================================================================
# Ye code 'if __name__' se BAHAR hai, taaki Gunicorn isko turant chala de
init_db()
bot = GameBot()
t = threading.Thread(target=bot.run, daemon=True)
t.start()

if __name__ == '__main__':
    # Ye sirf PC pe chalane ke liye hai
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)