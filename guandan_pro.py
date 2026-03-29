from flask import Flask, render_template_string, request, redirect, url_for, session, send_file, abort
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import text
from datetime import datetime
import random
import pandas as pd
import io
import os

app = Flask(__name__)
app.secret_key = "sv_guandan_v18_ultimate_international_key"
database_url = os.environ.get('DATABASE_URL')
if database_url:
    # Render PostgreSQL URL starts with postgres://, SQLAlchemy needs postgresql://
    database_url = database_url.replace('postgres://', 'postgresql://', 1)
else:
    db_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'guandan_pro_v18.db')
    database_url = 'sqlite:///' + db_path
app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- 0. 国际化 (i18n) 双语引擎 ---
def T(zh, en):
    """双语渲染引擎。返回带样式的双语字符串"""
    lang = session.get('lang', 'zh-en')
    if lang == 'zh-en': 
        return f"{zh} <span style='font-size:0.75em; opacity:0.85; font-weight:normal;'>({en})</span>"
    return f"{zh} / {en}"

# --- 1. 完整数据库模型 ---

class Tournament(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    owner = db.Column(db.String(50), nullable=True) 
    created_at = db.Column(db.DateTime, default=datetime.now)
    is_active = db.Column(db.Boolean, default=True)

class TournamentInfo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tournament_id = db.Column(db.Integer, db.ForeignKey('tournament.id'))
    t_date = db.Column(db.String(100))
    t_location = db.Column(db.String(200))
    t_sponsor = db.Column(db.String(200))
    t_note = db.Column(db.Text)

class SystemConfig(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tournament_id = db.Column(db.Integer, db.ForeignKey('tournament.id'))
    current_round = db.Column(db.Integer, default=1)
    total_rounds = db.Column(db.Integer, default=5)
    scroll_ad = db.Column(db.String(500), default="📢 欢迎参加国际掼蛋大奖赛！ Welcome to the International Guandan Tournament!")
    bg_music_url = db.Column(db.String(500), default="https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3")

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True)
    password = db.Column(db.String(200)) 
    is_locked = db.Column(db.Boolean, default=False)

class Team(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tournament_id = db.Column(db.Integer, db.ForeignKey('tournament.id'))
    name = db.Column(db.String(50))
    players = db.Column(db.String(200))
    current_score = db.Column(db.Integer, default=0)
    round_score = db.Column(db.Integer, default=0)
    history_opponents = db.Column(db.Text, default="")

class Match(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tournament_id = db.Column(db.Integer, db.ForeignKey('tournament.id'))
    round_no = db.Column(db.Integer)
    table_no = db.Column(db.Integer)
    team_a_id = db.Column(db.Integer)
    team_b_id = db.Column(db.Integer)
    team_a_name = db.Column(db.String(50))
    team_b_name = db.Column(db.String(50))
    pos_east = db.Column(db.String(50))
    pos_west = db.Column(db.String(50))
    pos_north = db.Column(db.String(50))
    pos_south = db.Column(db.String(50))
    pos_p5 = db.Column(db.String(50))
    pos_p6 = db.Column(db.String(50))
    score_a = db.Column(db.Integer, default=-1) 
    score_b = db.Column(db.Integer, default=-1) 
    is_completed = db.Column(db.Boolean, default=False)

class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tournament_id = db.Column(db.Integer, db.ForeignKey('tournament.id'), nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.now)
    user = db.Column(db.String(50))
    action = db.Column(db.String(100))
    details = db.Column(db.Text)

# --- 2. 核心拦截器与辅助逻辑 ---

@app.before_request
def check_frozen():
    allowed = ['unlock', 'logout', 'login', 'static', 'panorama']
    if request.endpoint and request.endpoint not in allowed:
        if session.get('frozen'):
            return redirect(url_for('unlock'))

def get_active_t():
    curr_user = session.get('username')
    if not curr_user: return None
    if curr_user == 'admin': return Tournament.query.filter_by(is_active=True).first()
    return Tournament.query.filter_by(is_active=True, owner=curr_user).first()

def get_config(t_id):
    c = SystemConfig.query.filter_by(tournament_id=t_id).first()
    if not c:
        c = SystemConfig(tournament_id=t_id)
        db.session.add(c)
        db.session.commit()
    return c

def log_act(action, details="", t_id=None):
    try:
        new_log = AuditLog(user=session.get('username','SYSTEM'), action=action, details=details, tournament_id=t_id)
        db.session.add(new_log)
        db.session.commit()
    except:
        db.session.rollback()

def swiss_pairing(t_id, round_no):
    teams = Team.query.filter_by(tournament_id=t_id).all()
    teams.sort(key=lambda x: (x.current_score, x.round_score), reverse=True)
    paired, used = [], set()
    total_r = get_config(t_id).total_rounds
    for i in range(len(teams)):
        if teams[i].id in used: continue
        found = False
        for j in range(i + 1, len(teams)):
            if teams[j].id in used: continue
            history = teams[i].history_opponents.split(',') if teams[i].history_opponents else []
            if str(teams[j].id) not in history or round_no >= total_r:
                paired.append((teams[i], teams[j]))
                used.update([teams[i].id, teams[j].id])
                found = True
                break
        if not found and i < len(teams) - 1:
            for k in range(i + 1, len(teams)):
                if teams[k].id not in used:
                    paired.append((teams[i], teams[k]))
                    used.update([teams[i].id, teams[k].id])
                    break
    return paired

# --- 3. UI 渲染引擎 ---

def render_layout(content, active="", is_login=False, hide_nav=False):
    t = get_active_t()
    conf = get_config(t.id) if t else None
    navs = [
        ('setup', T('赛事设置','Setup')),
        ('matches', T('全景面板','Matches')),
        ('leaderboard', T('积分榜','Leaderboard')),
        ('info', T('赛事详情','Details')),
        ('users', T('系统管理','Settings')),
        ('logs', T('日志','Logs'))
    ]
    nav_html = "".join([f'<li class="nav-item"><a class="nav-link {"active text-info fw-bold" if active==n[0] else "text-white opacity-75"}" href="/{n[0]}">{n[1]}</a></li>' for n in navs])
    
    body_style = "background: #0f172a; color: #f1f5f9; min-height: 100vh; font-family: 'Microsoft YaHei', system-ui; overflow-x: hidden;"
    if is_login:
        body_style = "background: radial-gradient(circle at center, #065f46 0%, #022c22 100%); display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; overflow: hidden; font-family: 'Microsoft YaHei', system-ui;"

    # 修复：增大了 .seat-player 的 font-size 到 1.2rem，使其在大屏上更清晰可见
    return render_template_string(f"""
    <!DOCTYPE html><html><head><meta charset="UTF-8"><title>🏆 Guandan System V18.0</title>
    <link href="https://cdn.staticfile.org/twitter-bootstrap/5.3.0/css/bootstrap.min.css" rel="stylesheet">
    <script src="https://cdn.staticfile.org/bootstrap/5.3.0/js/bootstrap.bundle.min.js"></script>
    <style>
        .navbar {{ background: rgba(30, 41, 59, 0.95); backdrop-filter: blur(12px); border-bottom: 1px solid rgba(255,255,255,0.1); z-index: 1050; position: sticky; top: 0; }}
        .glass-card {{ background: rgba(30, 41, 59, 0.7); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 16px; box-shadow: 0 10px 30px rgba(0,0,0,0.4); }}
        
        .seat-wrapper {{ position: relative; height: 210px; background: rgba(0,0,0,0.3); border-radius: 12px; border: 1px solid rgba(59,130,246,0.2); display: flex; align-items: center; justify-content: center; }}
        .seat-player {{ position: absolute; font-size: 1.2rem; font-weight: bold; color: #0dcaf0; white-space: nowrap; text-shadow: 1px 1px 2px #000; z-index: 10; background: rgba(15, 23, 42, 0.8); padding: 4px 10px; border-radius: 6px; border: 1px solid rgba(13,202,240,0.4); }}
        
        .pos-4-n {{ top: 10px; left: 50%; transform: translateX(-50%); }}
        .pos-4-s {{ bottom: 10px; left: 50%; transform: translateX(-50%); }}
        .pos-4-w {{ left: 10px; top: 50%; transform: translateY(-50%); }}
        .pos-4-e {{ right: 10px; top: 50%; transform: translateY(-50%); }}

        .pos-6-1 {{ top: 10px; left: 50%; transform: translateX(-50%); }}
        .pos-6-2 {{ top: 25%; right: 10px; transform: translateY(-50%); }}
        .pos-6-3 {{ bottom: 25%; right: 10px; transform: translateY(50%); }}
        .pos-6-4 {{ bottom: 10px; left: 50%; transform: translateX(-50%); }}
        .pos-6-5 {{ bottom: 25%; left: 10px; transform: translateY(50%); }}
        .pos-6-6 {{ top: 25%; left: 10px; transform: translateY(-50%); }}

        .table-circle {{ border-radius: 50%; height: 65px; width: 65px; display: flex; align-items: center; justify-content: center; font-weight: bold; cursor: pointer; transition: 0.4s all; border: 2px solid rgba(255,255,255,0.2); position: relative; z-index: 20; }}
        .table-blue {{ background: linear-gradient(135deg, #3b82f6, #1d4ed8); box-shadow: 0 0 20px rgba(59,130,246,0.5); }}
        .table-red {{ background: linear-gradient(135deg, #ef4444, #991b1b) !important; box-shadow: 0 0 20px rgba(239,68,68,0.5); cursor: not-allowed; }}
        #timer-box {{ background: rgba(30, 41, 59, 0.8); border: 2px solid #3b82f6; border-radius: 60px; width: fit-content; margin: 0 auto 30px; padding: 12px 40px; display: flex; align-items: center; gap: 25px; box-shadow: 0 0 30px rgba(59,130,246,0.3); }}
        #time-display {{ font-size: 2.2rem; font-weight: 800; font-family: 'Courier New', monospace; color: #3b82f6; text-shadow: 0 0 10px rgba(59,130,246,0.5); }}
        
        .ad-ticker-pro {{ background: linear-gradient(90deg, #991b1b, #ef4444, #991b1b); border-bottom: 2px solid #fbbf24; height: 50px; line-height: 50px; overflow: hidden; position: relative; z-index: 1040; box-shadow: 0 5px 15px rgba(0,0,0,0.5); }}
        .ad-content {{ position: absolute; white-space: nowrap; animation: ticker 25s linear infinite; font-weight: bold; color: #ffffff; font-size: 1.4rem; letter-spacing: 2px; text-shadow: 1px 1px 3px black; }}
        @keyframes ticker {{ 0% {{ left: 100%; }} 100% {{ left: -200%; }} }}
        
        .table-dark {{ --bs-table-bg: rgba(30, 41, 59, 0.5); border-color: rgba(255,255,255,0.05); }}
        .btn-info {{ background: #06b6d4; border: none; color: #000; font-weight: bold; }}
    </style>
    <script>
        let timer; let timeLeft; let isPaused = false;
        function startTimer() {{
            let durEl = document.getElementById('duration');
            let mins;
            if(durEl) {{
                mins = parseInt(durEl.value) || 50;
                localStorage.setItem('guandan_timer_mins', mins);
            }} else {{
                mins = parseInt(localStorage.getItem('guandan_timer_mins')) || 50;
            }}
            timeLeft = mins * 60;
            if(timer) clearInterval(timer);
            isPaused = false;
            updateTimerDisplay();
            timer = setInterval(() => {{
                if(!isPaused) {{
                    if(timeLeft <= 0) {{ clearInterval(timer); let td = document.getElementById('time-display'); if(td) {{ td.innerText = "FINISH"; td.style.color = "#ef4444"; }} }}
                    else {{ timeLeft--; updateTimerDisplay(); }}
                }}
            }}, 1000);
        }}
        function togglePause() {{ 
            isPaused = !isPaused; 
            let pb = document.getElementById('pause-btn');
            if(pb) {{
                pb.innerText = isPaused ? "Resume" : "Pause"; 
                pb.className = isPaused ? "btn btn-warning btn-sm rounded-pill" : "btn btn-outline-warning btn-sm rounded-pill";
            }}
        }}
        function updateTimerDisplay() {{ 
            let m=Math.floor(timeLeft/60), s=timeLeft%60; 
            let td = document.getElementById('time-display'); 
            if(td) td.innerText = (m<10?'0'+m:m)+":"+(s<10?'0'+s:s); 
        }}
        function initPanoramaDisplay() {{
            let mins = parseInt(localStorage.getItem('guandan_timer_mins')) || 50;
            let m = mins, s = 0;
            let td = document.getElementById('time-display');
            if(td) td.innerText = (m<10?'0'+m:m)+":"+(s<10?'0'+s:s);
        }}
    </script></head>
    <body style="{body_style}">
        {f'<div class="w-100 align-self-start"><nav class="navbar navbar-expand-lg navbar-dark mb-4"><div class="container-fluid px-5"><a class="navbar-brand fw-bold text-info fs-4" href="/">🏆 Guandan System</a><button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#navbarNav"><span class="navbar-toggler-icon"></span></button><div class="collapse navbar-collapse" id="navbarNav"><ul class="navbar-nav mx-auto">{nav_html}</ul><div class="d-flex align-items-center"><span class="badge bg-primary fs-6 px-3 py-2 me-3">{T("当前轮次: 第", "Current Round:")} {conf.current_round if conf else 0}</span><a href="/panorama" target="_blank" class="btn btn-outline-warning btn-sm rounded-pill me-2">📺 {T("大屏", "Display")}</a><a href="/lock_screen" class="btn btn-outline-light btn-sm rounded-pill me-2">❄️ {T("锁定", "Freeze")}</a><a href="/logout" class="btn btn-outline-danger btn-sm rounded-pill">退出 / Exit</a></div></div></div></nav></div>' if not is_login and not hide_nav else ''}
        <div class="{'w-100' if is_login else 'container-fluid px-5 py-2'}">{content}</div>
    </body></html>
    """)

# --- 4. 业务逻辑 ---

@app.route('/lock_screen')
def lock_screen():
    session['frozen'] = True
    return redirect(url_for('unlock'))

@app.route('/unlock', methods=['GET', 'POST'])
def unlock():
    if request.method == 'POST':
        pwd = request.form['p']
        u = User.query.filter_by(username=session.get('username')).first()
        if u and check_password_hash(u.password, pwd):
            session.pop('frozen', None)
            return redirect(url_for('setup'))
        return render_layout(f'<div class="alert alert-danger text-center w-25 mx-auto mt-5 shadow-lg">{T("密码错误", "Incorrect Password")}</div>', is_login=True)
    
    html = f"""
    <div class="p-5 text-center position-relative mx-3" style="background: rgba(15, 23, 42, 0.9); border: 1px solid rgba(13, 202, 240, 0.5); border-radius: 20px; box-shadow: 0 25px 50px -12px rgba(0,0,0,1);">
        <h2 class="text-info mb-4 fw-bold">❄️ {T("屏幕已冻结", "Screen Frozen")}</h2>
        <p class="text-secondary small mb-4">{T(f"需要用户 {session.get('username')} 的密码解冻", f"Password required for {session.get('username')}")}</p>
        <form method="post">
            <div class="form-floating mb-4 text-dark">
                <input type="password" name="p" class="form-control" id="p" placeholder="Password" required autofocus>
                <label for="p">🔑 {T('解锁密码', 'Unlock Password')}</label>
            </div>
            <button class="btn btn-info w-100 py-3 fw-bold shadow rounded-pill fs-5">{T('解除冻结', 'Unfreeze Screen')}</button>
        </form>
    </div>
    """
    return render_layout(html, is_login=True)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        u = User.query.filter_by(username=request.form['u']).first()
        if u and check_password_hash(u.password, request.form['p']):
            if u.is_locked: return render_layout(f'<div class="alert alert-danger w-25 mx-auto mt-5 text-center shadow-lg">{T("账户已被锁定，请联系管理员", "Account locked, contact admin.")}</div>', is_login=True)
            session.update({'logged_in': True, 'username': u.username})
            log_act("Admin Login", f"IP: {request.remote_addr}")
            return redirect(url_for('setup'))
            
    login_html = f"""
    <style>
        .suit-bg {{ position: absolute; font-size: 18rem; color: rgba(255, 255, 255, 0.03); z-index: 0; user-select: none; font-family: Arial, sans-serif; pointer-events: none; }}
        .suit-1 {{ top: -5%; left: 10%; transform: rotate(-20deg); }}
        .suit-2 {{ bottom: -10%; right: 5%; transform: rotate(15deg); color: rgba(239, 68, 68, 0.04); }}
        .suit-3 {{ top: 40%; left: -5%; transform: rotate(10deg); color: rgba(239, 68, 68, 0.04); }}
        .suit-4 {{ top: 15%; right: 15%; transform: rotate(-25deg); }}
        .login-card-pro {{ background: rgba(15, 23, 42, 0.85); backdrop-filter: blur(20px); border: 1px solid rgba(251, 191, 36, 0.4); box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.9); z-index: 10; width: 100%; max-width: 440px; border-radius: 20px; }}
        .logo-box {{ width: 120px; height: 120px; border-radius: 50%; border: 3px solid #fbbf24; box-shadow: 0 0 30px rgba(251, 191, 36, 0.5); object-fit: cover; margin-top: -60px; margin-bottom: 20px; background: #fff; padding: 2px; }}
        .login-title {{ font-family: 'STKaiti', 'KaiTi', serif; letter-spacing: 3px; text-shadow: 0 2px 5px rgba(0,0,0,0.8); }}
        .btn-gold {{ background: linear-gradient(180deg, #fcd34d 0%, #f59e0b 100%); color: #451a03; border: none; transition: 0.3s; }}
        .btn-gold:hover {{ background: linear-gradient(180deg, #fde68a 0%, #d97706 100%); transform: translateY(-2px); box-shadow: 0 10px 20px rgba(245, 158, 11, 0.4); }}
        .login-ad-wrap {{ display:none; width:92vw; max-width:1100px; margin:0 auto 22px; z-index:20; position:relative; }}
        .login-ad-inner {{ display:flex; align-items:center; gap:14px; background:rgba(10,20,40,0.88); border:2px solid rgba(212,172,13,0.6); border-radius:40px; padding:26px 36px; min-height:108px; backdrop-filter:blur(12px); box-shadow:0 6px 32px rgba(0,0,0,0.55); cursor:default; }}
        .login-ad-label {{ flex-shrink:0; font-size:0.82rem; font-weight:700; color:#D4AC0D; border:1.5px solid rgba(212,172,13,0.6); border-radius:5px; padding:3px 8px; letter-spacing:2px; }}
        .login-ad-text-wrap {{ flex:1; overflow:hidden; }}
        .login-ad-text {{ display:inline-block; white-space:nowrap; color:#fff; font-size:22px; font-weight:500; animation: ad-scroll-login 20s linear infinite; }}
        .login-ad-close {{ flex-shrink:0; color:rgba(255,255,255,0.45); font-size:1.1rem; cursor:pointer; padding:4px 8px; border-radius:50%; transition:color 0.2s; }}
        .login-ad-close:hover {{ color:#fff; }}
        @keyframes ad-scroll-login {{
          0%   {{ transform: translateX(80px); }}
          100% {{ transform: translateX(-100%); }}
        }}
    </style>
    <div class="position-relative w-100 h-100 d-flex flex-column justify-content-start align-items-center" style="padding-top:6vh">
        <div class="suit-bg suit-1">♠</div><div class="suit-bg suit-2">♥</div>
        <div class="suit-bg suit-3">♦</div><div class="suit-bg suit-4">♣</div>
        <!-- 广告栏 -->
        <div class="login-ad-wrap" id="loginAdWrap">
          <div class="login-ad-inner" id="loginAdLink">
            <span class="login-ad-label">广 告</span>
            <div class="login-ad-text-wrap">
              <span class="login-ad-text" id="loginAdText"></span>
            </div>
            <span class="login-ad-close" onclick="closeLoginAd(event)" title="关闭">✕</span>
          </div>
        </div>
        <div style="padding-top:60px; width:100%; display:flex; justify-content:center;">
        <div class="login-card-pro p-5 text-center position-relative mx-3">
            <img src="/static/硅谷掼蛋协会logo.png" alt="Logo" class="logo-box position-relative" onerror="this.src='data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAxMDAgMTAwIj48Y2lyY2xlIGN4PSI1MCIgY3k9IjUwIiByPSI1MCIgZmlsbD0iI2ZiYmYyNCIvPjx0ZXh0IHg9IjUwJSIgeT0iNTAlIiBkeT0iLjNlbSIgdGV4dC1hbmNob3I9Im1pZGRsZSIgZm9udC1zaXplPSIyMCIgZmlsbD0iIzQ1MWEwMyI+TG9nbzwvdGV4dD48L3N2Zz4='">
            <h1 class="text-warning mb-1 fw-bold login-title">{T('硅谷掼蛋管理系统', 'SV Guandan System')}</h1>
            <p class="text-white-50 mb-5 small" style="letter-spacing: 1px;">Tournament Management V18.0</p>
            <form method="post">
                <div class="form-floating mb-3 text-dark">
                    <input name="u" class="form-control fw-bold border-0 bg-light" id="u" placeholder="Account" required>
                    <label for="u">🪪 {T('管理账号', 'Account')}</label>
                </div>
                <div class="form-floating mb-5 text-dark">
                    <input type="password" name="p" class="form-control fw-bold border-0 bg-light" id="p" placeholder="Password" required>
                    <label for="p">🔑 {T('管理密码', 'Password')}</label>
                </div>
                <button class="btn btn-gold w-100 py-3 fw-bold shadow fs-5 rounded-pill">♠ {T('进入赛场', 'Enter')} ♠</button>
            </form>
        </div>
        </div>
    </div>
    <script>
    (function(){{
      var API = 'https://silicon-guandan-system.onrender.com/scoreboard';
      var allAds = [], normalAds = [], specialAds = [];
      var normalIdx = 0, normalTimer = null, adClosed = false;

      fetch(API + '/api/ads').then(function(r){{ return r.json(); }}).then(function(data){{
        allAds = data.ads || (data.ad ? [data.ad] : []);
        normalAds = allAds.filter(function(a){{ return !a.frequency_minutes; }});
        specialAds = allAds.filter(function(a){{ return !!a.frequency_minutes; }});
        if (allAds.length === 0) return;
        if (normalAds.length > 0) {{
          showNormal(0);
          if (normalAds.length > 1) normalTimer = setTimeout(rotateNormal, 30000);
        }} else {{ displayAd(specialAds[0]); }}
        specialAds.forEach(function(ad){{
          setInterval(function(){{
            if (adClosed) return;
            if (normalTimer){{ clearTimeout(normalTimer); normalTimer = null; }}
            displayAd(ad);
            if (normalAds.length > 0) normalTimer = setTimeout(rotateNormal, 30000);
          }}, ad.frequency_minutes * 60 * 1000);
        }});
      }}).catch(function(){{}});

      function showNormal(idx){{ normalIdx = idx % normalAds.length; displayAd(normalAds[normalIdx]); }}
      function rotateNormal(){{
        if (adClosed) return;
        normalIdx = (normalIdx + 1) % normalAds.length;
        displayAd(normalAds[normalIdx]);
        normalTimer = setTimeout(rotateNormal, 30000);
      }}
      function displayAd(ad){{
        var el = document.getElementById('loginAdText');
        el.textContent = ad.content_text || ad.title;
        el.style.animation = 'none'; void el.offsetWidth; el.style.animation = '';
        var link = document.getElementById('loginAdLink');
        link.onclick = null;
        if (ad.link_url){{
          link.style.cursor = 'pointer';
          link.onclick = function(e){{
            if (e.target.classList.contains('login-ad-close')) return;
            fetch(API + '/api/ads/' + ad.id + '/click', {{method:'POST'}}).catch(function(){{}});
            window.open(ad.link_url, '_blank', 'noopener');
          }};
        }} else {{ link.style.cursor = 'default'; }}
        document.getElementById('loginAdWrap').style.display = 'block';
        adClosed = false;
        fetch(API + '/api/ads/' + ad.id + '/impression', {{method:'POST'}}).catch(function(){{}});
      }}
      window.closeLoginAd = function(e){{
        e.stopPropagation();
        document.getElementById('loginAdWrap').style.display = 'none';
        adClosed = true;
        if (normalTimer){{ clearTimeout(normalTimer); normalTimer = null; }}
      }};
    }})();
    </script>
    """
    return render_layout(login_html, is_login=True)

@app.route('/setup', methods=['GET', 'POST'])
def setup():
    if not session.get('logged_in'): return redirect(url_for('login'))
    curr_user = session.get('username')
    t = get_active_t()
    
    if request.method == 'POST' and 'team_n' in request.form:
        if t:
            new_team = Team(tournament_id=t.id, name=request.form['team_n'], players=request.form['team_p'])
            db.session.add(new_team); db.session.commit()
            log_act("Manual Add Team", f"Team: {new_team.name}", t.id)

    teams = Team.query.filter_by(tournament_id=t.id).all() if t else []
    
    if not t:
        excel_upload_html = f'<div class="glass-card p-3 mb-4 shadow text-center border-0" style="background: rgba(13,202,240,0.1);"><small class="text-info">💡 {T("欢迎！请先在下方【启动新赛事】或激活存档。", "Welcome! Start a new tournament or activate an archive below.")}</small></div>'
        team_input_html = f'<div class="glass-card p-4 mb-4 shadow text-center"><h5 class="text-secondary mb-3">👥 {T("单个录入队伍", "Add Single Team")}</h5><p class="text-white-50 small m-0">{T("需激活赛事后方可录入数据", "Activate a tournament first")}</p></div>'
    elif len(teams) == 0:
        excel_upload_html = f"""<div class="glass-card p-4 mb-4 shadow" style="border: 1px dashed rgba(255, 193, 7, 0.5);"><h6 class="text-warning fw-bold mb-3">📁 {T('批量初始化参赛名单','Batch Import Excel')}</h6><form action="/upload_teams_excel" method="post" enctype="multipart/form-data"><div class="mb-3"><input type="file" name="excel_file" accept=".xlsx, .xls" class="form-control form-control-sm bg-dark text-white border-secondary" required></div><button type="submit" class="btn btn-warning btn-sm w-100 fw-bold">{T('上传并且读取Excel文件','Upload & Parse')}</button></form><small class="text-white-50 mt-2 d-block" style="font-size: 11px;">(需包含列: <b>排名</b>, <b>队名</b>, <b>队员</b>)</small></div>"""
        team_input_html = f"""<div class="glass-card p-4 mb-4 shadow"><h5>👥 {T('单个录入队伍','Add Single Team')}</h5><form method="post" class="mt-3"><div class="mb-3"><input name="team_n" class="form-control bg-dark text-white border-secondary" placeholder="{T('队伍名称','Team Name')}" required></div><div class="mb-3"><input name="team_p" class="form-control bg-dark text-white border-secondary" placeholder="{T('选手1, 选手2','Player 1, Player 2')}" required></div><button class="btn btn-success w-100 fw-bold">{T('确认录入','Confirm Add')}</button></form></div>"""
    else:
        excel_upload_html = f"""<div class="glass-card p-3 mb-4 shadow text-center border-0" style="background: rgba(255,193,7,0.1);"><small class="text-warning">⚠️ {T('当前赛事已有数据，批量上传已锁定。','Batch upload locked as data exists.')}</small></div>"""
        team_input_html = f"""<div class="glass-card p-4 mb-4 shadow"><h5>👥 {T('单个录入队伍','Add Single Team')}</h5><form method="post" class="mt-3"><div class="mb-3"><input name="team_n" class="form-control bg-dark text-white border-secondary" placeholder="{T('队伍名称','Team Name')}" required></div><div class="mb-3"><input name="team_p" class="form-control bg-dark text-white border-secondary" placeholder="{T('选手1, 选手2','Player 1, Player 2')}" required></div><button class="btn btn-success w-100 fw-bold">{T('确认录入','Confirm Add')}</button></form></div>"""

    if curr_user == 'admin':
        history = Tournament.query.order_by(Tournament.created_at.desc()).all()
    else:
        history = Tournament.query.filter_by(owner=curr_user).order_by(Tournament.created_at.desc()).all()
    
    rows = "".join([f"<tr><td class='fw-bold text-info'>{tm.name}</td><td>{tm.players}</td><td><button class='btn btn-sm btn-outline-info me-2' data-bs-toggle='modal' data-bs-target='#edit{tm.id}'>{T('编辑','Edit')}</button><a href='/del_team/{tm.id}' class='btn btn-sm btn-outline-danger'>{T('删除','Delete')}</a></td></tr><div class='modal fade' id='edit{tm.id}'><div class='modal-dialog modal-dialog-centered'><div class='modal-content bg-dark text-white border-info'><form action='/edit_team/{tm.id}' method='post'><div class='modal-body p-4'><h5 class='mb-4 text-info fw-bold'>{T('修改参赛信息','Edit Info')}</h5><div class='mb-3'><label>{T('名称','Name')}</label><input name='name' class='form-control bg-secondary text-white border-0' value='{tm.name}'></div><div class='mb-3'><label>{T('选手','Players')}</label><input name='players' class='form-control bg-secondary text-white border-0' value='{tm.players}'></div></div><div class='modal-footer border-0'><button type='submit' class='btn btn-info w-100 fw-bold'>{T('保存修改','Save Changes')}</button></div></form></div></div></div>" for tm in teams])
    
    return render_layout(f"""<div class="row"><div class="col-md-4">{excel_upload_html}{team_input_html}<div class="glass-card p-4 shadow"><h5>📅 {T('赛事存档','Tournament Archives')}</h5><div class="list-group mt-3 small mb-4">{"".join([f"<a href='/view_history/{h.id}' class='list-group-item list-group-item-action bg-transparent text-white border-secondary'>{'<span class=\"badge bg-info\">Active</span>' if h.is_active else '📁'} {h.name} ({h.owner})</a>" for h in history])}</div><div class="border-top border-secondary pt-3"><h6 class="text-info small fw-bold mb-3">🆕 {T('启动新赛事','Create New Tournament')}</h6><form action="/create_new_tournament" method="post"><div class="input-group input-group-sm"><input name="new_name" class="form-control bg-dark text-white border-secondary" placeholder="Name" required><button class="btn btn-outline-info" type="submit">{T('创建','Create')}</button></div></form></div></div></div><div class="col-md-8 px-4"><div class="glass-card p-4 shadow"><div class="d-flex justify-content-between mb-4"><h5>{T('参赛名单','Participant List')} ({len(teams)})</h5><a href="/export_excel" class="btn btn-outline-info btn-sm rounded-pill px-3 {'disabled' if not t else ''}">📥 {T('导出全记录 (Excel)','Export Full Record')}</a></div><div class="table-responsive" style="max-height: 500px;"><table class="table table-dark table-hover"><thead><tr><th>{T('队伍名称','Team')}</th><th>{T('选手成员','Players')}</th><th>{T('管理','Manage')}</th></tr></thead><tbody>{rows}</tbody></table></div><a href="/init_game" class="btn btn-info w-100 py-3 fw-bold mt-4 fs-5 shadow-lg {'disabled' if not t else ''}">🎲 {T('锁定并生成首轮对阵','Lock & Generate Round 1')}</a></div></div></div>""", "setup")

@app.route('/upload_teams_excel', methods=['POST'])
def upload_teams_excel():
    if not session.get('logged_in'): return redirect(url_for('login'))
    t = get_active_t()
    if not t: return "<script>alert('Error: No active tournament!');window.location.href='/setup';</script>"
    existing_teams_count = Team.query.filter_by(tournament_id=t.id).count()
    if existing_teams_count > 0: return "<script>alert('Error: Data exists. Batch upload locked.');window.location.href='/setup';</script>"
    if 'excel_file' not in request.files: return "<script>alert('No file detected.');window.location.href='/setup';</script>"
    file = request.files['excel_file']
    if file.filename == '': return "<script>alert('No file selected.');window.location.href='/setup';</script>"
    if file and (file.filename.endswith('.xlsx') or file.filename.endswith('.xls')):
        try:
            df = pd.read_excel(file)
            required_cols = ['排名', '队名', '队员']
            if not all(col in df.columns for col in required_cols): return f"<script>alert('Format error! Must include: 排名, 队名, 队员');window.location.href='/setup';</script>"
            df = df.sort_values(by='排名')
            for index, row in df.iterrows(): db.session.add(Team(name=str(row['队名']).strip(), players=str(row['队员']).strip(), tournament_id=t.id))
            db.session.commit()
            log_act("Excel Import", f"Imported {len(df)} teams", t.id)
            return redirect(url_for('setup'))
        except Exception as e:
            db.session.rollback(); return f"<script>alert('File read error: {str(e)}');window.location.href='/setup';</script>"
    return "<script>alert('Invalid format! Only .xlsx or .xls');window.location.href='/setup';</script>"

@app.route('/create_new_tournament', methods=['POST'])
def create_new_tournament():
    if not session.get('logged_in'): return redirect(url_for('login'))
    curr_user = session.get('username')
    new_name = request.form.get('new_name')
    if new_name:
        if curr_user == 'admin': Tournament.query.update({Tournament.is_active: False})
        else: Tournament.query.filter_by(owner=curr_user).update({Tournament.is_active: False})
        new_t = Tournament(name=new_name, is_active=True, owner=curr_user)
        db.session.add(new_t); db.session.commit(); log_act("Create Tournament", f"Name: {new_name}", new_t.id)
    return redirect(url_for('setup'))

@app.route('/init_game')
def init_game():
    t = get_active_t()
    if not t: return redirect(url_for('setup'))
    conf = get_config(t.id); conf.current_round = 1
    Match.query.filter_by(tournament_id=t.id).delete()
    ts = Team.query.filter_by(tournament_id=t.id).all()
    if len(ts) < 2: return "Not enough teams"
    random.shuffle(ts)
    for i in range(0, len(ts)-1, 2):
        t1, t2 = ts[i], ts[i+1]
        p1, p2 = [x.strip() for x in t1.players.replace('，',',').split(',')], [x.strip() for x in t2.players.replace('，',',').split(',')]
        # 6人赛：每队恰好3名队员；4人赛：每队恰好2名队员
        if len(p1) >= 3 and len(p2) >= 3:
            # 6人赛桌：A队占①③⑤位，B队占②④⑥位（对面而坐）
            db.session.add(Match(tournament_id=t.id, round_no=1, table_no=(i//2+1), team_a_id=t1.id, team_b_id=t2.id, team_a_name=t1.name, team_b_name=t2.name, pos_north=p1[0].strip(), pos_p5=p2[0].strip(), pos_east=p1[1].strip(), pos_south=p2[1].strip(), pos_p6=p1[2].strip(), pos_west=p2[2].strip()))
        else:
            # 4人赛桌：A队占北/南位置，B队占东/西位置
            db.session.add(Match(tournament_id=t.id, round_no=1, table_no=(i//2+1), team_a_id=t1.id, team_b_id=t2.id, team_a_name=t1.name, team_b_name=t2.name, pos_north=p1[0].strip() if len(p1)>0 else "P1", pos_east=p2[0].strip() if len(p2)>0 else "P2", pos_south=p1[1].strip() if len(p1)>1 else "P3", pos_west=p2[1].strip() if len(p2)>1 else "P4", pos_p5="", pos_p6=""))
    log_act("Init Game", "Generated Round 1", t.id); db.session.commit()
    return redirect(url_for('matches'))

# --- 核心页面：共用生成比赛卡片代码 ---
def generate_matches_html(t, conf, is_panorama=False):
    ms = Match.query.filter_by(tournament_id=t.id, round_no=conf.current_round).all()
    cards = []
    for m in ms:
        is_6p = bool(m.pos_p5 and m.pos_p6)
        if is_6p:
            seats_html = f'<div class="seat-player pos-6-1">① {m.pos_north}</div><div class="seat-player pos-6-2">② {m.pos_p5}</div><div class="seat-player pos-6-3">③ {m.pos_east}</div><div class="seat-player pos-6-4">④ {m.pos_south}</div><div class="seat-player pos-6-5">⑤ {m.pos_p6}</div><div class="seat-player pos-6-6">⑥ {m.pos_west}</div>'
        else:
            seats_html = f'<div class="seat-player pos-4-n">[N] {m.pos_north}</div><div class="seat-player pos-4-e">[E] {m.pos_east}</div><div class="seat-player pos-4-s">[S] {m.pos_south}</div><div class="seat-player pos-4-w">[W] {m.pos_west}</div>'
        
        click_attr = f'data-bs-toggle="modal" data-bs-target="#m{m.id}"' if not m.is_completed and not is_panorama else ''
        card = f"""<div class="col-md-4 mb-4"><div class="glass-card p-3 shadow-sm" style="background: rgba(45, 55, 72, 0.4);"><div class="seat-wrapper">{seats_html}<div class="table-circle {'table-red' if m.is_completed else 'table-blue'}" {click_attr}>T-{m.table_no}</div></div><div class="mt-4 text-center bg-black bg-opacity-25 py-2 rounded"><span class="badge bg-primary px-3">{m.team_a_name}</span> VS <span class="badge bg-secondary px-3">{m.team_b_name}</span></div></div></div>"""
        
        if not is_panorama:
            card += f"""<div class="modal fade" id="m{m.id}"><div class="modal-dialog modal-dialog-centered"><div class="modal-content bg-dark border-info text-white shadow-lg"><form action="/save/{m.id}" method="post"><div class="modal-body p-5 text-center"><h4 class="mb-4 text-info fw-bold">{T('第','Table')} {m.table_no} {T('桌成绩','Score')}</h4><div class="row align-items-center mb-4"><div class="col-5"><label class="small mb-3 d-block text-white-50">{m.team_a_name}</label><input name="sa" type="number" class="form-control bg-secondary text-white text-center fs-2 fw-bold" required autofocus></div><div class="col-2 fs-2 text-info">:</div><div class="col-5"><label class="small mb-3 d-block text-white-50">{m.team_b_name}</label><input name="sb" type="number" class="form-control bg-secondary text-white text-center fs-2 fw-bold" required></div></div></div><div class="modal-footer border-0 p-4"><button class="btn btn-info w-100 py-3 fw-bold fs-5 shadow">{T('提交成绩','Submit')}</button></div></form></div></div></div>"""
        cards.append(card)
        
    next_btn = f'<div class="text-center mt-4"><a href="/next_r" class="btn btn-warning btn-lg px-5 py-3 fw-bold rounded-pill shadow-lg text-dark fs-4">🏁 {T("下一轮编排","Generate Next Round")}</a></div>' if ms and all(m.is_completed for m in ms) and not is_panorama else ""
    return "".join(cards), next_btn

@app.route('/matches')
def matches():
    if not session.get('logged_in'): return redirect(url_for('login'))
    t = get_active_t()
    if not t: return redirect(url_for('setup'))
    conf = get_config(t.id)
    cards_html, next_btn = generate_matches_html(t, conf, is_panorama=False)
    
    html = f'<div id="timer-box"><div class="small text-secondary text-center">{T("计时","Timer")}<br><input type="number" id="duration" class="bg-transparent text-info border-0 text-center fw-bold" style="width:55px; outline:none;" value="50"></div><div id="time-display">00:00</div><button onclick="startTimer()" class="btn btn-info px-4 fw-bold rounded-pill">{T("开始","Start")}</button><button id="pause-btn" onclick="togglePause()" class="btn btn-outline-warning px-4 fw-bold rounded-pill">{T("暂停","Pause")}</button></div><div class="row">{cards_html}</div>{next_btn}'
    return render_layout(html, "matches")

@app.route('/panorama')
def panorama():
    t = get_active_t()
    if not t: return "No active tournament"
    conf = get_config(t.id)
    cards_html, _ = generate_matches_html(t, conf, is_panorama=True)
    
    marquee = f'<div class="ad-ticker-pro w-100"><div class="ad-content">📢 {conf.scroll_ad} 📢</div></div>'
    
    html = f'{marquee}<div class="container-fluid px-5 mt-4"><div id="timer-box" style="transform: scale(1.2); margin-top:20px; margin-bottom: 50px;"><div id="time-display" style="margin:0 30px;">--:--</div></div><div class="row">{cards_html}</div></div><script>window.onload = function() {{ initPanoramaDisplay(); startTimer(); }};</script>'
    
    return render_layout(html, active="panorama", hide_nav=True)

@app.route('/save/<int:mid>', methods=['POST'])
def save(mid):
    m = Match.query.get(mid); t = get_active_t()
    t1, t2 = Team.query.get(m.team_a_id), Team.query.get(m.team_b_id)
    sa, sb = int(request.form.get('sa', 0)), int(request.form.get('sb', 0))
    
    m.score_a = sa; m.score_b = sb 
    
    t1.round_score += sa; t2.round_score += sb
    if sa > sb: t1.current_score += 3
    elif sb > sa: t2.current_score += 3
    else: t1.current_score += 1; t2.current_score += 1
    
    m.is_completed = True
    t1.history_opponents = (t1.history_opponents + f",{t2.id}").strip(',')
    t2.history_opponents = (t2.history_opponents + f",{t1.id}").strip(',')
    log_act("Submit Score", f"Table {m.table_no} | {m.team_a_name}({sa}) : {m.team_b_name}({sb})", t.id)
    db.session.commit()
    return redirect(url_for('matches'))

@app.route('/leaderboard')
def leaderboard():
    t = get_active_t()
    if not t: return redirect(url_for('setup'))
    conf = get_config(t.id)
    ts = Team.query.filter_by(tournament_id=t.id).all()
    ts.sort(key=lambda x: (x.current_score, x.round_score), reverse=True)
    
    rows = "".join([f"<tr><td>{i+1}</td><td class='text-info fw-bold'>{team.name}</td><td class='text-warning'>{team.current_score}</td><td class='text-success'>{team.round_score}</td><td>{team.players} <a href='#' data-bs-toggle='modal' data-bs-target='#editScore{team.id}' class='text-secondary ms-2' style='text-decoration:none;'>✎</a></td></tr>" + 
    f"""<div class='modal fade' id='editScore{team.id}'><div class='modal-dialog modal-dialog-centered'><div class='modal-content bg-dark text-white border-warning shadow-lg'><form action='/adjust_score/{team.id}' method='post'><div class='modal-body p-4 text-start'><h5 class='text-warning fw-bold mb-4'>{T('修正成绩', 'Adjust Score')}: {team.name}</h5><div class='mb-3'><label class='small opacity-75'>{T('累计胜分 (总分)', 'Win Pts')}</label><input name='c_score' type='number' class='form-control bg-secondary text-white border-0 py-2' value='{team.current_score}'></div><div class='mb-4'><label class='small opacity-75'>{T('累计级分 (小分)', 'Round Pts')}</label><input name='r_score' type='number' class='form-control bg-secondary text-white border-0 py-2' value='{team.round_score}'></div><button type='submit' class='btn btn-warning w-100 fw-bold py-2'>{T('确认修正', 'Confirm')}</button></div></form></div></div></div>""" 
    for i, team in enumerate(ts)])
    
    aw, as2, ab = (ts[0] if len(ts)>0 else None), (ts[1] if len(ts)>1 else None), (ts[2] if len(ts)>2 else None)
    award_modal = f"""<div class="modal fade" id="awardModal"><div class="modal-dialog modal-fullscreen"><div class="modal-content text-center text-white" style="background: radial-gradient(circle, #1e293b 0%, #0f172a 100%);"><audio id="victoryMusic" loop><source src="{conf.bg_music_url}" type="audio/mpeg"></audio><div class="container py-5"><h1 class="display-1 text-warning fw-bold mb-5">🏆 {t.name} {T('荣耀颁奖', 'Awards')}</h1><div class="glass-card p-5 border-warning w-75 mx-auto mb-5 shadow-lg"><p class="display-4 text-warning mb-2">🥇 {T('冠 军', 'Champion')}</p><h1 class="display-2 fw-bold">{aw.name if aw else '-'}</h1><p class='fs-2 text-info mt-3'>{aw.players if aw else ''}</p></div><div class="row w-75 mx-auto gap-4"><div class="col glass-card p-4 border-info"><p class="h2 text-info mb-2">🥈 {T('亚 军', 'Runner-up')}</p><h2>{as2.name if as2 else '-'}</h2><p class='text-info opacity-75 fs-4 mt-2'>{as2.players if as2 else ''}</p></div><div class="col glass-card p-4 border-light"><p class="h2 text-light mb-2">🥉 {T('季 军', 'Third Place')}</p><h2>{ab.name if ab else '-'}</h2><p class='text-light opacity-75 fs-4 mt-2'>{ab.players if ab else ''}</p></div></div><div class="mt-5"><button class="btn btn-outline-danger btn-lg rounded-pill" onclick="document.getElementById('victoryMusic').pause();" data-bs-dismiss="modal">{T('返回后台', 'Return')}</button></div></div><script>document.getElementById('awardModal').addEventListener('shown.bs.modal', function(){{ document.getElementById('victoryMusic').play().catch(e=>console.log('Block')); }});</script></div></div></div>"""
    
    return render_layout(f'<div class="d-flex justify-content-between align-items-center mb-4"><div><h3 class="text-info fw-bold m-0">📊 {T("实时积分排行榜","Live Leaderboard")}</h3><small class="text-white-50">{T("点击队员名旁的 ✎ 可手动微调成绩","Click ✎ to adjust score manually")}</small></div></div><div class="glass-card p-5 shadow-lg"><div class="table-responsive"><table class="table table-dark table-hover text-center align-middle"><thead><tr><th>{T("名次","Rank")}</th><th>{T("队伍","Team")}</th><th>{T("总胜分","Win Pts")}</th><th>{T("总级分","Round Pts")}</th><th>{T("选手 (编辑)","Players (Edit)")}</th></tr></thead><tbody>{rows}</tbody></table></div><button class="btn btn-warning w-100 py-4 mt-4 fw-bold rounded-pill shadow-lg fs-4" data-bs-toggle="modal" data-bs-target="#awardModal">🎊 {T("开启颁奖大屏", "Open Awards Screen")}</button></div>{award_modal}', "leaderboard")

@app.route('/adjust_score/<int:tid>', methods=['POST'])
def adjust_score(tid):
    if not session.get('logged_in'): return redirect(url_for('login'))
    t = get_active_t()
    team = Team.query.get(tid)
    if team:
        old_c, old_r = team.current_score, team.round_score
        team.current_score, team.round_score = int(request.form.get('c_score', 0)), int(request.form.get('r_score', 0))
        db.session.commit()
        log_act("Manual Score Adjust", f"Team:{team.name} | Win:{old_c}->{team.current_score}, Round:{old_r}->{team.round_score}", t.id)
    return redirect(url_for('leaderboard'))

@app.route('/info', methods=['GET', 'POST'])
def info():
    if not session.get('logged_in'): return redirect(url_for('login'))
    t = get_active_t()
    if not t: return redirect(url_for('setup'))
    info_data = TournamentInfo.query.filter_by(tournament_id=t.id).first()
    if not info_data: info_data = TournamentInfo(tournament_id=t.id); db.session.add(info_data); db.session.commit()
    if request.method == 'POST':
        info_data.t_date, info_data.t_location, info_data.t_sponsor, info_data.t_note = request.form['t_date'], request.form['t_location'], request.form['t_sponsor'], request.form['t_note']
        db.session.commit(); log_act("Update Tournament Info", "Updated Details", t.id); return redirect(url_for('info'))
    return render_layout(f'<h4>📋 {T("赛事详情档案","Tournament Info")}</h4><form method="post" class="mt-4"><div class="row mb-4"><div class="col"><label>{T("日期","Date")}</label><input name="t_date" class="form-control bg-dark text-white" value="{info_data.t_date or ""}"></div><div class="col"><label>{T("地点","Location")}</label><input name="t_location" class="form-control bg-dark text-white" value="{info_data.t_location or ""}"></div></div><div class="mb-4"><label>{T("赞助商","Sponsor")}</label><input name="t_sponsor" class="form-control bg-dark text-white" value="{info_data.t_sponsor or ""}"></div><div class="mb-4"><label>{T("赛事备注","Notes")}</label><textarea name="t_note" class="form-control bg-dark text-white" rows="8">{info_data.t_note or ""}</textarea></div><button class="btn btn-info w-100 py-3 rounded-pill fw-bold shadow">{T("更新档案数据","Update Info")}</button></form>', "info")

@app.route('/export_excel')
def export_excel():
    t = get_active_t()
    if not t: return "No active tournament"
    
    teams = Team.query.filter_by(tournament_id=t.id).all()
    teams.sort(key=lambda x: (x.current_score, x.round_score), reverse=True)
    
    export_data = []
    for i, team in enumerate(teams):
        row = {
            "排名 (Rank)": i+1,
            "队名 (Team)": team.name,
            "选手 (Players)": team.players,
            "总胜分 (Total Win)": team.current_score,
            "总级分 (Total Round)": team.round_score
        }
        
        matches = Match.query.filter(
            (Match.team_a_id == team.id) | (Match.team_b_id == team.id), 
            Match.tournament_id == t.id
        ).order_by(Match.round_no).all()
        
        for m in matches:
            if m.score_a != -1 and m.score_b != -1: 
                is_a = (m.team_a_id == team.id)
                my_score = m.score_a if is_a else m.score_b
                op_score = m.score_b if is_a else m.score_a
                
                win_pt = 3 if my_score > op_score else (0 if my_score < op_score else 1)
                row[f"第{m.round_no}轮 胜分 (R{m.round_no} Win)"] = win_pt
                row[f"第{m.round_no}轮 级分 (R{m.round_no} Round)"] = my_score
                
        export_data.append(row)
        
    df = pd.DataFrame(export_data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer: df.to_excel(writer, index=False)
    output.seek(0)
    log_act("Export Excel", "Downloaded full match records.", t.id)
    return send_file(output, as_attachment=True, download_name=f"Guandan_Record_{t.name}.xlsx")

@app.route('/logs')
def logs():
    if not session.get('logged_in'): return redirect(url_for('login'))
    t = get_active_t()
    ls = AuditLog.query.filter_by(tournament_id=t.id).order_by(AuditLog.timestamp.desc()).limit(200).all() if t else []
    rows = "".join([f"<tr><td>{l.timestamp.strftime('%H:%M:%S')}</td><td class='text-info'>{l.user}</td><td>{l.action}</td><td class='small opacity-75'>{l.details}</td></tr>" for l in ls])
    return render_layout(f'<div class="glass-card p-4 shadow"><h5>📜 {T("系统审计跟踪日志","System Audit Logs")} (V18.0)</h5><div class="table-responsive mt-3"><table class="table table-dark table-sm table-hover"><thead><tr><th>{T("时间","Time")}</th><th>{T("操作员","User")}</th><th>{T("动作","Action")}</th><th>{T("说明","Details")}</th></tr></thead><tbody>{rows}</tbody></table></div></div>', "logs")

@app.route('/users', methods=['GET', 'POST'])
def users():
    if not session.get('logged_in'): return redirect(url_for('login'))
    curr_user_name = session.get('username')
    t = get_active_t()
    conf = get_config(t.id) if t else None
    
    if request.method == 'POST':
        if 'upd_cfg' in request.form:
            conf.scroll_ad, conf.bg_music_url = request.form['scroll_ad'], request.form['bg_music_url']
            db.session.commit(); log_act("Update Settings", "Updated Scroll Text / Music", t.id)
            
        elif 'old_p' in request.form:
            u = User.query.filter_by(username=curr_user_name).first()
            p1, p2 = request.form['p1'], request.form['p2']
            if p1 != p2: return render_layout('<div class="alert alert-danger">Password mismatch!</div>', "users")
            u.password = generate_password_hash(p1); db.session.commit()
            log_act("User Change Password", f"User {curr_user_name} updated pwd")
            return render_layout('<div class="alert alert-success">Password updated!</div>', "users")
            
        elif 'admin_change_pwd' in request.form and curr_user_name == 'admin':
            target_u = User.query.get(request.form['target_uid'])
            if target_u:
                target_u.password = generate_password_hash(request.form['new_pwd'])
                db.session.commit()
                log_act("Admin Force Reset Pwd", f"Reset pwd for {target_u.username}")
                return render_layout(f'<div class="alert alert-success mt-4">Reset {target_u.username} password success!</div>', "users")
                
        elif 'u' in request.form and curr_user_name == 'admin':
            u_name, p1, p2 = request.form['u'], request.form['p'], request.form['p2']
            if p1 != p2: return "Password mismatch!"
            db.session.add(User(username=u_name, password=generate_password_hash(p1)))
            db.session.commit(); log_act("Add New Admin", f"Created user: {u_name}")

    us = User.query.all() if curr_user_name == 'admin' else User.query.filter_by(username=curr_user_name).all()
    u_rows = ""
    for u in us:
        if curr_user_name == 'admin':
            action_btns = f"<a href='/toggle_user/{u.id}' class='btn btn-sm btn-outline-warning me-1'>{'解锁 Unlock' if u.is_locked else '锁定 Lock'}</a>"
            action_btns += f"<button class='btn btn-sm btn-outline-danger' data-bs-toggle='modal' data-bs-target='#resetPwd{u.id}'>{T('改密', 'Reset Pwd')}</button>"
            modal_html = f"<div class='modal fade' id='resetPwd{u.id}'><div class='modal-dialog modal-dialog-centered'><div class='modal-content bg-dark text-white border-danger shadow-lg'><form method='post'><div class='modal-body p-4 text-start'><h5 class='mb-4 text-danger fw-bold'>Force Reset: {u.username}</h5><input type='hidden' name='admin_change_pwd' value='1'><input type='hidden' name='target_uid' value='{u.id}'><div class='mb-3'><input type='password' name='new_pwd' class='form-control bg-secondary text-white border-0 py-2' placeholder='New Password' required></div></div><div class='modal-footer border-0 p-4'><button type='submit' class='btn btn-danger w-100 fw-bold py-2'>Confirm</button></div></form></div></div></div>"
            u_rows += f"<tr><td>{u.username}</td><td>{'<span class=\"badge bg-danger\">Locked</span>' if u.is_locked else '<span class=\"badge bg-success\">Active</span>'}</td><td>{action_btns}</td></tr>{modal_html}"
        else:
            u_rows += f"<tr><td>{u.username}</td><td>{'Locked' if u.is_locked else 'Active'}</td><td>-</td></tr>"

    change_self_pwd_form = f"""<div class="mt-4 pt-3 border-top border-secondary"><h6 class="text-warning small fw-bold mb-3">🔑 {T('修改个人密码', 'Change Personal Pwd')}</h6><form method="post"><div class="mb-2"><input type="password" name="p1" class="form-control form-control-sm bg-dark text-white border-secondary" placeholder="New Password" required></div><div class="mb-3"><input type="password" name="p2" class="form-control form-control-sm bg-dark text-white border-secondary" placeholder="Confirm Password" required></div><input type="hidden" name="old_p" value="1"><button class="btn btn-outline-warning w-100 rounded-pill btn-sm py-2">Submit</button></form></div>"""

    add_user_form = f"""<form method="post" class="mt-4 pt-3 border-top border-secondary"><h6 class="text-info small fw-bold mb-3">👤 {T('添加新管理员', 'Add Admin')}</h6><input name="u" class="form-control bg-dark text-white mb-2" placeholder="Account" required><input name="p" type="password" class="form-control bg-dark text-white mb-2" placeholder="Password" required><input name="p2" type="password" class="form-control bg-dark text-white mb-3" placeholder="Confirm Pwd" required><button class="btn btn-outline-info w-100 rounded-pill btn-sm py-2">Add Admin</button></form>""" if curr_user_name == 'admin' else ""

    return render_layout(f"""<div class="row"><div class="col-md-7"><div class="glass-card p-4 mb-4 shadow"><h5 class="text-info fw-bold mb-4">📣 {T('赛场环境配置','System Configuration')}</h5><form method="post"><input type="hidden" name="upd_cfg" value="1"><div class="mb-4"><label class="small text-white-50">{T('滚动公告 (将在大屏显示)','Scroll Marquee Text')}</label><textarea name="scroll_ad" class="form-control bg-dark text-white" rows="4">{conf.scroll_ad if conf else ""}</textarea></div><div class="mb-4"><label class="small text-white-50">{T('背景音乐 URL','Background Music URL')}</label><input name="bg_music_url" class="form-control bg-dark text-white" value="{conf.bg_music_url if conf else ""}"></div><button class="btn btn-info w-100 rounded-pill py-2">{T('保存配置','Save')}</button></form></div></div><div class="col-md-5"><div class="glass-card p-4 shadow"><h5 class="text-info fw-bold mb-4">🔐 {T('权限管理', 'Role Management')}</h5><table class="table table-dark table-sm align-middle">{u_rows}</table>{change_self_pwd_form}{add_user_form}</div></div></div>""", "users")

# -- 辅助路由还原 --
@app.route('/toggle_user/<int:uid>')
def toggle_user(uid):
    if session.get('username') != 'admin': abort(403)
    u = User.query.get(uid); u.is_locked = not u.is_locked
    db.session.commit(); log_act("Toggle User Lock", f"Target: {u.username}")
    return redirect(url_for('users'))

@app.route('/view_history/<int:tid>')
def view_history(tid):
    if not session.get('logged_in'): return redirect(url_for('login'))
    curr_user = session.get('username')
    if curr_user == 'admin':
        Tournament.query.update({Tournament.is_active: False})
        t = Tournament.query.filter_by(id=tid).first()
    else:
        Tournament.query.filter_by(owner=curr_user).update({Tournament.is_active: False})
        t = Tournament.query.filter_by(id=tid, owner=curr_user).first()
    if t: 
        t.is_active = True; db.session.commit(); log_act("Switch Active Tournament", f"Target: {t.name}")
    return redirect(url_for('setup'))

@app.route('/del_team/<int:tid>')
def del_team(tid):
    t = get_active_t()
    tm = Team.query.get(tid)
    if tm: db.session.delete(tm); db.session.commit(); log_act("Delete Team", f"Target: {tm.name}", t.id if t else None)
    return redirect(url_for('setup'))

@app.route('/edit_team/<int:tid>', methods=['POST'])
def edit_team_data(tid):
    t = get_active_t()
    tm = Team.query.get(tid)
    if tm:
        tm.name, tm.players = request.form['name'], request.form['players']
        db.session.commit(); log_act("Edit Team", f"Target: {tm.name}", t.id if t else None)
    return redirect(url_for('setup'))

@app.route('/next_r')
def next_r():
    t = get_active_t()
    conf = get_config(t.id); conf.current_round += 1
    for i, (t1, t2) in enumerate(swiss_pairing(t.id, conf.current_round)):
        p1, p2 = [x.strip() for x in t1.players.replace('，',',').split(',')], [x.strip() for x in t2.players.replace('，',',').split(',')]
        # 6人赛：每队恰好3名队员；4人赛：每队恰好2名队员
        if len(p1) >= 3 and len(p2) >= 3:
            # 6人赛桌：A队占①③⑤位，B队占②④⑥位（对面而坐）
            db.session.add(Match(tournament_id=t.id, round_no=conf.current_round, table_no=i+1, team_a_id=t1.id, team_b_id=t2.id, team_a_name=t1.name, team_b_name=t2.name, pos_north=p1[0].strip(), pos_p5=p2[0].strip(), pos_east=p1[1].strip(), pos_south=p2[1].strip(), pos_p6=p1[2].strip(), pos_west=p2[2].strip()))
        else:
            # 4人赛桌：A队占北/南位置，B队占东/西位置
            db.session.add(Match(tournament_id=t.id, round_no=conf.current_round, table_no=i+1, team_a_id=t1.id, team_b_id=t2.id, team_a_name=t1.name, team_b_name=t2.name, pos_north=p1[0].strip() if len(p1)>0 else "P1", pos_east=p2[0].strip() if len(p2)>0 else "P2", pos_south=p1[1].strip() if len(p1)>1 else "P3", pos_west=p2[1].strip() if len(p2)>1 else "P4", pos_p5="", pos_p6=""))
    log_act("Next Round", f"Round {conf.current_round}", t.id); db.session.commit()
    return redirect(url_for('matches'))

@app.route('/logout')
def logout():
    session.clear(); return redirect(url_for('login'))

def init_db():
    with app.app_context():
        db.create_all()
        try: db.session.execute(text("ALTER TABLE match ADD COLUMN pos_p5 VARCHAR(50)")); db.session.commit()
        except Exception: db.session.rollback()
        try: db.session.execute(text("ALTER TABLE match ADD COLUMN pos_p6 VARCHAR(50)")); db.session.commit()
        except Exception: db.session.rollback()
        try: db.session.execute(text("ALTER TABLE match ADD COLUMN score_a INTEGER DEFAULT -1")); db.session.commit()
        except Exception: db.session.rollback()
        try: db.session.execute(text("ALTER TABLE match ADD COLUMN score_b INTEGER DEFAULT -1")); db.session.commit()
        except Exception: db.session.rollback()
        if not User.query.filter_by(username='admin').first():
            db.session.add(User(username='admin', password=generate_password_hash('123')))
            db.session.commit()

init_db()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)