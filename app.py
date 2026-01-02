import threading
import sqlite3
import json
import time
import random
import os
from flask import Flask, render_template_string, request, jsonify
import websocket # pip install websocket-client

# ==============================================================================
# 1. CONFIGURATION (UPDATED DROPBOX IMAGES)
# ==============================================================================
# Note: 'raw=1' ensures the image loads directly in chat/browser
GAME_IMAGES = {
    "idle":     "https://www.dropbox.com/scl/fi/dk23zdtm3oy5czo7qvwjz/1767368972763.jpg?rlkey=kfeweu26q3x3po8jumkfp2pve&st=0tz0m5oi&raw=1",
    "low_bar":  "https://www.dropbox.com/scl/fi/frufp2y2b686dx9cs6agy/1767369081606.jpg?rlkey=pxp6dx3g0b1fz7zhzd93zizkx&st=hc2x1rzq&raw=1",
    "high_bar": "https://www.dropbox.com/scl/fi/h2emqah824pnbhl2pg71n/1767369091032.jpg?rlkey=58cogoxo25548kxm44ze77pi0&st=jd1p0wjo&raw=1",
    "win":      "https://www.dropbox.com/scl/fi/jlzf075rcdfpy8n7ze248/1767369132611.jpg?rlkey=l2l9weyf1go1d0wtroath7l12&st=5kuvj88c&raw=1",
    "fail":     "https://www.dropbox.com/scl/fi/6j5quie12zht1xo8lnco7/1767369209244.jpg?rlkey=dea28h9h41zmovh9lpyntgopc&st=42s6ny8u&raw=1"
}

DB_FILE = "titan_limbo.db"
WS_URL = "wss://chatp.net:5333/server" # Chat Server URL

# ==============================================================================
# 2. DATABASE ENGINE (Thread-Safe)
# ==============================================================================
db_lock = threading.Lock()

def init_db():
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        # Table: Username, Score, Avatar URL, Total Wins
        c.execute('''CREATE TABLE IF NOT EXISTS players 
                     (username TEXT PRIMARY KEY, score INTEGER, avatar TEXT, wins INTEGER DEFAULT 0)''')
        conn.commit()
        conn.close()

# Initialize DB on start
init_db()

def update_player(user, score_change, avatar_url=None):
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        
        c.execute("SELECT score, wins FROM players WHERE username=?", (user,))
        row = c.fetchone()
        
        if row:
            # Existing Player
            new_score = row[0] + score_change
            new_wins = row[1] + (1 if score_change > 0 else 0)
            
            # Only update avatar if a new one is provided, else keep old
            if avatar_url:
                c.execute("UPDATE players SET score=?, wins=?, avatar=? WHERE username=?", (new_score, new_wins, avatar_url, user))
            else:
                c.execute("UPDATE players SET score=?, wins=? WHERE username=?", (new_score, new_wins, user))
        else:
            # New Player (Start with 1000 Bonus)
            start_score = 1000 + score_change
            # Use default avatar if none provided
            av = avatar_url if avatar_url else f"https://ui-avatars.com/api/?name={user}&background=random&color=fff"
            c.execute("INSERT INTO players (username, score, avatar, wins) VALUES (?, ?, ?, ?)", 
                      (user, start_score, av, 0))
        
        conn.commit()
        conn.close()

def get_leaderboard_data():
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row # To access by column name
        c = conn.cursor()
        # Top 50 Players ordered by Score
        c.execute("SELECT * FROM players ORDER BY score DESC LIMIT 50")
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
        return row[0] if row else 1000 # Default 1000 for new users

