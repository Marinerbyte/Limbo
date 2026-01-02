import threading
import sqlite3
import json
import time
import random
import string
import ssl
from flask import Flask, render_template_string, request, jsonify
import websocket # pip install websocket-client

# ==============================================================================
# 1. CONFIGURATION
# ==============================================================================
GAME_IMAGES = {
    "idle":     "https://www.dropbox.com/scl/fi/dk23zdtm3oy5czo7qvwjz/1767368972763.jpg?rlkey=kfeweu26q3x3po8jumkfp2pve&st=0tz0m5oi&raw=1",
    "low_bar":  "https://www.dropbox.com/scl/fi/frufp2y2b686dx9cs6agy/1767369081606.jpg?rlkey=pxp6dx3g0b1fz7zhzd93zizkx&st=hc2x1rzq&raw=1",
    "high_bar": "https://www.dropbox.com/scl/fi/h2emqah824pnbhl2pg71n/1767369091032.jpg?rlkey=58cogoxo25548kxm44ze77pi0&st=jd1p0wjo&raw=1",
    "win":      "https://www.dropbox.com/scl/fi/jlzf075rcdfpy8n7ze248/1767369132611.jpg?rlkey=l2l9weyf1go1d0wtroath7l12&st=5kuvj88c&raw=1",
    "fail":     "https://www.dropbox.com/scl/fi/6j5quie12zht1xo8lnco7/1767369209244.jpg?rlkey=dea28h9h41zmovh9lpyntgopc&st=42s6ny8u&raw=1"
}

DB_FILE = "titan_limbo.db"
WS_URL = "wss://chatp.net:5333/server" 

# ==============================================================================
# 2. DATABASE ENGINE
# ==============================================================================
db_lock = threading.Lock()