# ==============================================================================
# 3. GAME BOT LOGIC
# ==============================================================================
class LimboBot:
    def __init__(self):
        self.ws = None
        self.active = False
        self.creds = {}
        self.sessions = {} # Active Game Sessions

    def start_bot(self, u, p, r):
        self.creds = {"u": u, "p": p, "r": r}
        self.active = True
        # Run WebSocket in a separate thread
        t = threading.Thread(target=self.run_socket)
        t.daemon = True
        t.start()

    def run_socket(self):
        while self.active:
            try:
                # websocket.enableTrace(True)
                self.ws = websocket.WebSocketApp(WS_URL,
                    on_open=self.on_open,
                    on_message=self.on_message,
                    on_error=self.on_error,
                    on_close=self.on_close)
                self.ws.run_forever()
                time.sleep(5) # Reconnect delay
            except Exception as e:
                print(f"Connection Error: {e}")
                time.sleep(5)

    def on_open(self, ws):
        print("[BOT] Connected to Server")
        # Login Packet
        ws.send(json.dumps({
            "handler": "login",
            "username": self.creds['u'],
            "password": self.creds['p']
        }))

    def on_close(self, ws, code, msg):
        print("[BOT] Disconnected")

    def on_error(self, ws, error):
        print(f"[BOT] Error: {error}")

    def send(self, msg, img=None):
        if not self.ws: return
        payload = {"handler": "message", "message": msg}
        if img:
            payload["type"] = "image"
            payload["url"] = img
            payload["body"] = msg
        try:
            self.ws.send(json.dumps(payload))
        except: pass

    def on_message(self, ws, message):
        if not self.active: return
        try:
            data = json.loads(message)
            
            # Auto-Join Room on Login Success
            if data.get('handler') == 'login_event' and data.get('type') == 'success':
                ws.send(json.dumps({"handler": "room_join", "name": self.creds['r']}))
            
            # Process Chat Message
            if data.get('type') in ['text', 'image']:
                self.process_game_logic(data)
        except: pass

    def process_game_logic(self, data):
        user = data.get('username') or data.get('from')
        if not user: return
        
        txt = data.get('body', '').lower().strip()
        avatar = data.get('icon') or data.get('avatar_url') # Capture Avatar

        # 1. START GAME COMMAND
        if txt.startswith("limbo"):
            if user in self.sessions:
                self.send(f"@{user} finish your current game first!")
                return
            
            # Parse Bet
            bet = 0
            if "#" in txt:
                try:
                    parts = txt.split("#")
                    bet = int(parts[1])
                    if bet < 1: return
                except: return

            # Check Balance
            bal = get_balance(user)
            if bet > bal:
                self.send(f"@{user} You're broke! Balance: {bal}")
                return

            # Register Player in DB (Save Avatar)
            update_player(user, 0, avatar)

            # Start Session
            self.sessions[user] = {
                "round": 1, 
                "bet": bet, 
                "h": random.randint(10, 90)
            }
            self.announce_round(user)

        # 2. GAME MOVES
        elif user in self.sessions and txt in ["jump", "duck"]:
            self.handle_move(user, txt, avatar)

    def announce_round(self, user):
        s = self.sessions[user]
        h = s['h']
        
        # Visual Logic
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
            # WON ROUND
            profit = s['bet'] if s['bet'] > 0 else 10
            update_player(user, profit, avatar)
            
            self.send(f"✅ PASSED! +{profit}", GAME_IMAGES['win'])
            
            # Next Round Setup
            s['round'] += 1
            s['h'] = random.randint(10, 90)
            time.sleep(2)
            self.announce_round(user)
        else:
            # FAILED
            loss = s['bet'] if s['bet'] > 0 else 0
            if loss > 0: update_player(user, -loss, avatar)
            
            final_score = get_balance(user)
            self.send(f"❌ CRASH! You lost {loss}. Total: {final_score}", GAME_IMAGES['fail'])
            del self.sessions[user]

bot = LimboBot()

# ==============================================================================
# 4. FLASK SERVER (UI & LEADERBOARD)
# ==============================================================================
app = Flask(__name__)

# --- LOGIN PAGE (To start Bot) ---
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
        h1 { margin-top: 0; }
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
        <br><br>
        <a href="/leaderboard" target="_blank" style="color:#fff">VIEW LEADERBOARD</a>
    </div>
</body>
</html>
"""

# --- THE CUTE 3D LEADERBOARD HTML ---
LEADERBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Limbo Champions</title>
    <link href="https://fonts.googleapis.com/css2?family=Fredoka+One&family=Nunito:wght@700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-dark: #0f0c29;
            --card-glass: rgba(255, 255, 255, 0.05);
            --neon-pink: #ff00cc;
            --neon-blue: #3333ff;
            --neon-cyan: #00f3ff;
            --gold: #ffd700;
        }

        body {
            margin: 0; padding: 0;
            background: linear-gradient(135deg, #0f0c29, #302b63, #24243e);
            color: #fff;
            font-family: 'Nunito', sans-serif;
            min-height: 100vh;
            overflow-x: hidden;
        }

        /* HEADER */
        .header {
            text-align: center;
            padding: 40px 20px;
            font-family: 'Fredoka One', cursive;
        }
        
        h1 {
            font-size: 3rem; margin: 0;
            background: linear-gradient(to right, var(--neon-cyan), var(--neon-pink));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            filter: drop-shadow(0 0 10px rgba(0,243,255,0.5));
            animation: float 3s ease-in-out infinite;
        }

        /* 3D LIST */
        .container {
            max-width: 600px;
            margin: 0 auto;
            padding: 0 20px 50px 20px;
            perspective: 1000px; /* Essential for 3D */
        }

        .player-card {
            display: flex;
            align-items: center;
            background: var(--card-glass);
            backdrop-filter: blur(10px);
            margin-bottom: 15px;
            padding: 15px;
            border-radius: 20px;
            
            /* The Cute Border Trick */
            position: relative;
            border: 2px solid transparent;
            background-clip: padding-box;
            box-shadow: 0 10px 30px rgba(0,0,0,0.3);
            
            transition: transform 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275);
            transform-style: preserve-3d;
            cursor: default;
        }

        /* Gradient Border pseudo-element */
        .player-card::after {
            content: '';
            position: absolute; top: -2px; left: -2px; right: -2px; bottom: -2px;
            background: linear-gradient(45deg, var(--neon-pink), var(--neon-cyan));
            z-index: -1;
            border-radius: 20px;
        }

        .player-card:hover {
            transform: translateZ(20px) scale(1.02);
            box-shadow: 0 20px 40px rgba(0,0,0,0.5);
        }

        /* RANK BADGE */
        .rank {
            width: 40px; height: 40px;
            background: #fff; color: #333;
            border-radius: 50%;
            display: flex; justify-content: center; align-items: center;
            font-weight: 900; font-size: 1.2rem;
            margin-right: 15px;
            box-shadow: 0 0 10px rgba(255,255,255,0.5);
        }

        /* Top 3 Styling */
        .rank-1 .rank { background: var(--gold); color: #000; box-shadow: 0 0 20px var(--gold); }
        .rank-2 .rank { background: #c0c0c0; }
        .rank-3 .rank { background: #cd7f32; }

        /* AVATAR */
        .avatar {
            width: 55px; height: 55px;
            border-radius: 50%;
            object-fit: cover;
            border: 3px solid rgba(255,255,255,0.3);
            margin-right: 15px;
            background: #000;
        }

        .info { flex: 1; }
        .name { font-size: 1.2rem; font-weight: bold; margin-bottom: 4px; }
        .wins { font-size: 0.8rem; color: rgba(255,255,255,0.7); }

        .score-display {
            text-align: right;
        }
        .score {
            font-size: 1.5rem; font-family: 'Fredoka One';
            color: var(--neon-cyan);
            text-shadow: 0 0 5px var(--neon-cyan);
        }

        @keyframes float { 0%,100%{transform:translateY(0);} 50%{transform:translateY(-5px);} }

        /* Responsive */
        @media(max-width: 500px) {
            .player-card { padding: 10px; }
            .avatar { width: 45px; height: 45px; }
            .name { font-size: 1rem; }
        }
    </style>
</head>
<body>

    <div class="header">
        <h1>LIMBO LEGENDS</h1>
        <div style="color:rgba(255,255,255,0.6); margin-top:5px;">LIVE RANKINGS</div>
    </div>

    <div class="container" id="board">
        <!-- JS LOADS DATA HERE -->
        <div style="text-align:center; padding:20px;">Loading Data...</div>
    </div>

<script>
    async function refreshBoard() {
        try {
            // Fetch Data from our API
            let response = await fetch('/api/stats');
            let data = await response.json();
            
            let html = '';
            
            data.forEach((player, index) => {
                let rank = index + 1;
                let specialClass = rank <= 3 ? `rank-${rank}` : '';
                let emoji = '';
                
                if(rank === 1) emoji = '👑';
                else if(rank === 2) emoji = '⚔️';
                else if(rank === 3) emoji = '🛡️';

                // Use the Avatar from DB or a default cute one
                let av = player.avatar || `https://ui-avatars.com/api/?name=${player.username}&background=random`;

                html += `
                <div class="player-card ${specialClass}">
                    <div class="rank">${rank}</div>
                    <img src="${av}" class="avatar">
                    <div class="info">
                        <div class="name">${player.username} ${emoji}</div>
                        <div class="wins">Wins: ${player.wins}</div>
                    </div>
                    <div class="score-display">
                        <div class="score">${player.score}</div>
                    </div>
                </div>
                `;
            });

            if(data.length === 0) {
                html = '<div style="text-align:center; padding:20px; color:#aaa;">No players yet. Start a game!</div>';
            }

            document.getElementById('board').innerHTML = html;

        } catch(e) {
            console.error("Connection Error", e);
        }
    }

    // Refresh every 3 seconds for Real-Time feel
    setInterval(refreshBoard, 3000);
    refreshBoard();
</script>

</body>
</html>
"""

# --- ROUTES ---
@app.route('/')
def index():
    return render_template_string(LOGIN_HTML)

@app.route('/leaderboard')
def leaderboard():
    return render_template_string(LEADERBOARD_HTML)

@app.route('/api/stats')
def api():
    data = get_leaderboard_data()
    return jsonify(data)

@app.route('/start', methods=['POST'])
def start():
    u = request.form['u']
    p = request.form['p']
    r = request.form['r']
    if not bot.active:
        bot.start_bot(u, p, r)
        return """
        <body style='background:#000; color:#0f0; text-align:center; font-family:monospace; display:flex; flex-direction:column; justify-content:center; height:100vh; margin:0;'>
            <h1>✅ SYSTEM ONLINE</h1>
            <p>Bot is running in room: """ + r + """</p>
            <br>
            <a href='/leaderboard' target='_blank' style='color:#fff; font-size:20px; text-decoration:none; border:1px solid #fff; padding:10px; border-radius:10px;'>🚀 OPEN LEADERBOARD</a>
        </body>
        """
    return "Bot already active."

if __name__ == '__main__':
    # Gunicorn expects app:app, locally we run this
    app.run(host='0.0.0.0', port=5000)