def init_db():
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS players 
                     (username TEXT PRIMARY KEY, score INTEGER, avatar TEXT, wins INTEGER DEFAULT 0)''')
        conn.commit()
        conn.close()

init_db()

def update_player(user, score_change, avatar_url=None, is_win=False):
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT score, wins FROM players WHERE username=?", (user,))
        row = c.fetchone()
        
        win_add = 1 if is_win else 0
        
        if row:
            new_score = row[0] + score_change
            new_wins = row[1] + win_add
            if avatar_url:
                c.execute("UPDATE players SET score=?, wins=?, avatar=? WHERE username=?", (new_score, new_wins, avatar_url, user))
            else:
                c.execute("UPDATE players SET score=?, wins=? WHERE username=?", (new_score, new_wins, user))
        else:
            # New Player joins with 1000, but wins are 0
            start_score = 1000 + score_change
            av = avatar_url if avatar_url else f"https://ui-avatars.com/api/?name={user}&background=random"
            c.execute("INSERT INTO players (username, score, avatar, wins) VALUES (?, ?, ?, ?)", 
                      (user, start_score, av, win_add))
        conn.commit()
        conn.close()

def get_leaderboard_data():
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        # FIX: Only show players who have won at least 1 round (wins > 0)
        c.execute("SELECT * FROM players WHERE wins > 0 ORDER BY score DESC LIMIT 50")
        rows = [dict(row) for row in c.fetchall()]
        conn.close()
        return rows

def get_balance(user):
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT score FROM players WHERE username=?", (user,))
        row = c.fetchone()
        conn.close()
        return row[0] if row else 1000

# ==============================================================================
# 3. GAME BOT LOGIC (PROTOCOL FIXED FROM TANVAR.PY)
# ==============================================================================
def gen_random_str(length):
    chars = "abcdefghijklmnopqrstuvwxyz0123456789"
    return ''.join(random.choice(chars) for i in range(length))

class LimboBot:
    def __init__(self):
        self.ws = None
        self.active = False
        self.creds = {}
        self.sessions = {} 

    def start_bot(self, u, p, r):
        self.creds = {"u": u, "p": p, "r": r}
        self.active = True
        t = threading.Thread(target=self.run_socket)
        t.daemon = True
        t.start()

    def run_socket(self):
        while self.active:
            try:
                # SSL Bypass added similar to tanvar script logic (via sslopt)
                self.ws = websocket.WebSocketApp(WS_URL,
                    on_open=self.on_open,
                    on_message=self.on_message,
                    on_error=self.on_error,
                    on_close=self.on_close)
                
                self.ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE})
                time.sleep(5)
            except Exception as e:
                print(f"Connection Error: {e}")
                time.sleep(5)

    def on_open(self, ws):
        print("[BOT] Connected. Sending Login...")
        # LOGIN PAYLOAD (Standard)
        ws.send(json.dumps({
            "handler": "login",
            "username": self.creds['u'],
            "password": self.creds['p'],
            "id": gen_random_str(20)
        }))

    def on_close(self, ws, code, msg):
        print("[BOT] Disconnected")

    def on_error(self, ws, error):
        print(f"[BOT] Error: {error}")

    # --- FIX: SEND MESSAGE FUNCTION UPDATED ---
    def send(self, msg, img=None):
        if not self.ws: return
        
        # Based on tanvar.py structure for 'room_message'
        payload = {
            "handler": "room_message",
            "id": gen_random_str(20),
            "room": self.creds['r'],
            "type": "image" if img else "text",
            "body": msg,      # Text goes here
            "url": img if img else "", # Image URL goes here
            "length": ""
        }
        
        try:
            self.ws.send(json.dumps(payload))
        except Exception as e:
            print(f"Send Error: {e}")

    def on_message(self, ws, message):
        if not self.active: return
        try:
            data = json.loads(message)
            handler = data.get('handler')

            # Login Success -> Join Room
            if handler == 'login_event' and data.get('type') == 'success':
                print(f"[BOT] Login Success! Joining {self.creds['r']}...")
                ws.send(json.dumps({
                    "handler": "room_join", 
                    "id": gen_random_str(20),
                    "name": self.creds['r']
                }))
            
            # Chat Message Handling
            if handler == 'room_message' or data.get('type') in ['text', 'image']:
                self.process_game_logic(data)
                
        except: pass

    def process_game_logic(self, data):
        # Extract data robustly
        user = data.get('username') or data.get('from')
        if not user or user == self.creds['u']: return # Don't reply to self
        
        raw_txt = data.get('body') or data.get('text') or ''
        txt = str(raw_txt).lower().strip()
        avatar = data.get('avatar_url') or data.get('icon') or ""

        # --- GAME COMMANDS ---
        if txt.startswith("limbo"):
            if user in self.sessions:
                self.send(f"@{user} You are already playing! Type 'jump' or 'duck'.")
                return
            
            bet = 0
            if "#" in txt:
                try:
                    bet = int(txt.split("#")[1])
                    if bet < 1: return
                except: return

            bal = get_balance(user)
            if bet > bal:
                self.send(f"@{user} Insufficient funds! You have {bal}.")
                return

            # Note: We do NOT update wins here, just initialize session
            update_player(user, 0, avatar, is_win=False)

            self.sessions[user] = {
                "round": 1, 
                "bet": bet, 
                "h": random.randint(10, 90)
            }
            self.announce_round(user)

        elif user in self.sessions and txt in ["jump", "duck"]:
            self.handle_move(user, txt, avatar)

        # Help Commands
        elif txt == "!bal":
            bal = get_balance(user)
            self.send(f"💰 @{user} Balance: {bal}")
        
        elif txt == "!board":
             self.send(f"🏆 Check Leaderboard: {request.host_url}leaderboard")

    def announce_round(self, user):
        s = self.sessions[user]
        h = s['h']
        if h < 50:
            action = "JUMP"
            img = GAME_IMAGES['low_bar']
            hint = "BAR IS LOW ⬇️"
        else:
            action = "DUCK"
            img = GAME_IMAGES['high_bar']
            hint = "BAR IS HIGH ⬆️"
            
        msg = f"🔵 {user} | Round {s['round']} | H: {h}cm | Bet: {s['bet']}\n⚠️ {hint} -> Type '{action}'"
        self.send(msg, img)

    def handle_move(self, user, move, avatar):
        s = self.sessions[user]
        req = "jump" if s['h'] < 50 else "duck"
        
        if move == req:
            # WIN
            profit = s['bet'] if s['bet'] > 0 else 100
            # Mark is_win=True to show on leaderboard
            update_player(user, profit, avatar, is_win=True)
            
            self.send(f"✅ PASSED! +{profit}", GAME_IMAGES['win'])
            
            s['round'] += 1
            s['h'] = random.randint(10, 90)
            time.sleep(2)
            self.announce_round(user)
        else:
            # LOSE
            loss = s['bet'] if s['bet'] > 0 else 0
            update_player(user, -loss, avatar, is_win=False)
            
            bal = get_balance(user)
            self.send(f"❌ CRASH! Lost {loss}. Balance: {bal}", GAME_IMAGES['fail'])
            del self.sessions[user]

bot = LimboBot()

# ==============================================================================
# 4. FLASK SERVER
# ==============================================================================
app = Flask(__name__)

LOGIN_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>TITAN LOGIN</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { background: #000; display: flex; justify-content: center; align-items: center; height: 100vh; color: #0ff; font-family: monospace; margin: 0; }
        .box { border: 2px solid #0ff; padding: 20px; text-align: center; box-shadow: 0 0 30px #0ff; max-width: 90%; }
        input { background: #111; border: 1px solid #066; padding: 10px; width: 90%; color: #fff; margin: 5px 0; }
        button { background: #0ff; color: #000; padding: 10px 20px; border: none; font-weight: bold; cursor: pointer; width: 100%; margin-top: 10px; }
    </style>
</head>
<body>
    <div class="box">
        <h1>🤖 LIMBO SYSTEM</h1>
        <form action="/start" method="post">
            <input name="u" placeholder="Bot Username" required>
            <input name="p" type="password" placeholder="Bot Password" required>
            <input name="r" placeholder="Target Room" required>
            <button>LAUNCH BOT</button>
        </form>
        <br><a href="/leaderboard" target="_blank" style="color:#fff">VIEW LEADERBOARD</a>
    </div>
</body>
</html>
"""

LEADERBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Limbo Champions</title>
    <link href="https://fonts.googleapis.com/css2?family=Fredoka+One&family=Nunito:wght@700&display=swap" rel="stylesheet">
    <style>
        :root { --bg-dark: #0f0c29; --card-glass: rgba(255, 255, 255, 0.05); --neon-cyan: #00f3ff; --neon-pink: #ff00cc; --gold: #ffd700; }
        body { margin: 0; background: linear-gradient(135deg, #0f0c29, #302b63, #24243e); color: #fff; font-family: 'Nunito', sans-serif; min-height: 100vh; }
        .header { text-align: center; padding: 40px 20px; font-family: 'Fredoka One', cursive; }
        h1 { font-size: 3rem; margin: 0; background: linear-gradient(to right, var(--neon-cyan), var(--neon-pink)); -webkit-background-clip: text; -webkit-text-fill-color: transparent; filter: drop-shadow(0 0 10px rgba(0,243,255,0.5)); animation: float 3s infinite; }
        .container { max-width: 600px; margin: 0 auto; padding: 0 20px 50px 20px; perspective: 1000px; }
        .player-card { display: flex; align-items: center; background: var(--card-glass); backdrop-filter: blur(10px); margin-bottom: 15px; padding: 15px; border-radius: 20px; position: relative; border: 2px solid transparent; background-clip: padding-box; box-shadow: 0 10px 30px rgba(0,0,0,0.3); transition: transform 0.3s; transform-style: preserve-3d; }
        .player-card::after { content: ''; position: absolute; top: -2px; left: -2px; right: -2px; bottom: -2px; background: linear-gradient(45deg, var(--neon-pink), var(--neon-cyan)); z-index: -1; border-radius: 20px; }
        .player-card:hover { transform: translateZ(20px) scale(1.02); box-shadow: 0 20px 40px rgba(0,0,0,0.5); }
        .rank { width: 40px; height: 40px; background: #fff; color: #333; border-radius: 50%; display: flex; justify-content: center; align-items: center; font-weight: 900; font-size: 1.2rem; margin-right: 15px; }
        .rank-1 .rank { background: var(--gold); box-shadow: 0 0 20px var(--gold); }
        .rank-2 .rank { background: #c0c0c0; }
        .rank-3 .rank { background: #cd7f32; }
        .avatar { width: 55px; height: 55px; border-radius: 50%; object-fit: cover; border: 3px solid rgba(255,255,255,0.3); margin-right: 15px; background: #000; }
        .info { flex: 1; }
        .name { font-size: 1.2rem; font-weight: bold; }
        .wins { font-size: 0.8rem; color: rgba(255,255,255,0.7); }
        .score { font-size: 1.5rem; font-family: 'Fredoka One'; color: var(--neon-cyan); text-shadow: 0 0 5px var(--neon-cyan); }
        @keyframes float { 0%,100%{transform:translateY(0);} 50%{transform:translateY(-5px);} }
        @media(max-width: 500px) { .player-card { padding: 10px; } .avatar { width: 45px; height: 45px; } }
    </style>
</head>
<body>
    <div class="header"><h1>LIMBO LEGENDS</h1><div style="color:rgba(255,255,255,0.6)">ACTIVE PLAYERS</div></div>
    <div class="container" id="board"><div style="text-align:center;">Loading...</div></div>
    <script>
        async function refreshBoard() {
            try {
                let response = await fetch('/api/stats');
                let data = await response.json();
                let html = '';
                data.forEach((p, i) => {
                    let r = i + 1;
                    let cls = r <= 3 ? `rank-${r}` : '';
                    let em = r === 1 ? '👑' : (r === 2 ? '⚔️' : (r === 3 ? '🛡️' : ''));
                    let av = p.avatar || `https://ui-avatars.com/api/?name=${p.username}&background=random`;
                    html += `<div class="player-card ${cls}"><div class="rank">${r}</div><img src="${av}" class="avatar"><div class="info"><div class="name">${p.username} ${em}</div><div class="wins">Wins: ${p.wins}</div></div><div class="score">${p.score}</div></div>`;
                });
                if(data.length===0) html='<div style="text-align:center;color:#aaa">No active players yet (Win a round to appear!)</div>';
                document.getElementById('board').innerHTML = html;
            } catch(e) {}
        }
        setInterval(refreshBoard, 3000); refreshBoard();
    </script>
</body>
</html>
"""

@app.route('/')
def index(): return render_template_string(LOGIN_HTML)

@app.route('/leaderboard')
def leaderboard(): return render_template_string(LEADERBOARD_HTML)

@app.route('/api/stats')
def api(): return jsonify(get_leaderboard_data())

@app.route('/start', methods=['POST'])
def start():
    u, p, r = request.form['u'], request.form['p'], request.form['r']
    if not bot.active:
        bot.start_bot(u, p, r)
        return f"<body style='background:#000;color:#0f0'><h1>✅ STARTED in {r}</h1><a href='/leaderboard' style='color:#fff'>Leaderboard</a></body>"
    return "Already Active"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)