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
MONITOR_API_URL = os.environ.get('MONITOR_API_URL', '')
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
    # 小组赛扩展字段
    mode = db.Column(db.Integer, default=0)             # 0=普通循环赛, 1=小组赛模式
    num_groups = db.Column(db.Integer, default=0)       # 分几组
    advance_per_group = db.Column(db.Integer, default=0)# 每组出线名额
    stage = db.Column(db.String(20), default=None)      # 'group' | 'finals' | None
    pairing_mode = db.Column(db.String(20), default='swiss')  # 'swiss' | 'roundrobin'

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
    seat_ns_count = db.Column(db.Integer, default=0)   # 北/南方向（或6人赛①③⑤位）出场次数
    seat_ew_count = db.Column(db.Integer, default=0)   # 东/西方向（或6人赛②④⑥位）出场次数
    had_bye = db.Column(db.Boolean, default=False)     # 是否已获得过拜轮
    # 小组赛扩展字段
    group_id = db.Column(db.Integer, default=0)        # 所属组号(1起)，0=普通/决赛模式
    is_finalist = db.Column(db.Boolean, default=False) # 是否晋级决赛

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
    group_id = db.Column(db.Integer, default=0)  # 小组赛时所属组号，0=普通/决赛

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

def _backtrack_pair(teams, round_no, total_r, used, pairs, idx):
    """回溯配对核心：确保所有队伍都能完成配对，无死锁"""
    # 跳过已配对队伍
    while idx < len(teams) and teams[idx].id in used:
        idx += 1
    if idx >= len(teams):
        return list(pairs) if len(used) == len(teams) else None
    current = teams[idx]
    candidates = [t for t in teams[idx+1:] if t.id not in used]
    # 两轮尝试：第一轮避免重复对阵；第二轮（最后轮或被迫时）允许重复
    allow_rematch_rounds = [False, True] if round_no < total_r else [True]
    for allow_rematch in allow_rematch_rounds:
        for opp in candidates:
            history = current.history_opponents.split(',') if current.history_opponents else []
            if str(opp.id) in history and not allow_rematch:
                continue
            used.add(current.id); used.add(opp.id)
            pairs.append((current, opp))
            result = _backtrack_pair(teams, round_no, total_r, used, pairs, idx + 1)
            if result is not None:
                return result
            used.discard(current.id); used.discard(opp.id)
            pairs.pop()
    return None

def assign_seats(t1, t2, p1, p2, is_6p):
    """
    座位方向平衡分配（国际标准：每队各方向出场次数尽量均等）
    t1 NS次数 <= EW次数 → 本轮给 t1 分配 NS；否则给 EW。
    """
    t1_wants_ns = (t1.seat_ns_count <= t1.seat_ew_count)
    if is_6p:
        if t1_wants_ns:
            # t1 → ①③⑤位（north/east/p6），t2 → ②④⑥位（p5/south/west）
            seats = dict(pos_north=p1[0], pos_east=p1[1], pos_p6=p1[2],
                         pos_p5=p2[0], pos_south=p2[1], pos_west=p2[2])
            t1.seat_ns_count += 1; t2.seat_ew_count += 1
        else:
            seats = dict(pos_north=p2[0], pos_east=p2[1], pos_p6=p2[2],
                         pos_p5=p1[0], pos_south=p1[1], pos_west=p1[2])
            t1.seat_ew_count += 1; t2.seat_ns_count += 1
    else:
        if t1_wants_ns:
            # t1 → 北/南，t2 → 东/西
            seats = dict(pos_north=p1[0] if len(p1)>0 else 'P1',
                         pos_south=p1[1] if len(p1)>1 else 'P3',
                         pos_east=p2[0] if len(p2)>0 else 'P2',
                         pos_west=p2[1] if len(p2)>1 else 'P4',
                         pos_p5='', pos_p6='')
            t1.seat_ns_count += 1; t2.seat_ew_count += 1
        else:
            seats = dict(pos_north=p2[0] if len(p2)>0 else 'P1',
                         pos_south=p2[1] if len(p2)>1 else 'P3',
                         pos_east=p1[0] if len(p1)>0 else 'P2',
                         pos_west=p1[1] if len(p1)>1 else 'P4',
                         pos_p5='', pos_p6='')
            t1.seat_ew_count += 1; t2.seat_ns_count += 1
    return seats

def swiss_pairing(t_id, round_no):
    """
    改进版瑞士制配对算法 V2（符合国际标准）
    改进点：
      1. 回溯算法 — 保证配对完整，无死锁
      2. 拜轮处理 — 奇数队时积分最低且未曾拜轮的队获得拜轮（自动 3 胜分）
      3. 座位方向平衡 — 见 assign_seats()
    返回 (pairs, bye_team)，bye_team 为 None 或获得拜轮的队
    """
    teams = Team.query.filter_by(tournament_id=t_id).all()
    teams.sort(key=lambda x: (x.current_score, x.round_score), reverse=True)
    total_r = get_config(t_id).total_rounds
    bye_team = None
    working = list(teams)
    # 拜轮处理：奇数队时，从积分最低队开始找未曾拜轮者
    if len(working) % 2 == 1:
        for team in reversed(working):
            if not team.had_bye:
                bye_team = team; break
        if bye_team is None:
            bye_team = working[-1]  # 所有队都曾拜轮，给积分最低者再次拜轮
        working = [t for t in working if t.id != bye_team.id]
    # 回溯配对
    result = _backtrack_pair(working, round_no, total_r, set(), [], 0)
    if result is None:
        # 极端兜底：顺序强制配对
        result = [(working[i], working[i+1]) for i in range(0, len(working)-1, 2)]
    return result, bye_team

def roundrobin_pairing(t_id, round_no):
    """纯粹轮流循环赛：按固定顺序避免重复对阵，不按积分排序"""
    teams = Team.query.filter_by(tournament_id=t_id).all()
    teams.sort(key=lambda x: x.id)
    n = len(teams)
    total_r = (n - 1) if n % 2 == 0 else n  # 每队与所有其他队各赛一场所需轮次
    bye_team = None
    working = list(teams)
    if len(working) % 2 == 1:
        for team in reversed(working):
            if not team.had_bye: bye_team = team; break
        if bye_team is None: bye_team = working[-1]
        working = [t for t in working if t.id != bye_team.id]
    result = _backtrack_pair(working, round_no, total_r, set(), [], 0)
    if result is None:
        result = [(working[i], working[i+1]) for i in range(0, len(working)-1, 2)]
    return result, bye_team

def group_roundrobin_pairing(t_id, round_no, group_id):
    """小组纯粹轮流循环赛：组内按固定顺序避免重复对阵，不按积分排序"""
    teams = Team.query.filter_by(tournament_id=t_id, group_id=group_id).all()
    teams.sort(key=lambda x: x.id)
    n = len(teams)
    total_r = (n - 1) if n % 2 == 0 else n
    bye_team = None
    working = list(teams)
    if len(working) % 2 == 1:
        for team in reversed(working):
            if not team.had_bye: bye_team = team; break
        if bye_team is None: bye_team = working[-1]
        working = [t for t in working if t.id != bye_team.id]
    result = _backtrack_pair(working, round_no, total_r, set(), [], 0)
    if result is None:
        result = [(working[i], working[i+1]) for i in range(0, len(working)-1, 2)]
    return result, bye_team

# --- 2b. 小组赛辅助函数（新代码，不影响现有逻辑）---

def validate_group_config(num_teams, num_groups, advance_per_group):
    """验证分组配置是否符合国际规则。返回 (valid:bool, msg:str)"""
    if num_groups < 2:
        return False, "至少需要分2组"
    max_groups = num_teams // 3
    if max_groups < num_groups:
        return False, f"{num_teams}支队伍最多可分{max_groups}组（每组至少3支队）"
    min_per_group = num_teams // num_groups
    if advance_per_group >= min_per_group:
        return False, f"每组约{min_per_group}队，出线名额必须小于每组队数（当前设{advance_per_group}）"
    total_finalists = advance_per_group * num_groups
    if total_finalists < 2:
        return False, "决赛至少需要2支队伍"
    return True, f"配置合理：{num_groups}组，每组出线{advance_per_group}名，共{total_finalists}支队伍参加决赛"

def group_swiss_pairing(t_id, round_no, group_id):
    """针对单个小组内的瑞士制配对，逻辑与 swiss_pairing 完全一致"""
    teams = Team.query.filter_by(tournament_id=t_id, group_id=group_id).all()
    teams.sort(key=lambda x: (x.current_score, x.round_score), reverse=True)
    total_r = get_config(t_id).total_rounds
    bye_team = None
    working = list(teams)
    if len(working) % 2 == 1:
        for team in reversed(working):
            if not team.had_bye:
                bye_team = team; break
        if bye_team is None:
            bye_team = working[-1]
        working = [t for t in working if t.id != bye_team.id]
    result = _backtrack_pair(working, round_no, total_r, set(), [], 0)
    if result is None:
        result = [(working[i], working[i+1]) for i in range(0, len(working)-1, 2)]
    return result, bye_team

def get_group_qualifiers(t_id):
    """按组取出线队伍（每组前 advance_per_group 名），返回 list of Team"""
    conf = get_config(t_id)
    qualifiers = []
    for g in range(1, conf.num_groups + 1):
        group_teams = Team.query.filter_by(tournament_id=t_id, group_id=g).all()
        group_teams.sort(key=lambda x: (x.current_score, x.round_score), reverse=True)
        qualifiers.extend(group_teams[:conf.advance_per_group])
    return qualifiers

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
        #timer-box {{ background: rgba(30, 41, 59, 0.8); border: 2px solid #3b82f6; border-radius: 60px; width: fit-content; margin: 0 auto 30px; padding: 12px 40px; display: flex; align-items: center; gap: 25px; box-shadow: 0 0 30px rgba(59,130,246,0.3); transition: all 0.4s ease; }}
        #time-display {{ font-size: 2.2rem; font-weight: 800; font-family: 'Courier New', monospace; color: #3b82f6; text-shadow: 0 0 10px rgba(59,130,246,0.5); transition: font-size 0.4s ease; }}
        #timer-box.enlarged {{ width: 100%; height: 33vh; border-radius: 20px; justify-content: center; gap: 40px; padding: 0 60px; box-sizing: border-box; }}
        #timer-box.enlarged #time-display {{ font-size: 20vh; line-height: 1; }}
        
        .ad-ticker-pro {{ background: linear-gradient(90deg, #991b1b, #ef4444, #991b1b); border-bottom: 2px solid #fbbf24; height: 50px; line-height: 50px; overflow: hidden; position: relative; z-index: 1040; box-shadow: 0 5px 15px rgba(0,0,0,0.5); }}
        .ad-content {{ position: absolute; white-space: nowrap; animation: ticker 25s linear infinite; font-weight: bold; color: #ffffff; font-size: 1.4rem; letter-spacing: 2px; text-shadow: 1px 1px 3px black; }}
        @keyframes ticker {{ 0% {{ left: 100%; }} 100% {{ left: -200%; }} }}
        
        .table-dark {{ --bs-table-bg: rgba(30, 41, 59, 0.5); border-color: rgba(255,255,255,0.05); }}
        .btn-info {{ background: #06b6d4; border: none; color: #000; font-weight: bold; }}
    </style>
    <script>
        let timer; let timeLeft; let isPaused = false;
        let fiveMinAlertDone = false; let bgAudio = null;
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
            fiveMinAlertDone = false;
            stopBgMusic();
            updateTimerDisplay();
            timer = setInterval(() => {{
                if(!isPaused) {{
                    if(timeLeft <= 0) {{ clearInterval(timer); stopBgMusic(); let td = document.getElementById('time-display'); if(td) {{ td.innerText = "FINISH"; td.style.color = "#ef4444"; }} }}
                    else {{ timeLeft--; updateTimerDisplay(); if(timeLeft === 300 && !fiveMinAlertDone) {{ fiveMinAlertDone = true; startFiveMinuteAlert(); }} }}
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
        function toggleClock() {{
            const box = document.getElementById('timer-box');
            const btn = document.getElementById('zoom-btn');
            if (!box) return;
            if (box.classList.contains('enlarged')) {{
                box.classList.remove('enlarged');
                if (btn) btn.innerText = '⤢ 放大时钟';
            }} else {{
                box.classList.add('enlarged');
                if (btn) btn.innerText = '⤡ 缩小时钟';
            }}
        }}
        function stopBgMusic() {{
            if(bgAudio) {{ bgAudio.pause(); bgAudio.currentTime = 0; bgAudio = null; }}
        }}
        function startFiveMinuteAlert() {{
            stopBgMusic();
            bgAudio = new Audio('/static/kenny_g_going_home.mp3');
            bgAudio.loop = true;
            bgAudio.volume = 0.75;
            bgAudio.play().catch(function(e) {{ console.warn('Audio play failed:', e); }});
            if(!('speechSynthesis' in window)) return;
            const zhText = '女士们，先生们，比赛已经进入五分钟倒计时，请没有赛完的选手们抓紧时间结束比赛，刚刚赛完的请尽快启动最后一轮比赛，祝大家愉快并取得好成绩！';
            const enText = 'Ladies and gentlemen, the tournament has entered the final five-minute countdown. Please finish your current game as soon as possible. If you have just finished, please start your final round immediately. We wish everyone a wonderful time and great results!';
            var _spoken = false;
            function doSpeak() {{
                if(_spoken) return; _spoken = true;
                var voices = window.speechSynthesis.getVoices();
                var zhVoice = voices.find(function(v) {{ return v.lang === 'zh-TW'; }})
                           || voices.find(function(v) {{ return v.lang.indexOf('zh-TW') === 0; }})
                           || voices.find(function(v) {{ return v.lang.indexOf('zh') === 0; }});
                var enVoice = voices.find(function(v) {{ return v.lang === 'en-US'; }})
                           || voices.find(function(v) {{ return v.lang.indexOf('en') === 0; }});
                window.speechSynthesis.resume();
                window.speechSynthesis.cancel();
                var uZh = new SpeechSynthesisUtterance(zhText);
                uZh.lang = 'zh-TW'; uZh.rate = 0.85; uZh.pitch = 1.15; uZh.volume = 1.0;
                if(zhVoice) uZh.voice = zhVoice;
                var uEn = new SpeechSynthesisUtterance(enText);
                uEn.lang = 'en-US'; uEn.rate = 0.88; uEn.pitch = 1.1; uEn.volume = 1.0;
                if(enVoice) uEn.voice = enVoice;
                uZh.onend = function() {{ window.speechSynthesis.resume(); window.speechSynthesis.speak(uEn); }};
                setTimeout(function() {{ window.speechSynthesis.speak(uZh); }}, 50);
            }}
            if(window.speechSynthesis.getVoices().length > 0) {{
                doSpeak();
            }} else {{
                window.speechSynthesis.onvoiceschanged = function() {{ doSpeak(); }};
                setTimeout(doSpeak, 800);
            }}
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
    {'<script>(function(){{var _m="{monitor_url}";if(!_m)return;try{{fetch(_m+"/api/visit",{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{page:"{page_name}"}})}}). catch(function(){{}});}}catch(e){{}}}})()</script>'.format(monitor_url=MONITOR_API_URL, page_name=('gs-' + active) if active else ('gs-login' if is_login else 'gs-home')) if MONITOR_API_URL else ''}
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
        .login-card-pro {{ background: rgba(15, 23, 42, 0.88); backdrop-filter: blur(24px); border: 1px solid rgba(251, 191, 36, 0.45); box-shadow: 0 30px 60px -12px rgba(0, 0, 0, 0.9), inset 0 1px 0 rgba(255,255,255,0.06); z-index: 10; width: 100%; max-width: 500px; border-radius: 24px; }}
        .logo-box {{ width: 130px; height: 130px; border-radius: 50%; border: 3px solid #fbbf24; box-shadow: 0 0 36px rgba(251, 191, 36, 0.55); object-fit: cover; margin-top: -65px; margin-bottom: 16px; background: #fff; padding: 2px; }}
        .login-title {{ font-family: 'STKaiti', 'KaiTi', serif; font-size: 1.85rem; letter-spacing: 4px; white-space: nowrap; text-shadow: 0 2px 8px rgba(0,0,0,0.9); }}
        .login-subtitle-en {{ font-size: 0.72rem; letter-spacing: 3px; color: rgba(251,191,36,0.55); text-transform: uppercase; margin-top: 2px; margin-bottom: 6px; }}
        .login-divider {{ width: 60px; height: 2px; background: linear-gradient(90deg, transparent, rgba(212,172,13,0.7), transparent); margin: 12px auto 24px; border-radius: 2px; }}
        .btn-gold {{ background: linear-gradient(180deg, #fcd34d 0%, #f59e0b 100%); color: #451a03; border: none; transition: 0.3s; letter-spacing: 2px; }}
        .btn-gold:hover {{ background: linear-gradient(180deg, #fde68a 0%, #d97706 100%); transform: translateY(-2px); box-shadow: 0 10px 25px rgba(245, 158, 11, 0.5); }}
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
            <h1 class="text-warning mb-0 fw-bold login-title">硅谷掼蛋管理系统</h1>
            <p class="login-subtitle-en">Silicon Valley Guandan · Management System</p>
            <p class="text-white-50 mb-0 small" style="letter-spacing: 1px; font-size:0.7rem;">Tournament Management V18.0</p>
            <div class="login-divider"></div>
            <form method="post">
                <div class="form-floating mb-3 text-dark">
                    <input name="u" class="form-control fw-bold border-0 bg-light" id="u" placeholder="Account" required>
                    <label for="u">🪪 {T('管理账号', 'Account')}</label>
                </div>
                <div class="form-floating mb-4 text-dark">
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
    
    init_modal = f"""
<div class="modal fade" id="initModal" tabindex="-1">
  <div class="modal-dialog modal-dialog-centered modal-lg">
    <div class="modal-content border-info shadow-lg" style="background:#1e293b;color:#f1f5f9;">
      <div class="modal-header" style="border-bottom:1px solid #06b6d4;">
        <h5 class="modal-title fw-bold" style="color:#06b6d4;">🎲 {T('选择赛制','Select Format')}</h5>
        <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body p-4">
        <p class="text-white-50 small mb-3">当前参赛队数：<strong style="color:#06b6d4;">{len(teams)}</strong> 支</p>

        <!-- 选项1 -->
        <label style="display:block;cursor:pointer;padding:16px;border-radius:10px;border:2px solid rgba(6,182,212,0.3);margin-bottom:12px;background:rgba(6,182,212,0.05);" onclick="selectMode(1)">
          <input type="radio" name="formatMode" id="mode1" value="1" style="width:18px;height:18px;vertical-align:middle;margin-right:10px;accent-color:#06b6d4;">
          <span style="font-size:1.1rem;font-weight:700;">🔄 不分组 · 直接随机循环赛</span>
          <div style="color:#94a3b8;font-size:0.85rem;margin-top:6px;margin-left:28px;">所有队伍直接进入循环赛</div>
        </label>

        <!-- 选项2 -->
        <label style="display:block;cursor:pointer;padding:16px;border-radius:10px;border:2px solid rgba(251,191,36,0.3);margin-bottom:12px;background:rgba(251,191,36,0.05);" onclick="selectMode(2)">
          <input type="radio" name="formatMode" id="mode2" value="2" style="width:18px;height:18px;vertical-align:middle;margin-right:10px;accent-color:#fbbf24;">
          <span style="font-size:1.1rem;font-weight:700;color:#fbbf24;">🏆 分小组赛</span>
          <div style="color:#94a3b8;font-size:0.85rem;margin-top:6px;margin-left:28px;">先小组循环赛，各组前几名再参加决赛循环赛</div>
        </label>

        <!-- 配对方式选择（选择任意赛制后显示）-->
        <div id="pairingTypeSection" style="display:none;padding:14px 16px;border-radius:10px;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.12);margin-bottom:12px;">
          <div style="font-size:0.88rem;color:#94a3b8;margin-bottom:10px;font-weight:600;">⚙️ 配对方式</div>
          <div style="display:flex;gap:24px;flex-wrap:wrap;">
            <label style="cursor:pointer;color:#f1f5f9;font-size:0.95rem;display:flex;align-items:center;gap:6px;" onclick="event.stopPropagation()">
              <input type="radio" name="pairingType" id="pt_swiss" value="swiss" checked style="accent-color:#06b6d4;width:16px;height:16px;">
              🎯 瑞士制循环赛
            </label>
            <label style="cursor:pointer;color:#f1f5f9;font-size:0.95rem;display:flex;align-items:center;gap:6px;" onclick="event.stopPropagation()">
              <input type="radio" name="pairingType" id="pt_rr" value="roundrobin" style="accent-color:#06b6d4;width:16px;height:16px;">
              🔁 纯粹轮流循环赛
            </label>
          </div>
          <div style="font-size:0.78rem;color:#64748b;margin-top:8px;">瑞士制：按积分高低配对 &nbsp;|&nbsp; 纯粹轮流：每队依次与所有其他队各赛一场，不按积分配对</div>
        </div>

        <!-- 分小组赛配置（默认隐藏） -->
        <div id="groupConfig" style="display:none;padding:16px;border-radius:10px;background:rgba(251,191,36,0.08);border:1px solid rgba(251,191,36,0.3);margin-bottom:12px;">
          <div style="display:flex;gap:16px;margin-bottom:12px;">
            <div style="flex:1;">
              <label style="font-size:0.85rem;color:#94a3b8;display:block;margin-bottom:4px;">分几组？</label>
              <input type="number" id="num_groups" min="2" max="8" value="2" oninput="validateGroups()"
                style="width:100%;padding:8px 12px;border-radius:8px;border:1px solid #475569;background:#0f172a;color:#f1f5f9;font-size:1rem;">
            </div>
            <div style="flex:1;">
              <label style="font-size:0.85rem;color:#94a3b8;display:block;margin-bottom:4px;">每组出线名额</label>
              <input type="number" id="adv_pg" min="1" max="6" value="2" oninput="validateGroups()"
                style="width:100%;padding:8px 12px;border-radius:8px;border:1px solid #475569;background:#0f172a;color:#f1f5f9;font-size:1rem;">
            </div>
          </div>
          <div id="groupValidMsg" style="display:none;padding:8px 12px;border-radius:8px;font-size:0.85rem;margin-bottom:8px;"></div>
        </div>

        <!-- 确认按钮 -->
        <button id="confirmBtn" onclick="doConfirm()" disabled
          style="width:100%;padding:14px;border-radius:10px;border:none;background:#475569;color:#94a3b8;font-size:1.1rem;font-weight:700;cursor:not-allowed;margin-top:4px;">
          请先选择赛制
        </button>
      </div>
    </div>
  </div>
</div>

<script>
var totalTeams={len(teams)};
var selectedMode=0;

function selectMode(m){{
  selectedMode=m;
  document.getElementById('mode1').checked=(m===1);
  document.getElementById('mode2').checked=(m===2);
  var l1=document.getElementById('mode1').parentElement;
  var l2=document.getElementById('mode2').parentElement;
  l1.style.borderColor=m===1?'#06b6d4':'rgba(6,182,212,0.3)';
  l1.style.background=m===1?'rgba(6,182,212,0.12)':'rgba(6,182,212,0.05)';
  l2.style.borderColor=m===2?'#fbbf24':'rgba(251,191,36,0.3)';
  l2.style.background=m===2?'rgba(251,191,36,0.12)':'rgba(251,191,36,0.05)';
  document.getElementById('groupConfig').style.display=m===2?'block':'none';
  document.getElementById('pairingTypeSection').style.display='block';
  if(m===1){{
    var btn=document.getElementById('confirmBtn');
    btn.disabled=false;btn.style.background='linear-gradient(135deg,#0ea5e9,#06b6d4)';
    btn.style.color='#fff';btn.style.cursor='pointer';btn.textContent='✅ 确认 · 不分组循环赛';
  }} else {{
    validateGroups();
  }}
}}

function getPairingMode(){{
  var el=document.querySelector('input[name="pairingType"]:checked');
  return el?el.value:'swiss';
}}

function validateGroups(){{
  var g=parseInt(document.getElementById('num_groups').value)||0;
  var a=parseInt(document.getElementById('adv_pg').value)||0;
  var msg=document.getElementById('groupValidMsg');
  var btn=document.getElementById('confirmBtn');
  msg.style.display='block';
  if(g<2){{msg.style.background='rgba(239,68,68,0.15)';msg.style.color='#fca5a5';msg.textContent='❌ 至少需要分2组';btn.disabled=true;btn.style.background='#475569';btn.style.color='#94a3b8';btn.style.cursor='not-allowed';btn.textContent='配置有误，无法提交';return;}}
  var maxG=Math.floor(totalTeams/3);
  if(g>maxG){{msg.style.background='rgba(239,68,68,0.15)';msg.style.color='#fca5a5';msg.textContent='❌ '+totalTeams+'支队最多分'+maxG+'组（每组至少3队）';btn.disabled=true;btn.style.background='#475569';btn.style.color='#94a3b8';btn.style.cursor='not-allowed';btn.textContent='配置有误，无法提交';return;}}
  var minPer=Math.floor(totalTeams/g);
  if(a>=minPer){{msg.style.background='rgba(239,68,68,0.15)';msg.style.color='#fca5a5';msg.textContent='❌ 每组约'+minPer+'队，出线名额须小于每组队数';btn.disabled=true;btn.style.background='#475569';btn.style.color='#94a3b8';btn.style.cursor='not-allowed';btn.textContent='配置有误，无法提交';return;}}
  if(a*g<2){{msg.style.background='rgba(239,68,68,0.15)';msg.style.color='#fca5a5';msg.textContent='❌ 决赛至少需要2支队';btn.disabled=true;btn.style.background='#475569';btn.style.color='#94a3b8';btn.style.cursor='not-allowed';btn.textContent='配置有误，无法提交';return;}}
  msg.style.background='rgba(34,197,94,0.15)';msg.style.color='#86efac';
  msg.textContent='✅ 配置合理：'+g+'组，每组出线'+a+'名，共'+(a*g)+'支队进决赛';
  btn.disabled=false;btn.style.background='linear-gradient(135deg,#f59e0b,#fbbf24)';
  btn.style.color='#000';btn.style.cursor='pointer';btn.textContent='✅ 确认 · 开始分组循环赛';
}}

function doConfirm(){{
  var pm=getPairingMode();
  if(selectedMode===1){{
    var form=document.createElement('form');
    form.method='POST';form.action='/init_game';
    var fp=document.createElement('input');fp.type='hidden';fp.name='pairing_mode';fp.value=pm;
    form.appendChild(fp);document.body.appendChild(form);form.submit();
  }} else if(selectedMode===2){{
    var g=document.getElementById('num_groups').value;
    var a=document.getElementById('adv_pg').value;
    var form=document.createElement('form');
    form.method='POST';form.action='/init_game_group';
    var f1=document.createElement('input');f1.type='hidden';f1.name='num_groups';f1.value=g;
    var f2=document.createElement('input');f2.type='hidden';f2.name='advance_per_group';f2.value=a;
    var fp=document.createElement('input');fp.type='hidden';fp.name='pairing_mode';fp.value=pm;
    form.appendChild(f1);form.appendChild(f2);form.appendChild(fp);
    document.body.appendChild(form);form.submit();
  }}
}}
</script>"""

    if curr_user == 'admin':
        archive_rows = "".join([
            f"<div class='list-group-item bg-transparent text-white border-secondary d-flex justify-content-between align-items-center py-2'>"
            f"<a href='/view_history/{h.id}' class='text-white text-decoration-none flex-grow-1 me-2'>"
            f"{'<span class=\"badge bg-info me-1\">Active</span>' if h.is_active else '📁 '}{h.name} ({h.owner or 'admin'})</a>"
            f"<form action='/delete_tournament/{h.id}' method='post' class='flex-shrink-0' onsubmit=\"return confirm('确认删除赛事「{h.name}」？\\n此操作不可撤销，所有数据将永久删除！')\">"
            f"<button type='submit' class='btn btn-danger btn-sm py-0 px-2' style='font-size:11px;'>🗑 {T('删除','Delete')}</button></form></div>"
            for h in history
        ])
    else:
        archive_rows = "".join([
            f"<a href='/view_history/{h.id}' class='list-group-item list-group-item-action bg-transparent text-white border-secondary'>"
            f"{'<span class=\"badge bg-info\">Active</span>' if h.is_active else '📁'} {h.name} ({h.owner})</a>"
            for h in history
        ])

    return render_layout(
        f"""<div class="row">
          <div class="col-md-4">{excel_upload_html}{team_input_html}
            <div class="glass-card p-4 shadow">
              <h5>📅 {T('赛事存档','Tournament Archives')}</h5>
              <div class="list-group mt-3 small mb-4">{archive_rows}</div>
              <div class="border-top border-secondary pt-3">
                <h6 class="text-info small fw-bold mb-3">🆕 {T('启动新赛事','Create New Tournament')}</h6>
                <form action="/create_new_tournament" method="post">
                  <div class="input-group input-group-sm">
                    <input name="new_name" class="form-control bg-dark text-white border-secondary" placeholder="Name" required>
                    <button class="btn btn-outline-info" type="submit">{T('创建','Create')}</button>
                  </div>
                </form>
              </div>
            </div>
          </div>
          <div class="col-md-8 px-4">
            <div class="glass-card p-4 shadow">
              <div class="d-flex justify-content-between mb-4">
                <h5>{T('参赛名单','Participant List')} ({len(teams)})</h5>
                <a href="/export_excel" class="btn btn-outline-info btn-sm rounded-pill px-3 {'disabled' if not t else ''}">📥 {T('导出全记录 (Excel)','Export Full Record')}</a>
              </div>
              <div class="table-responsive" style="max-height: 500px;">
                <table class="table table-dark table-hover">
                  <thead><tr><th>{T('队伍名称','Team')}</th><th>{T('选手成员','Players')}</th><th>{T('管理','Manage')}</th></tr></thead>
                  <tbody>{rows}</tbody>
                </table>
              </div>
              <button type="button" class="btn btn-info w-100 py-3 fw-bold mt-4 fs-5 shadow-lg" {'disabled' if not t else ''} data-bs-toggle="modal" data-bs-target="#initModal">
                🎲 {T('锁定并生成首轮对阵','Lock & Generate Round 1')}
              </button>
            </div>
          </div>
        </div>
        {init_modal}""",
        "setup"
    )

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

@app.route('/init_game', methods=['GET', 'POST'])
def init_game():
    t = get_active_t()
    if not t: return redirect(url_for('setup'))
    conf = get_config(t.id)
    conf.current_round = 1; conf.mode = 0; conf.stage = None; conf.num_groups = 0; conf.advance_per_group = 0
    conf.pairing_mode = request.form.get('pairing_mode', request.args.get('pairing_mode', 'swiss'))
    Match.query.filter_by(tournament_id=t.id).delete()
    ts = Team.query.filter_by(tournament_id=t.id).all()
    if len(ts) < 2: return "Not enough teams"
    # 重置座位计数与拜轮记录（新赛事从零开始）
    for team in ts:
        team.seat_ns_count = 0; team.seat_ew_count = 0; team.had_bye = False
        team.group_id = 0; team.is_finalist = False
    random.shuffle(ts)
    # 拜轮处理（首轮奇数队）
    bye_team = None
    working = list(ts)
    if len(working) % 2 == 1:
        bye_team = working[-1]
        working = working[:-1]
        bye_team.had_bye = True
        bye_team.current_score += 3
        log_act("Bye Round", f"Round 1 bye: {bye_team.name} (+3 win pts)", t.id)
    for i in range(0, len(working)-1, 2):
        t1, t2 = working[i], working[i+1]
        p1 = [x.strip() for x in t1.players.replace('，',',').split(',')]
        p2 = [x.strip() for x in t2.players.replace('，',',').split(',')]
        is_6p = len(p1) >= 3 and len(p2) >= 3
        seats = assign_seats(t1, t2, p1, p2, is_6p)
        db.session.add(Match(tournament_id=t.id, round_no=1, table_no=(i//2+1),
                             team_a_id=t1.id, team_b_id=t2.id,
                             team_a_name=t1.name, team_b_name=t2.name, **seats))
    log_act("Init Game", "Generated Round 1 (Swiss V2)", t.id); db.session.commit()
    return redirect(url_for('matches'))

@app.route('/init_game_group', methods=['POST'])
def init_game_group():
    """小组赛初始化：随机分组 + 生成各组首轮对阵"""
    t = get_active_t()
    if not t: return redirect(url_for('setup'))
    num_groups = int(request.form.get('num_groups', 2))
    advance_per_group = int(request.form.get('advance_per_group', 1))
    ts = Team.query.filter_by(tournament_id=t.id).all()
    valid, msg = validate_group_config(len(ts), num_groups, advance_per_group)
    if not valid:
        return f"<script>alert('配置错误: {msg}');window.history.back();</script>"
    conf = get_config(t.id)
    conf.current_round = 1
    conf.mode = 1
    conf.num_groups = num_groups
    conf.advance_per_group = advance_per_group
    conf.stage = 'group'
    conf.pairing_mode = request.form.get('pairing_mode', 'swiss')
    Match.query.filter_by(tournament_id=t.id).delete()
    for team in ts:
        team.seat_ns_count = 0; team.seat_ew_count = 0; team.had_bye = False
        team.current_score = 0; team.round_score = 0; team.history_opponents = ""
        team.is_finalist = False; team.group_id = 0
    random.shuffle(ts)
    for i, team in enumerate(ts):
        team.group_id = (i % num_groups) + 1
    db.session.flush()
    table_counter = 1
    for g in range(1, num_groups + 1):
        group_teams = [tm for tm in ts if tm.group_id == g]
        bye_team = None
        working = list(group_teams)
        if len(working) % 2 == 1:
            bye_team = working[-1]; working = working[:-1]
            bye_team.had_bye = True; bye_team.current_score += 3
            log_act("Bye Round", f"Group {g} R1 bye: {bye_team.name} (+3)", t.id)
        for i in range(0, len(working)-1, 2):
            t1, t2 = working[i], working[i+1]
            p1 = [x.strip() for x in t1.players.replace('，',',').split(',')]
            p2 = [x.strip() for x in t2.players.replace('，',',').split(',')]
            is_6p = len(p1) >= 3 and len(p2) >= 3
            seats = assign_seats(t1, t2, p1, p2, is_6p)
            db.session.add(Match(tournament_id=t.id, round_no=1, table_no=table_counter,
                                 team_a_id=t1.id, team_b_id=t2.id,
                                 team_a_name=t1.name, team_b_name=t2.name,
                                 group_id=g, **seats))
            table_counter += 1
    log_act("Init Group Stage", f"{num_groups}组，每组出线{advance_per_group}名", t.id)
    db.session.commit()
    return redirect(url_for('matches'))

# --- 核心页面：共用生成比赛卡片代码 ---
def generate_matches_html(t, conf, is_panorama=False, finalist_ids=None):
    ms_raw = Match.query.filter_by(tournament_id=t.id, round_no=conf.current_round).all()
    if finalist_ids is not None:
        # 决赛对阵 group_id=0，小组赛对阵 group_id>0——必须同时过滤，防止两支决赛队的旧小组赛对阵混入
        ms = [m for m in ms_raw if (m.group_id or 0) == 0 and m.team_a_id in finalist_ids and m.team_b_id in finalist_ids]
    else:
        ms = ms_raw
    team_map = {tm.id: tm for tm in Team.query.filter_by(tournament_id=t.id).all()}
    cards = []
    for m in ms:
        is_6p = bool(m.pos_p5 and m.pos_p6)
        _ta = team_map.get(m.team_a_id)
        _ta_ps = set(p.strip() for p in (_ta.players or '').replace('，', ',').split(',') if p.strip()) if _ta else set()
        pc = lambda n, _s=_ta_ps: '#60A5FA' if (n and n.strip() in _s) else '#FB923C'
        if is_6p:
            seats_html = (f'<div class="seat-player pos-6-1" style="color:{pc(m.pos_north)}">① {m.pos_north}</div>'
                          f'<div class="seat-player pos-6-2" style="color:{pc(m.pos_p5)}">② {m.pos_p5}</div>'
                          f'<div class="seat-player pos-6-3" style="color:{pc(m.pos_east)}">③ {m.pos_east}</div>'
                          f'<div class="seat-player pos-6-4" style="color:{pc(m.pos_south)}">④ {m.pos_south}</div>'
                          f'<div class="seat-player pos-6-5" style="color:{pc(m.pos_p6)}">⑤ {m.pos_p6}</div>'
                          f'<div class="seat-player pos-6-6" style="color:{pc(m.pos_west)}">⑥ {m.pos_west}</div>')
        else:
            seats_html = (f'<div class="seat-player pos-4-n" style="color:{pc(m.pos_north)}">[N] {m.pos_north}</div>'
                          f'<div class="seat-player pos-4-e" style="color:{pc(m.pos_east)}">[E] {m.pos_east}</div>'
                          f'<div class="seat-player pos-4-s" style="color:{pc(m.pos_south)}">[S] {m.pos_south}</div>'
                          f'<div class="seat-player pos-4-w" style="color:{pc(m.pos_west)}">[W] {m.pos_west}</div>')

        click_attr = f'data-bs-toggle="modal" data-bs-target="#m{m.id}"' if not m.is_completed and not is_panorama else ''
        card = (f'<div class="col-md-4 mb-4"><div class="glass-card p-3 shadow-sm" style="background:rgba(45,55,72,0.4);">'
                f'<div class="seat-wrapper">{seats_html}'
                f'<div class="table-circle {"table-red" if m.is_completed else "table-blue"}" {click_attr}>T-{m.table_no}</div></div>'
                f'<div class="mt-4 text-center bg-black bg-opacity-25 py-2 rounded">'
                f'<span style="color:#60A5FA;font-weight:700;">{m.team_a_name}</span>'
                f' <span style="color:rgba(255,255,255,0.35);">VS</span> '
                f'<span style="color:#FB923C;font-weight:700;">{m.team_b_name}</span></div></div></div>')

        if not is_panorama:
            card += (f'<div class="modal fade" id="m{m.id}"><div class="modal-dialog modal-dialog-centered">'
                     f'<div class="modal-content bg-dark border-info text-white shadow-lg">'
                     f'<form action="/save/{m.id}" method="post"><div class="modal-body p-5 text-center">'
                     f'<h4 class="mb-4 text-info fw-bold">{T("第","Table")} {m.table_no} {T("桌成绩","Score")}</h4>'
                     f'<div class="row align-items-center mb-4">'
                     f'<div class="col-5"><label class="small mb-3 d-block" style="color:#60A5FA;">{m.team_a_name}</label>'
                     f'<input name="sa" type="number" class="form-control bg-secondary text-white text-center fs-2 fw-bold" required autofocus></div>'
                     f'<div class="col-2 fs-2 text-info">:</div>'
                     f'<div class="col-5"><label class="small mb-3 d-block" style="color:#FB923C;">{m.team_b_name}</label>'
                     f'<input name="sb" type="number" class="form-control bg-secondary text-white text-center fs-2 fw-bold" required></div></div></div>'
                     f'<div class="modal-footer border-0 p-4">'
                     f'<button class="btn btn-info w-100 py-3 fw-bold fs-5 shadow">{T("提交成绩","Submit")}</button>'
                     f'</div></form></div></div></div>')
        cards.append(card)
        
    all_done = ms and all(m.is_completed for m in ms)
    if all_done and not is_panorama:
        conf = get_config(t.id)
        if conf.mode == 1 and conf.stage == 'group':
            next_btn = (
                f'<div class="text-center mt-4 d-flex justify-content-center gap-3">'
                f'<a href="/next_r" class="btn btn-warning btn-lg px-5 py-3 fw-bold rounded-pill shadow-lg text-dark fs-4">🏁 {T("下一轮编排","Generate Next Round")}</a>'
                f'<button class="btn btn-danger btn-lg px-5 py-3 fw-bold rounded-pill shadow-lg fs-4" data-bs-toggle="modal" data-bs-target="#endGroupModal">🏆 {T("小组赛结束","End Group Stage")}</button>'
                f'</div>'
            )
        else:
            next_btn = f'<div class="text-center mt-4"><a href="/next_r" class="btn btn-warning btn-lg px-5 py-3 fw-bold rounded-pill shadow-lg text-dark fs-4">🏁 {T("下一轮编排","Generate Next Round")}</a></div>'
    else:
        next_btn = ""
    return "".join(cards), next_btn

@app.route('/matches')
def matches():
    if not session.get('logged_in'): return redirect(url_for('login'))
    t = get_active_t()
    if not t: return redirect(url_for('setup'))
    conf = get_config(t.id)

    timer_html = (f'<div id="timer-box"><div class="small text-secondary text-center">{T("计时","Timer")}<br>'
                  f'<input type="number" id="duration" class="bg-transparent text-info border-0 text-center fw-bold" style="width:55px; outline:none;" value="50"></div>'
                  f'<div id="time-display">00:00</div>'
                  f'<button onclick="startTimer()" class="btn btn-info px-4 fw-bold rounded-pill">{T("开始","Start")}</button>'
                  f'<button id="pause-btn" onclick="togglePause()" class="btn btn-outline-warning px-4 fw-bold rounded-pill">{T("暂停","Pause")}</button>'
                  f'<button id="zoom-btn" onclick="toggleClock()" class="btn btn-outline-light px-3 fw-bold rounded-pill" style="font-size:0.85rem;">⤢ 放大时钟</button></div>')

    if conf.mode == 1 and conf.stage == 'group':
        # ===== 小组赛：按组分块显示（同积分榜模式）=====
        ms = Match.query.filter_by(tournament_id=t.id, round_no=conf.current_round).order_by(Match.table_no).all()
        team_map = {tm.id: tm for tm in Team.query.filter_by(tournament_id=t.id).all()}
        GROUP_COLORS = ['#60A5FA','#34D399','#FBBF24','#F87171','#A78BFA','#FB923C','#38BDF8','#4ADE80']
        all_done = bool(ms) and all(m.is_completed for m in ms)
        groups_html = ""
        modals_html = ""
        for g in range(1, conf.num_groups + 1):
            color = GROUP_COLORS[(g - 1) % len(GROUP_COLORS)]
            g_team_ids = {tid for tid, tm in team_map.items() if (tm.group_id or 0) == g}
            g_matches = [m for m in ms if m.team_a_id in g_team_ids or m.team_b_id in g_team_ids]
            g_cards = []
            for m in g_matches:
                is_6p = bool(m.pos_p5 and m.pos_p6)
                _ta = team_map.get(m.team_a_id)
                _ta_ps = set(p.strip() for p in (_ta.players or '').replace('，', ',').split(',') if p.strip()) if _ta else set()
                pc = lambda n, _s=_ta_ps: '#60A5FA' if (n and n.strip() in _s) else '#FB923C'
                if is_6p:
                    sh = (f'<div class="seat-player pos-6-1" style="color:{pc(m.pos_north)}">① {m.pos_north}</div>'
                          f'<div class="seat-player pos-6-2" style="color:{pc(m.pos_p5)}">② {m.pos_p5}</div>'
                          f'<div class="seat-player pos-6-3" style="color:{pc(m.pos_east)}">③ {m.pos_east}</div>'
                          f'<div class="seat-player pos-6-4" style="color:{pc(m.pos_south)}">④ {m.pos_south}</div>'
                          f'<div class="seat-player pos-6-5" style="color:{pc(m.pos_p6)}">⑤ {m.pos_p6}</div>'
                          f'<div class="seat-player pos-6-6" style="color:{pc(m.pos_west)}">⑥ {m.pos_west}</div>')
                else:
                    sh = (f'<div class="seat-player pos-4-n" style="color:{pc(m.pos_north)}">[N] {m.pos_north}</div>'
                          f'<div class="seat-player pos-4-e" style="color:{pc(m.pos_east)}">[E] {m.pos_east}</div>'
                          f'<div class="seat-player pos-4-s" style="color:{pc(m.pos_south)}">[S] {m.pos_south}</div>'
                          f'<div class="seat-player pos-4-w" style="color:{pc(m.pos_west)}">[W] {m.pos_west}</div>')
                click = f'data-bs-toggle="modal" data-bs-target="#m{m.id}"' if not m.is_completed else ''
                g_cards.append(
                    f'<div class="col-md-4 mb-3">'
                    f'<div class="glass-card p-3 shadow-sm" style="background:rgba(45,55,72,0.4);border-top:3px solid {color};">'
                    f'<div class="seat-wrapper">{sh}'
                    f'<div class="table-circle {"table-red" if m.is_completed else "table-blue"}" {click}>T-{m.table_no}</div>'
                    f'</div><div class="mt-3 text-center bg-black bg-opacity-25 py-2 rounded">'
                    f'<span style="color:#60A5FA;font-weight:700;">{m.team_a_name}</span>'
                    f' <span style="color:rgba(255,255,255,0.35);">VS</span> '
                    f'<span style="color:#FB923C;font-weight:700;">{m.team_b_name}</span></div></div></div>'
                )
                # 录分弹窗
                modals_html += (
                    f'<div class="modal fade" id="m{m.id}"><div class="modal-dialog modal-dialog-centered">'
                    f'<div class="modal-content bg-dark border-info text-white shadow-lg">'
                    f'<form action="/save/{m.id}" method="post"><div class="modal-body p-5 text-center">'
                    f'<h4 class="mb-4 text-info fw-bold">{T("第","Table")} {m.table_no} {T("桌成绩","Score")}</h4>'
                    f'<div class="row align-items-center mb-4">'
                    f'<div class="col-5"><label class="small mb-3 d-block" style="color:#60A5FA;">{m.team_a_name}</label>'
                    f'<input name="sa" type="number" class="form-control bg-secondary text-white text-center fs-2 fw-bold" required autofocus></div>'
                    f'<div class="col-2 fs-2 text-info">:</div>'
                    f'<div class="col-5"><label class="small mb-3 d-block" style="color:#FB923C;">{m.team_b_name}</label>'
                    f'<input name="sb" type="number" class="form-control bg-secondary text-white text-center fs-2 fw-bold" required></div></div></div>'
                    f'<div class="modal-footer border-0 p-4">'
                    f'<button class="btn btn-info w-100 py-3 fw-bold fs-5 shadow">{T("提交成绩","Submit")}</button>'
                    f'</div></form></div></div></div>'
                )
            # 本组对阵信息（含座位）
            g_rows = ""
            for m in g_matches:
                is_6p = bool(m.pos_p5 and m.pos_p6)
                _ta = team_map.get(m.team_a_id)
                _ta_ps = set(p.strip() for p in (_ta.players or '').replace('，', ',').split(',') if p.strip()) if _ta else set()
                pc = lambda n, _s=_ta_ps: '#60A5FA' if (n and n.strip() in _s) else '#FB923C'
                if is_6p:
                    seats_str = (f'① <span style="color:{pc(m.pos_north)}">{m.pos_north or "-"}</span> &nbsp; '
                                 f'② <span style="color:{pc(m.pos_p5)}">{m.pos_p5 or "-"}</span> &nbsp; '
                                 f'③ <span style="color:{pc(m.pos_east)}">{m.pos_east or "-"}</span> &nbsp; '
                                 f'④ <span style="color:{pc(m.pos_south)}">{m.pos_south or "-"}</span> &nbsp; '
                                 f'⑤ <span style="color:{pc(m.pos_p6)}">{m.pos_p6 or "-"}</span> &nbsp; '
                                 f'⑥ <span style="color:{pc(m.pos_west)}">{m.pos_west or "-"}</span>')
                else:
                    seats_str = (f'北: <span style="color:{pc(m.pos_north)}">{m.pos_north or "-"}</span> &nbsp;&nbsp; '
                                 f'南: <span style="color:{pc(m.pos_south)}">{m.pos_south or "-"}</span> &nbsp;&nbsp; '
                                 f'东: <span style="color:{pc(m.pos_east)}">{m.pos_east or "-"}</span> &nbsp;&nbsp; '
                                 f'西: <span style="color:{pc(m.pos_west)}">{m.pos_west or "-"}</span>')
                g_rows += (
                    f'<div style="display:grid;grid-template-columns:75px 1fr;gap:10px;padding:10px 0;'
                    f'border-bottom:1px solid rgba(255,255,255,0.1);align-items:center;">'
                    f'<div style="color:{color};font-weight:900;font-size:1.15rem;text-align:center;line-height:1.3;">'
                    f'（{m.table_no}）<br><span style="font-size:0.78rem;">号桌</span></div>'
                    f'<div><div style="font-size:0.98rem;margin-bottom:3px;">'
                    f'<span style="color:#60A5FA;font-weight:700;">{m.team_a_name}</span>'
                    f'<span style="color:rgba(255,255,255,0.35);margin:0 6px;">vs</span>'
                    f'<span style="color:#FB923C;font-weight:700;">{m.team_b_name}</span></div>'
                    f'<div style="font-size:0.85rem;">{seats_str}</div>'
                    f'</div></div>'
                )
            groups_html += (
                f'<div class="mb-4" style="border:2px solid {color};border-radius:12px;overflow:hidden;">'
                f'<div style="background:{color}22;padding:12px 20px;color:{color};font-weight:900;font-size:1.2rem;">'
                f'第{g}组 · Group {g}</div>'
                f'<div class="p-3"><div class="row">{"".join(g_cards)}</div>'
                f'<div style="padding:0 8px;margin-top:8px;">{g_rows}</div></div></div>'
            )
        if all_done:
            next_btn = (
                f'<div class="text-center mt-4 d-flex justify-content-center gap-3">'
                f'<a href="/next_r" class="btn btn-warning btn-lg px-5 py-3 fw-bold rounded-pill shadow-lg text-dark fs-4">🏁 {T("下一轮编排","Generate Next Round")}</a>'
                f'<button class="btn btn-danger btn-lg px-5 py-3 fw-bold rounded-pill shadow-lg fs-4" data-bs-toggle="modal" data-bs-target="#endGroupModal">🏆 {T("小组赛结束","End Group Stage")}</button>'
                f'</div>'
            )
        else:
            next_btn = ""
        stage_label = (f'<div style="text-align:center;color:#FBBF24;font-size:1rem;font-weight:700;'
                       f'margin:6px 0 16px;letter-spacing:2px;">🏟 第{conf.current_round}轮 小组赛 | '
                       f'{conf.num_groups}组赛制 · 每组出线{conf.advance_per_group}名</div>')
        # 小组赛结束弹出框
        qualifiers = get_group_qualifiers(t.id)
        q_rows = "".join([
            f'<tr><td class="text-warning fw-bold">第{q.group_id}组</td>'
            f'<td class="text-info fw-bold">{q.name}</td>'
            f'<td class="text-white-50">{q.players}</td>'
            f'<td class="text-warning">{q.current_score}胜分</td></tr>'
            for q in qualifiers
        ])
        end_group_modal = f"""
        <div class="modal fade" id="endGroupModal"><div class="modal-dialog modal-dialog-centered modal-lg">
        <div class="modal-content bg-dark text-white border-warning shadow-lg">
          <div class="modal-header border-warning"><h5 class="modal-title text-warning fw-bold">🏆 {T('小组赛出线名单','Group Stage Qualifiers')}</h5><button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button></div>
          <div class="modal-body p-4">
            <p class="text-white-50 small mb-3">{T('根据当前积分，以下队伍将晋级决赛循环赛','Based on current scores, the following teams advance to the finals')}：</p>
            <table class="table table-dark table-hover text-center"><thead><tr><th>{T('小组','Group')}</th><th>{T('队伍','Team')}</th><th>{T('选手','Players')}</th><th>{T('积分','Score')}</th></tr></thead>
            <tbody>{q_rows}</tbody></table>
            <p class="text-white-50 small mt-3">共 <strong class="text-warning">{len(qualifiers)}</strong> 支队伍晋级决赛</p>
          </div>
          <div class="modal-footer border-0 p-4">
            <form action="/confirm_finals" method="post" class="w-100">
              <button type="submit" class="btn btn-warning w-100 py-3 fw-bold fs-5 rounded-pill shadow">✅ {T('确认出线赛队，并且开始决赛循环赛编排','Confirm Finalists & Start Finals Draw')}</button>
            </form>
          </div>
        </div></div></div>"""
        html = f'{timer_html}{stage_label}{groups_html}{modals_html}{next_btn}{end_group_modal}'

    elif conf.mode == 1 and conf.stage == 'finals':
        # ===== 决赛：仅决赛队对阵框在上，小组赛完整存档在下 =====
        team_map = {tm.id: tm for tm in Team.query.filter_by(tournament_id=t.id).all()}
        finalist_ids = {tid for tid, tm in team_map.items() if tm.is_finalist}
        GROUP_COLORS = ['#60A5FA','#34D399','#FBBF24','#F87171','#A78BFA','#FB923C','#38BDF8','#4ADE80']
        FC = '#FFD700'

        # 只取当前轮次里两队均为决赛队的对阵（避免 round_no 与小组赛冲突）
        ms_all_cur = Match.query.filter_by(tournament_id=t.id, round_no=conf.current_round).order_by(Match.table_no).all()
        ms = [m for m in ms_all_cur if (m.group_id or 0) == 0 and m.team_a_id in finalist_ids and m.team_b_id in finalist_ids]

        all_done = bool(ms) and all(m.is_completed for m in ms)
        finals_cards = []
        finals_modals = ""
        finals_rows = ""
        for m in ms:
            is_6p = bool(m.pos_p5 and m.pos_p6)
            _ta = team_map.get(m.team_a_id)
            _ta_ps = set(p.strip() for p in (_ta.players or '').replace('，', ',').split(',') if p.strip()) if _ta else set()
            pc = lambda n, _s=_ta_ps: '#60A5FA' if (n and n.strip() in _s) else '#FB923C'
            if is_6p:
                sh = (f'<div class="seat-player pos-6-1" style="color:{pc(m.pos_north)}">① {m.pos_north}</div>'
                      f'<div class="seat-player pos-6-2" style="color:{pc(m.pos_p5)}">② {m.pos_p5}</div>'
                      f'<div class="seat-player pos-6-3" style="color:{pc(m.pos_east)}">③ {m.pos_east}</div>'
                      f'<div class="seat-player pos-6-4" style="color:{pc(m.pos_south)}">④ {m.pos_south}</div>'
                      f'<div class="seat-player pos-6-5" style="color:{pc(m.pos_p6)}">⑤ {m.pos_p6}</div>'
                      f'<div class="seat-player pos-6-6" style="color:{pc(m.pos_west)}">⑥ {m.pos_west}</div>')
                seats_str = (f'① <span style="color:{pc(m.pos_north)}">{m.pos_north or "-"}</span> &nbsp; '
                             f'② <span style="color:{pc(m.pos_p5)}">{m.pos_p5 or "-"}</span> &nbsp; '
                             f'③ <span style="color:{pc(m.pos_east)}">{m.pos_east or "-"}</span> &nbsp; '
                             f'④ <span style="color:{pc(m.pos_south)}">{m.pos_south or "-"}</span> &nbsp; '
                             f'⑤ <span style="color:{pc(m.pos_p6)}">{m.pos_p6 or "-"}</span> &nbsp; '
                             f'⑥ <span style="color:{pc(m.pos_west)}">{m.pos_west or "-"}</span>')
            else:
                sh = (f'<div class="seat-player pos-4-n" style="color:{pc(m.pos_north)}">[N] {m.pos_north}</div>'
                      f'<div class="seat-player pos-4-e" style="color:{pc(m.pos_east)}">[E] {m.pos_east}</div>'
                      f'<div class="seat-player pos-4-s" style="color:{pc(m.pos_south)}">[S] {m.pos_south}</div>'
                      f'<div class="seat-player pos-4-w" style="color:{pc(m.pos_west)}">[W] {m.pos_west}</div>')
                seats_str = (f'北: <span style="color:{pc(m.pos_north)}">{m.pos_north or "-"}</span> &nbsp;&nbsp; '
                             f'南: <span style="color:{pc(m.pos_south)}">{m.pos_south or "-"}</span> &nbsp;&nbsp; '
                             f'东: <span style="color:{pc(m.pos_east)}">{m.pos_east or "-"}</span> &nbsp;&nbsp; '
                             f'西: <span style="color:{pc(m.pos_west)}">{m.pos_west or "-"}</span>')
            click = f'data-bs-toggle="modal" data-bs-target="#m{m.id}"' if not m.is_completed else ''
            finals_cards.append(
                f'<div class="col-md-4 mb-3">'
                f'<div class="glass-card p-3 shadow-sm" style="background:rgba(45,55,72,0.4);border-top:3px solid {FC};">'
                f'<div class="seat-wrapper">{sh}'
                f'<div class="table-circle {"table-red" if m.is_completed else "table-blue"}" {click}>T-{m.table_no}</div>'
                f'</div><div class="mt-3 text-center bg-black bg-opacity-25 py-2 rounded">'
                f'<span style="color:#60A5FA;font-weight:700;">{m.team_a_name}</span>'
                f' <span style="color:rgba(255,255,255,0.35);">VS</span> '
                f'<span style="color:#FB923C;font-weight:700;">{m.team_b_name}</span></div></div></div>'
            )
            finals_modals += (
                f'<div class="modal fade" id="m{m.id}"><div class="modal-dialog modal-dialog-centered">'
                f'<div class="modal-content bg-dark border-info text-white shadow-lg">'
                f'<form action="/save/{m.id}" method="post"><div class="modal-body p-5 text-center">'
                f'<h4 class="mb-4 text-info fw-bold">{T("第","Table")} {m.table_no} {T("桌成绩","Score")}</h4>'
                f'<div class="row align-items-center mb-4">'
                f'<div class="col-5"><label class="small mb-3 d-block" style="color:#60A5FA;">{m.team_a_name}</label>'
                f'<input name="sa" type="number" class="form-control bg-secondary text-white text-center fs-2 fw-bold" required autofocus></div>'
                f'<div class="col-2 fs-2 text-info">:</div>'
                f'<div class="col-5"><label class="small mb-3 d-block" style="color:#FB923C;">{m.team_b_name}</label>'
                f'<input name="sb" type="number" class="form-control bg-secondary text-white text-center fs-2 fw-bold" required></div></div></div>'
                f'<div class="modal-footer border-0 p-4">'
                f'<button class="btn btn-info w-100 py-3 fw-bold fs-5 shadow">{T("提交成绩","Submit")}</button>'
                f'</div></form></div></div></div>'
            )
            finals_rows += (
                f'<div style="display:grid;grid-template-columns:75px 1fr;gap:10px;padding:10px 0;'
                f'border-bottom:1px solid rgba(255,215,0,0.15);align-items:center;">'
                f'<div style="color:{FC};font-weight:900;font-size:1.15rem;text-align:center;line-height:1.3;">'
                f'（{m.table_no}）<br><span style="font-size:0.78rem;">号桌</span></div>'
                f'<div><div style="font-size:0.98rem;margin-bottom:3px;">'
                f'<span style="color:#60A5FA;font-weight:700;">{m.team_a_name}</span>'
                f'<span style="color:rgba(255,255,255,0.35);margin:0 6px;">vs</span>'
                f'<span style="color:#FB923C;font-weight:700;">{m.team_b_name}</span></div>'
                f'<div style="font-size:0.85rem;">{seats_str}</div>'
                f'</div></div>'
            )
        finals_box = (
            f'<div class="mb-4" style="border:2px solid {FC};border-radius:12px;overflow:hidden;">'
            f'<div style="background:rgba(255,215,0,0.15);padding:12px 20px;color:{FC};font-weight:900;font-size:1.2rem;">'
            f'🏆 决赛小组 · 第{conf.current_round}轮</div>'
            f'<div class="p-3"><div class="row">{"".join(finals_cards)}</div>'
            f'<div style="padding:0 8px;margin-top:8px;">{finals_rows}</div></div></div>'
        )

        # 小组赛完整存档：每组一个彩色框，框内按轮次列出对阵（赛桌、队伍、座位、比分）
        all_hist = Match.query.filter_by(tournament_id=t.id).order_by(Match.round_no, Match.table_no).all()
        # 小组赛对阵 = match.group_id>0 OR 至少一方不是决赛队
        gs_by_group = {}  # group_id -> round_no -> [matches]
        for m in all_hist:
            is_gs = (m.group_id and m.group_id > 0) or not (m.team_a_id in finalist_ids and m.team_b_id in finalist_ids)
            if not is_gs:
                continue
            ta = team_map.get(m.team_a_id)
            grp = (ta.group_id or 0) if ta else 0
            if grp > 0:
                gs_by_group.setdefault(grp, {}).setdefault(m.round_no, []).append(m)

        gs_archive_html = '<div class="mt-3"><small class="text-white-50 d-block mb-2">📋 小组赛存档</small>'
        for g in range(1, conf.num_groups + 1):
            color = GROUP_COLORS[(g - 1) % len(GROUP_COLORS)]
            rounds_data = gs_by_group.get(g, {})
            rounds_html = ""
            for rnd in sorted(rounds_data.keys()):
                rnd_rows = ""
                for m in rounds_data[rnd]:
                    is_6p = bool(m.pos_p5 and m.pos_p6)
                    _rta = team_map.get(m.team_a_id)
                    _rta_ps = set(p.strip() for p in (_rta.players or '').replace('，', ',').split(',') if p.strip()) if _rta else set()
                    rpc = lambda n, _s=_rta_ps: '#60A5FA' if (n and n.strip() in _s) else '#FB923C'
                    if is_6p:
                        s_str = (f'① <span style="color:{rpc(m.pos_north)}">{m.pos_north or "-"}</span> '
                                 f'② <span style="color:{rpc(m.pos_p5)}">{m.pos_p5 or "-"}</span> '
                                 f'③ <span style="color:{rpc(m.pos_east)}">{m.pos_east or "-"}</span> '
                                 f'④ <span style="color:{rpc(m.pos_south)}">{m.pos_south or "-"}</span> '
                                 f'⑤ <span style="color:{rpc(m.pos_p6)}">{m.pos_p6 or "-"}</span> '
                                 f'⑥ <span style="color:{rpc(m.pos_west)}">{m.pos_west or "-"}</span>')
                    else:
                        s_str = (f'北:<span style="color:{rpc(m.pos_north)}">{m.pos_north or "-"}</span> '
                                 f'南:<span style="color:{rpc(m.pos_south)}">{m.pos_south or "-"}</span> '
                                 f'东:<span style="color:{rpc(m.pos_east)}">{m.pos_east or "-"}</span> '
                                 f'西:<span style="color:{rpc(m.pos_west)}">{m.pos_west or "-"}</span>')
                    score_str = f'{m.score_a} : {m.score_b}' if m.is_completed and m.score_a >= 0 else '-'
                    rnd_rows += (
                        f'<div style="display:grid;grid-template-columns:70px 1fr 60px;gap:8px;padding:8px 0;'
                        f'border-bottom:1px solid rgba(255,255,255,0.07);font-size:0.87rem;align-items:center;">'
                        f'<div style="color:{color};font-weight:800;text-align:center;">（{m.table_no}）号桌</div>'
                        f'<div><span style="color:#60A5FA;font-weight:600;">{m.team_a_name}</span>'
                        f' <span style="color:rgba(255,255,255,0.3);">vs</span>'
                        f' <span style="color:#FB923C;font-weight:600;">{m.team_b_name}</span>'
                        f'<br><span style="font-size:0.82rem;">{s_str}</span></div>'
                        f'<div style="color:#FFD700;font-weight:700;text-align:center;">{score_str}</div>'
                        f'</div>'
                    )
                rounds_html += (
                    f'<div style="margin-bottom:10px;">'
                    f'<div style="color:rgba(255,255,255,0.45);font-size:0.8rem;font-weight:700;'
                    f'margin-bottom:4px;letter-spacing:1px;">第{rnd}轮</div>'
                    f'{rnd_rows}</div>'
                )
            gs_archive_html += (
                f'<div class="mb-3" style="border:2px solid {color};border-radius:12px;overflow:hidden;">'
                f'<div style="background:{color}22;padding:10px 18px;color:{color};font-weight:900;font-size:1.1rem;">'
                f'第{g}组 · Group {g}</div>'
                f'<div class="p-3">{rounds_html or "<div style=\'color:rgba(255,255,255,0.35);font-size:0.88rem;\'>暂无数据</div>"}</div></div>'
            )
        gs_archive_html += '</div>'

        if all_done:
            next_btn = f'<div class="text-center mt-4"><a href="/next_r" class="btn btn-warning btn-lg px-5 py-3 fw-bold rounded-pill shadow-lg text-dark fs-4">🏁 {T("下一轮编排","Generate Next Round")}</a></div>'
        else:
            next_btn = ""
        finals_label = (f'<div style="text-align:center;color:#FFD700;font-size:1rem;font-weight:700;'
                        f'margin:6px 0 16px;letter-spacing:2px;">🏆 决赛循环赛 · 第{conf.current_round}轮</div>')
        html = f'{timer_html}{finals_label}{finals_box}{gs_archive_html}{finals_modals}{next_btn}'

    else:
        # ===== 普通循环赛 =====
        ms = Match.query.filter_by(tournament_id=t.id, round_no=conf.current_round).order_by(Match.table_no).all()
        team_map = {tm.id: tm for tm in Team.query.filter_by(tournament_id=t.id).all()}
        all_done = bool(ms) and all(m.is_completed for m in ms)
        BOX_COLOR = '#60A5FA'
        cards = []
        modals = ""
        info_rows = ""
        for m in ms:
            is_6p = bool(m.pos_p5 and m.pos_p6)
            _ta = team_map.get(m.team_a_id)
            _ta_ps = set(p.strip() for p in (_ta.players or '').replace('，', ',').split(',') if p.strip()) if _ta else set()
            pc = lambda n, _s=_ta_ps: '#60A5FA' if (n and n.strip() in _s) else '#FB923C'
            if is_6p:
                sh = (f'<div class="seat-player pos-6-1" style="color:{pc(m.pos_north)}">① {m.pos_north}</div>'
                      f'<div class="seat-player pos-6-2" style="color:{pc(m.pos_p5)}">② {m.pos_p5}</div>'
                      f'<div class="seat-player pos-6-3" style="color:{pc(m.pos_east)}">③ {m.pos_east}</div>'
                      f'<div class="seat-player pos-6-4" style="color:{pc(m.pos_south)}">④ {m.pos_south}</div>'
                      f'<div class="seat-player pos-6-5" style="color:{pc(m.pos_p6)}">⑤ {m.pos_p6}</div>'
                      f'<div class="seat-player pos-6-6" style="color:{pc(m.pos_west)}">⑥ {m.pos_west}</div>')
                seats_str = (f'① <span style="color:{pc(m.pos_north)}">{m.pos_north or "-"}</span> &nbsp; '
                             f'② <span style="color:{pc(m.pos_p5)}">{m.pos_p5 or "-"}</span> &nbsp; '
                             f'③ <span style="color:{pc(m.pos_east)}">{m.pos_east or "-"}</span> &nbsp; '
                             f'④ <span style="color:{pc(m.pos_south)}">{m.pos_south or "-"}</span> &nbsp; '
                             f'⑤ <span style="color:{pc(m.pos_p6)}">{m.pos_p6 or "-"}</span> &nbsp; '
                             f'⑥ <span style="color:{pc(m.pos_west)}">{m.pos_west or "-"}</span>')
            else:
                sh = (f'<div class="seat-player pos-4-n" style="color:{pc(m.pos_north)}">[N] {m.pos_north}</div>'
                      f'<div class="seat-player pos-4-e" style="color:{pc(m.pos_east)}">[E] {m.pos_east}</div>'
                      f'<div class="seat-player pos-4-s" style="color:{pc(m.pos_south)}">[S] {m.pos_south}</div>'
                      f'<div class="seat-player pos-4-w" style="color:{pc(m.pos_west)}">[W] {m.pos_west}</div>')
                seats_str = (f'北: <span style="color:{pc(m.pos_north)}">{m.pos_north or "-"}</span> &nbsp;&nbsp; '
                             f'南: <span style="color:{pc(m.pos_south)}">{m.pos_south or "-"}</span> &nbsp;&nbsp; '
                             f'东: <span style="color:{pc(m.pos_east)}">{m.pos_east or "-"}</span> &nbsp;&nbsp; '
                             f'西: <span style="color:{pc(m.pos_west)}">{m.pos_west or "-"}</span>')
            click = f'data-bs-toggle="modal" data-bs-target="#m{m.id}"' if not m.is_completed else ''
            cards.append(
                f'<div class="col-md-4 mb-3">'
                f'<div class="glass-card p-3 shadow-sm" style="background:rgba(45,55,72,0.4);border-top:3px solid {BOX_COLOR};">'
                f'<div class="seat-wrapper">{sh}'
                f'<div class="table-circle {"table-red" if m.is_completed else "table-blue"}" {click}>T-{m.table_no}</div>'
                f'</div><div class="mt-3 text-center bg-black bg-opacity-25 py-2 rounded">'
                f'<span style="color:#60A5FA;font-weight:700;">{m.team_a_name}</span>'
                f' <span style="color:rgba(255,255,255,0.35);">VS</span> '
                f'<span style="color:#FB923C;font-weight:700;">{m.team_b_name}</span></div></div></div>'
            )
            modals += (
                f'<div class="modal fade" id="m{m.id}"><div class="modal-dialog modal-dialog-centered">'
                f'<div class="modal-content bg-dark border-info text-white shadow-lg">'
                f'<form action="/save/{m.id}" method="post"><div class="modal-body p-5 text-center">'
                f'<h4 class="mb-4 text-info fw-bold">{T("第","Table")} {m.table_no} {T("桌成绩","Score")}</h4>'
                f'<div class="row align-items-center mb-4">'
                f'<div class="col-5"><label class="small mb-3 d-block" style="color:#60A5FA;">{m.team_a_name}</label>'
                f'<input name="sa" type="number" class="form-control bg-secondary text-white text-center fs-2 fw-bold" required autofocus></div>'
                f'<div class="col-2 fs-2 text-info">:</div>'
                f'<div class="col-5"><label class="small mb-3 d-block" style="color:#FB923C;">{m.team_b_name}</label>'
                f'<input name="sb" type="number" class="form-control bg-secondary text-white text-center fs-2 fw-bold" required></div></div></div>'
                f'<div class="modal-footer border-0 p-4">'
                f'<button class="btn btn-info w-100 py-3 fw-bold fs-5 shadow">{T("提交成绩","Submit")}</button>'
                f'</div></form></div></div></div>'
            )
            info_rows += (
                f'<div style="display:grid;grid-template-columns:75px 1fr;gap:10px;padding:10px 0;'
                f'border-bottom:1px solid rgba(96,165,250,0.2);align-items:center;">'
                f'<div style="color:{BOX_COLOR};font-weight:900;font-size:1.15rem;text-align:center;line-height:1.3;">'
                f'（{m.table_no}）<br><span style="font-size:0.78rem;">号桌</span></div>'
                f'<div><div style="font-size:0.98rem;margin-bottom:3px;">'
                f'<span style="color:#60A5FA;font-weight:700;">{m.team_a_name}</span>'
                f'<span style="color:rgba(255,255,255,0.35);margin:0 6px;">vs</span>'
                f'<span style="color:#FB923C;font-weight:700;">{m.team_b_name}</span></div>'
                f'<div style="font-size:0.85rem;">{seats_str}</div>'
                f'</div></div>'
            )
        round_box = (
            f'<div class="mb-4" style="border:2px solid {BOX_COLOR};border-radius:12px;overflow:hidden;">'
            f'<div style="background:{BOX_COLOR}22;padding:12px 20px;color:{BOX_COLOR};font-weight:900;font-size:1.2rem;">'
            f'🏟 第{conf.current_round}轮</div>'
            f'<div class="p-3"><div class="row">{"".join(cards)}</div>'
            f'<div style="padding:0 8px;margin-top:8px;">{info_rows}</div></div></div>'
        )
        if all_done:
            next_btn = f'<div class="text-center mt-4"><a href="/next_r" class="btn btn-warning btn-lg px-5 py-3 fw-bold rounded-pill shadow-lg text-dark fs-4">🏁 {T("下一轮编排","Generate Next Round")}</a></div>'
        else:
            next_btn = ""
        html = f'{timer_html}{round_box}{modals}{next_btn}'

    return render_layout(html, "matches")

@app.route('/panorama')
def panorama():
    t = get_active_t()
    if not t: return "No active tournament"
    conf = get_config(t.id)
    marquee = f'<div class="ad-ticker-pro w-100"><div class="ad-content">📢 {conf.scroll_ad} 📢</div></div>'
    ms_all = Match.query.filter_by(tournament_id=t.id, round_no=conf.current_round).order_by(Match.table_no).all()
    team_map = {team.id: team for team in Team.query.filter_by(tournament_id=t.id).all()}

    if conf.mode == 1 and conf.stage == 'group':
        # ===== 小组赛全景：按组分块显示（以 team.group_id 为准，与积分榜一致）=====
        GROUP_COLORS = ['#60A5FA','#34D399','#FBBF24','#F87171','#A78BFA','#FB923C','#38BDF8','#4ADE80']
        groups_html = ""
        all_detail_rows = ""
        for g in range(1, conf.num_groups + 1):
            color = GROUP_COLORS[(g-1) % len(GROUP_COLORS)]
            g_team_ids = {tid for tid, tm in team_map.items() if (tm.group_id or 0) == g}
            g_matches = [m for m in ms_all if m.team_a_id in g_team_ids or m.team_b_id in g_team_ids]
            g_cards = []
            for m in g_matches:
                is_6p = bool(m.pos_p5 and m.pos_p6)
                _ta = team_map.get(m.team_a_id)
                _ta_ps = set(p.strip() for p in (_ta.players or '').replace('，', ',').split(',') if p.strip()) if _ta else set()
                pc = lambda n, _s=_ta_ps: '#60A5FA' if (n and n.strip() in _s) else '#FB923C'
                if is_6p:
                    seats_html = (f'<div class="seat-player pos-6-1" style="color:{pc(m.pos_north)}">① {m.pos_north}</div>'
                                  f'<div class="seat-player pos-6-2" style="color:{pc(m.pos_p5)}">② {m.pos_p5}</div>'
                                  f'<div class="seat-player pos-6-3" style="color:{pc(m.pos_east)}">③ {m.pos_east}</div>'
                                  f'<div class="seat-player pos-6-4" style="color:{pc(m.pos_south)}">④ {m.pos_south}</div>'
                                  f'<div class="seat-player pos-6-5" style="color:{pc(m.pos_p6)}">⑤ {m.pos_p6}</div>'
                                  f'<div class="seat-player pos-6-6" style="color:{pc(m.pos_west)}">⑥ {m.pos_west}</div>')
                else:
                    seats_html = (f'<div class="seat-player pos-4-n" style="color:{pc(m.pos_north)}">[N] {m.pos_north}</div>'
                                  f'<div class="seat-player pos-4-e" style="color:{pc(m.pos_east)}">[E] {m.pos_east}</div>'
                                  f'<div class="seat-player pos-4-s" style="color:{pc(m.pos_south)}">[S] {m.pos_south}</div>'
                                  f'<div class="seat-player pos-4-w" style="color:{pc(m.pos_west)}">[W] {m.pos_west}</div>')
                g_cards.append(
                    f'<div class="col-md-4 mb-4"><div class="glass-card p-3 shadow-sm" style="background:rgba(45,55,72,0.4);border-top:3px solid {color};">'
                    f'<div class="seat-wrapper">{seats_html}<div class="table-circle table-{"red" if m.is_completed else "blue"}">T-{m.table_no}</div></div>'
                    f'<div class="mt-4 text-center bg-black bg-opacity-25 py-2 rounded">'
                    f'<span style="color:#60A5FA;font-weight:700;">{m.team_a_name}</span>'
                    f' <span style="color:rgba(255,255,255,0.35);">VS</span> '
                    f'<span style="color:#FB923C;font-weight:700;">{m.team_b_name}</span></div></div></div>'
                )
            # 本组对阵信息（含座位）—— 收集至底部汇总框
            g_rows = ""
            for m in g_matches:
                is_6p = bool(m.pos_p5 and m.pos_p6)
                _ta = team_map.get(m.team_a_id)
                _ta_ps = set(p.strip() for p in (_ta.players or '').replace('，', ',').split(',') if p.strip()) if _ta else set()
                pc = lambda n, _s=_ta_ps: '#60A5FA' if (n and n.strip() in _s) else '#FB923C'
                if is_6p:
                    seats_str = (f'① <span style="color:{pc(m.pos_north)}">{m.pos_north or "-"}</span> &nbsp;&nbsp; '
                                 f'② <span style="color:{pc(m.pos_p5)}">{m.pos_p5 or "-"}</span> &nbsp;&nbsp; '
                                 f'③ <span style="color:{pc(m.pos_east)}">{m.pos_east or "-"}</span> &nbsp;&nbsp; '
                                 f'④ <span style="color:{pc(m.pos_south)}">{m.pos_south or "-"}</span> &nbsp;&nbsp; '
                                 f'⑤ <span style="color:{pc(m.pos_p6)}">{m.pos_p6 or "-"}</span> &nbsp;&nbsp; '
                                 f'⑥ <span style="color:{pc(m.pos_west)}">{m.pos_west or "-"}</span>')
                else:
                    seats_str = (f'北: <span style="color:{pc(m.pos_north)}">{m.pos_north or "-"}</span> &nbsp;&nbsp; '
                                 f'南: <span style="color:{pc(m.pos_south)}">{m.pos_south or "-"}</span> &nbsp;&nbsp; '
                                 f'东: <span style="color:{pc(m.pos_east)}">{m.pos_east or "-"}</span> &nbsp;&nbsp; '
                                 f'西: <span style="color:{pc(m.pos_west)}">{m.pos_west or "-"}</span>')
                g_rows += (
                    f'<div style="display:grid;grid-template-columns:80px 1fr;gap:10px;padding:12px 0;'
                    f'border-bottom:1px solid rgba(255,255,255,0.12);align-items:center;">'
                    f'<div style="color:{color};font-weight:900;font-size:1.2rem;text-align:center;line-height:1.3;">'
                    f'（{m.table_no}）<br><span style="font-size:0.8rem;font-weight:600;">号桌</span></div>'
                    f'<div>'
                    f'<div style="margin-bottom:5px;font-size:1rem;">'
                    f'<span style="color:#60A5FA;font-weight:700;">{m.team_a_name}</span>'
                    f'<span style="color:rgba(255,255,255,0.35);margin:0 8px;">vs</span>'
                    f'<span style="color:#FB923C;font-weight:700;">{m.team_b_name}</span></div>'
                    f'<div style="font-size:0.88rem;">{seats_str}</div>'
                    f'</div></div>'
                )
            # 小组方框：只显示座位图，不含对阵详情
            groups_html += (
                f'<div class="mb-5" style="border:2px solid {color};border-radius:16px;padding:24px 32px;background:rgba(0,0,0,0.2);">'
                f'<div style="color:{color};font-size:1.6rem;font-weight:900;letter-spacing:3px;margin-bottom:18px;">第{g}组 · Group {g}</div>'
                f'<div class="row">{"".join(g_cards)}</div>'
                f'</div>'
            )
            # 汇总至底部框，附组别标题
            all_detail_rows += (
                f'<div style="color:{color};font-size:1.15rem;font-weight:900;letter-spacing:2px;'
                f'margin:{"0" if g==1 else "24px"} 0 8px;padding-bottom:6px;'
                f'border-bottom:2px solid {color};">第{g}组 · Group {g}</div>'
                f'{g_rows}'
            )
        cards_section = f'<div class="container-fluid px-5 mt-4">{groups_html}</div>'
        stage_label = f'<div style="text-align:center;color:#FBBF24;font-size:1.1rem;font-weight:700;margin:10px 0 20px;letter-spacing:2px;">🏟 第{conf.current_round}轮 小组赛 | {conf.num_groups}组赛制 · 每组出线{conf.advance_per_group}名</div>'
        detail_box = (
            f'<div class="container-fluid px-5 mb-5">'
            f'<div style="border:2px solid #FBBF24;border-radius:16px;padding:28px 36px;background:rgba(0,0,0,0.25);">'
            f'<div style="color:#FBBF24;font-size:1.5rem;font-weight:900;letter-spacing:3px;margin-bottom:20px;">'
            f'📋 第{conf.current_round}轮小组赛 — 桌号 · 队名 · 座位安排</div>'
            f'<div style="font-size:1.05rem;line-height:1.9;">{all_detail_rows}</div>'
            f'<div style="margin-top:28px;text-align:center;">'
            f'<a href="/export_group_matches" style="display:inline-block;background:linear-gradient(135deg,#F59E0B,#D97706);'
            f'color:#fff;font-size:1.1rem;font-weight:800;padding:14px 48px;border-radius:50px;text-decoration:none;'
            f'letter-spacing:1px;box-shadow:0 4px 20px rgba(245,158,11,0.4);">📥 导出小组赛对阵信息 (Excel)</a>'
            f'</div>'
            f'</div></div>'
        )
        grouping_box = stage_label + detail_box
    elif conf.mode == 1 and conf.stage == 'finals':
        # ===== 决赛全景：决赛循环赛置顶 + 历史小组赛数据在下 =====
        finalist_ids = {tid for tid, tm in team_map.items() if tm.is_finalist}
        cards_html, _ = generate_matches_html(t, conf, is_panorama=True, finalist_ids=finalist_ids)
        # 决赛对阵信息（含座位）—— 只取双方均为决赛队的对阵
        ms_finals = [m for m in ms_all if (m.group_id or 0) == 0]
        finals_rows = ""
        for m in ms_finals:
            is_6p = bool(m.pos_p5 and m.pos_p6)
            _ta = team_map.get(m.team_a_id)
            _ta_ps = set(p.strip() for p in (_ta.players or '').replace('，', ',').split(',') if p.strip()) if _ta else set()
            pc = lambda n, _s=_ta_ps: '#60A5FA' if (n and n.strip() in _s) else '#FB923C'
            if is_6p:
                seats_str = (f'① <span style="color:{pc(m.pos_north)}">{m.pos_north or "-"}</span> &nbsp;&nbsp; '
                             f'② <span style="color:{pc(m.pos_p5)}">{m.pos_p5 or "-"}</span> &nbsp;&nbsp; '
                             f'③ <span style="color:{pc(m.pos_east)}">{m.pos_east or "-"}</span> &nbsp;&nbsp; '
                             f'④ <span style="color:{pc(m.pos_south)}">{m.pos_south or "-"}</span> &nbsp;&nbsp; '
                             f'⑤ <span style="color:{pc(m.pos_p6)}">{m.pos_p6 or "-"}</span> &nbsp;&nbsp; '
                             f'⑥ <span style="color:{pc(m.pos_west)}">{m.pos_west or "-"}</span>')
            else:
                seats_str = (f'北: <span style="color:{pc(m.pos_north)}">{m.pos_north or "-"}</span> &nbsp;&nbsp; '
                             f'南: <span style="color:{pc(m.pos_south)}">{m.pos_south or "-"}</span> &nbsp;&nbsp; '
                             f'东: <span style="color:{pc(m.pos_east)}">{m.pos_east or "-"}</span> &nbsp;&nbsp; '
                             f'西: <span style="color:{pc(m.pos_west)}">{m.pos_west or "-"}</span>')
            finals_rows += (
                f'<div style="display:grid;grid-template-columns:80px 1fr;gap:10px;padding:14px 0;'
                f'border-bottom:1px solid rgba(255,215,0,0.2);align-items:center;">'
                f'<div style="color:#FFD700;font-weight:900;font-size:1.3rem;text-align:center;line-height:1.3;">'
                f'（{m.table_no}）<br><span style="font-size:0.82rem;font-weight:600;">号桌</span></div>'
                f'<div>'
                f'<div style="margin-bottom:5px;font-size:1.05rem;">'
                f'<span style="color:#60A5FA;font-weight:700;">{m.team_a_name}</span>'
                f'<span style="color:rgba(255,255,255,0.35);margin:0 8px;">vs</span>'
                f'<span style="color:#FB923C;font-weight:700;">{m.team_b_name}</span></div>'
                f'<div style="font-size:0.9rem;">{seats_str}</div>'
                f'</div></div>'
            )
        # 历史小组赛数据（按轮次→分组，以 match.group_id 为准——小组赛对阵 group_id>0，决赛对阵 group_id=0）
        hist_matches = Match.query.filter_by(tournament_id=t.id).order_by(Match.round_no, Match.table_no).all()
        hist_rounds = {}
        for m in hist_matches:
            if (m.group_id or 0) > 0:  # 只保留小组赛对阵（决赛对阵 group_id=0，已排除）
                ta = team_map.get(m.team_a_id)
                grp = (ta.group_id if ta else 0) or m.group_id
                hist_rounds.setdefault(m.round_no, {}).setdefault(grp, []).append(m)
        GROUP_COLORS_H = ['#60A5FA','#34D399','#FBBF24','#F87171','#A78BFA','#FB923C','#38BDF8','#4ADE80']
        hist_html = ""
        for rnd in sorted(hist_rounds.keys()):
            round_groups_html = ""
            for grp in sorted(hist_rounds[rnd].keys()):
                hcolor = GROUP_COLORS_H[(grp - 1) % len(GROUP_COLORS_H)]
                grp_rows = ""
                for m in hist_rounds[rnd][grp]:
                    is_6p = bool(m.pos_p5 and m.pos_p6)
                    score_str = f'{m.score_a} : {m.score_b}' if m.is_completed else '-'
                    _hta = team_map.get(m.team_a_id)
                    _hta_ps = set(p.strip() for p in (_hta.players or '').replace('，', ',').split(',') if p.strip()) if _hta else set()
                    hpc = lambda n, _s=_hta_ps: '#60A5FA' if (n and n.strip() in _s) else '#FB923C'
                    if is_6p:
                        s_str = (f'① <span style="color:{hpc(m.pos_north)}">{m.pos_north or "-"}</span> '
                                 f'② <span style="color:{hpc(m.pos_p5)}">{m.pos_p5 or "-"}</span> '
                                 f'③ <span style="color:{hpc(m.pos_east)}">{m.pos_east or "-"}</span> '
                                 f'④ <span style="color:{hpc(m.pos_south)}">{m.pos_south or "-"}</span> '
                                 f'⑤ <span style="color:{hpc(m.pos_p6)}">{m.pos_p6 or "-"}</span> '
                                 f'⑥ <span style="color:{hpc(m.pos_west)}">{m.pos_west or "-"}</span>')
                    else:
                        s_str = (f'北:<span style="color:{hpc(m.pos_north)}">{m.pos_north or "-"}</span> '
                                 f'南:<span style="color:{hpc(m.pos_south)}">{m.pos_south or "-"}</span> '
                                 f'东:<span style="color:{hpc(m.pos_east)}">{m.pos_east or "-"}</span> '
                                 f'西:<span style="color:{hpc(m.pos_west)}">{m.pos_west or "-"}</span>')
                    grp_rows += (
                        f'<div style="display:grid;grid-template-columns:70px 1fr 55px;gap:8px;padding:8px 0;'
                        f'border-bottom:1px solid rgba(255,255,255,0.07);align-items:center;font-size:0.87rem;">'
                        f'<div style="color:{hcolor};font-weight:800;text-align:center;">（{m.table_no}）号桌</div>'
                        f'<div><span style="color:#60A5FA;font-weight:600;">{m.team_a_name}</span>'
                        f' <span style="color:rgba(255,255,255,0.3);">vs</span>'
                        f' <span style="color:#FB923C;font-weight:600;">{m.team_b_name}</span>'
                        f'<br><span style="font-size:0.82rem;">{s_str}</span></div>'
                        f'<div style="color:#FFD700;font-weight:700;text-align:center;">{score_str}</div>'
                        f'</div>'
                    )
                round_groups_html += (
                    f'<div style="margin-bottom:14px;">'
                    f'<div style="color:{hcolor};font-weight:800;font-size:0.93rem;margin-bottom:6px;">第{grp}组</div>'
                    f'{grp_rows}</div>'
                )
            hist_html += (
                f'<div style="margin-bottom:18px;padding:16px 20px;border:1px solid rgba(255,255,255,0.1);'
                f'border-radius:12px;background:rgba(0,0,0,0.15);">'
                f'<div style="color:rgba(255,255,255,0.5);font-size:0.93rem;font-weight:700;'
                f'margin-bottom:12px;letter-spacing:1px;">小组赛 第{rnd}轮</div>'
                f'{round_groups_html}</div>'
            )
        cards_section = (
            f'<div class="container-fluid px-5 mt-2">'
            f'<div style="background:rgba(255,215,0,0.06);border:2px solid #FFD700;'
            f'border-radius:16px;padding:20px 32px 10px;margin-bottom:20px;">'
            f'<div style="color:#FFD700;font-size:1.6rem;font-weight:900;letter-spacing:3px;margin-bottom:16px;">'
            f'🏆 决赛循环赛 · 第{conf.current_round}轮</div>'
            f'<div class="row">{cards_html}</div>'
            f'</div></div>'
        )
        grouping_box = (
            f'<div class="container-fluid px-5 mb-4">'
            f'<div style="background:rgba(255,215,0,0.08);border:2px solid #FFD700;border-radius:16px;padding:24px 36px;">'
            f'<div style="color:#FFD700;font-size:1.3rem;font-weight:900;letter-spacing:2px;margin-bottom:16px;">'
            f'🏆 决赛循环赛 — 第{conf.current_round}轮 对阵详情</div>'
            f'<div>{finals_rows}</div>'
            f'<div style="margin-top:20px;text-align:center;">'
            f'<a href="/export_grouping" style="display:inline-block;background:linear-gradient(135deg,#F59E0B,#D97706);'
            f'color:#fff;font-size:1rem;font-weight:800;padding:12px 40px;border-radius:50px;text-decoration:none;">📥 导出对阵信息</a>'
            f'</div></div></div>'
            + (f'<div class="container-fluid px-5 mb-5">'
               f'<div style="border:1px solid rgba(255,255,255,0.15);border-radius:16px;'
               f'padding:24px 36px;background:rgba(0,0,0,0.2);">'
               f'<div style="color:rgba(255,255,255,0.45);font-size:1.1rem;font-weight:700;'
               f'letter-spacing:2px;margin-bottom:18px;">📁 小组赛历史数据</div>'
               f'{hist_html}</div></div>' if hist_html else '')
        )
    else:
        # ===== 普通循环赛全景：原有逻辑 =====
        cards_html, _ = generate_matches_html(t, conf, is_panorama=True)
        grouping_rows = ""
        for m in ms_all:
            is_6p = bool(m.pos_p5 and m.pos_p6)
            _ta = team_map.get(m.team_a_id)
            _ta_ps = set(p.strip() for p in (_ta.players or '').replace('，', ',').split(',') if p.strip()) if _ta else set()
            pc = lambda n, _s=_ta_ps: '#60A5FA' if (n and n.strip() in _s) else '#FB923C'
            if is_6p:
                seats_str = (f'① <span style="color:{pc(m.pos_north)}">{m.pos_north or "-"}</span> &nbsp;&nbsp; '
                             f'② <span style="color:{pc(m.pos_p5)}">{m.pos_p5 or "-"}</span> &nbsp;&nbsp; '
                             f'③ <span style="color:{pc(m.pos_east)}">{m.pos_east or "-"}</span> &nbsp;&nbsp; '
                             f'④ <span style="color:{pc(m.pos_south)}">{m.pos_south or "-"}</span> &nbsp;&nbsp; '
                             f'⑤ <span style="color:{pc(m.pos_p6)}">{m.pos_p6 or "-"}</span> &nbsp;&nbsp; '
                             f'⑥ <span style="color:{pc(m.pos_west)}">{m.pos_west or "-"}</span>')
            else:
                seats_str = (f'北: <span style="color:{pc(m.pos_north)}">{m.pos_north or "-"}</span> &nbsp;&nbsp; '
                             f'南: <span style="color:{pc(m.pos_south)}">{m.pos_south or "-"}</span> &nbsp;&nbsp; '
                             f'东: <span style="color:{pc(m.pos_east)}">{m.pos_east or "-"}</span> &nbsp;&nbsp; '
                             f'西: <span style="color:{pc(m.pos_west)}">{m.pos_west or "-"}</span>')
            grouping_rows += (
                f'<div style="display:grid;grid-template-columns:80px 1fr;gap:10px;padding:14px 0;'
                f'border-bottom:1px solid rgba(255,215,0,0.2);align-items:center;">'
                f'<div style="color:#FFD700;font-weight:900;font-size:1.3rem;text-align:center;line-height:1.3;">'
                f'（{m.table_no}）<br><span style="font-size:0.82rem;font-weight:600;">号桌</span></div>'
                f'<div>'
                f'<div style="margin-bottom:5px;font-size:1.05rem;">'
                f'<span style="color:#60A5FA;font-weight:700;">{m.team_a_name}</span>'
                f'<span style="color:rgba(255,255,255,0.35);margin:0 8px;">vs</span>'
                f'<span style="color:#FB923C;font-weight:700;">{m.team_b_name}</span></div>'
                f'<div style="font-size:0.9rem;">{seats_str}</div>'
                f'</div></div>'
            )
        cards_section = f'<div class="container-fluid px-5 mt-4"><div class="row">{cards_html}</div></div>'
        grouping_box = (f'<div class="container-fluid px-5 mt-5 mb-5">'
                        f'<div style="background:rgba(255,215,0,0.08);border:2px solid #FFD700;border-radius:16px;padding:30px 40px;box-shadow:0 0 30px rgba(255,215,0,0.15);">'
                        f'<div style="color:#FFD700;font-size:1.5rem;font-weight:900;letter-spacing:2px;margin-bottom:18px;">📋 第{conf.current_round}轮 参赛分组</div>'
                        f'<div style="font-size:1.15rem;line-height:1.8;">{grouping_rows}</div>'
                        f'<div style="margin-top:28px;text-align:center;"><a href="/export_grouping" style="display:inline-block;background:linear-gradient(135deg,#F59E0B,#D97706);color:#fff;font-size:1.1rem;font-weight:800;padding:14px 48px;border-radius:50px;text-decoration:none;letter-spacing:1px;box-shadow:0 4px 20px rgba(245,158,11,0.4);">📥 导出参赛分组信息</a></div>'
                        f'</div></div>')

    panorama_timer = (f'<div id="timer-box"><div class="small text-secondary text-center">计时 Timer<br>'
                      f'<input type="number" id="duration" class="bg-transparent text-info border-0 text-center fw-bold" style="width:55px; outline:none;" value="50"></div>'
                      f'<div id="time-display">00:00</div>'
                      f'<button onclick="startTimer()" class="btn btn-info px-4 fw-bold rounded-pill">开始 Start</button>'
                      f'<button id="pause-btn" onclick="togglePause()" class="btn btn-outline-warning px-4 fw-bold rounded-pill">暂停 Pause</button>'
                      f'<button id="zoom-btn" onclick="toggleClock()" class="btn btn-outline-light px-3 fw-bold rounded-pill" style="font-size:0.85rem;">⤢ 放大时钟</button></div>')
    html = f'{marquee}<div class="container-fluid px-5 mt-2">{panorama_timer}</div>{cards_section}{grouping_box}<script>window.onload=function(){{initPanoramaDisplay();}};</script>'
    return render_layout(html, active="panorama", hide_nav=True)

@app.route('/export_grouping')
def export_grouping():
    t = get_active_t()
    if not t: return "No active tournament"
    conf = get_config(t.id)
    ms_sorted = Match.query.filter_by(tournament_id=t.id, round_no=conf.current_round).order_by(Match.table_no).all()
    team_map = {team.id: team for team in Team.query.filter_by(tournament_id=t.id).all()}
    export_data = []
    for m in ms_sorted:
        ta = team_map.get(m.team_a_id)
        tb = team_map.get(m.team_b_id)
        export_data.append({
            "桌号": m.table_no,
            "队伍A": m.team_a_name,
            "队伍A选手": ta.players if ta else "",
            "队伍B": m.team_b_name,
            "队伍B选手": tb.players if tb else "",
        })
    df = pd.DataFrame(export_data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name=f"第{conf.current_round}轮分组")
    output.seek(0)
    log_act("Export Grouping", f"Round {conf.current_round} grouping exported.", t.id)
    return send_file(output, as_attachment=True, download_name=f"分组信息_第{conf.current_round}轮_{t.name}.xlsx")

@app.route('/export_group_matches')
def export_group_matches():
    t = get_active_t()
    if not t: return "No active tournament"
    team_map = {team.id: team for team in Team.query.filter_by(tournament_id=t.id).all()}
    all_matches = Match.query.filter_by(tournament_id=t.id).order_by(Match.round_no, Match.table_no).all()
    export_data = []
    for m in all_matches:
        ta = team_map.get(m.team_a_id)
        if not ta or not (ta.group_id or 0) > 0:
            continue
        is_6p = bool(m.pos_p5 and m.pos_p6)
        if is_6p:
            row = {
                "轮次": f"第{m.round_no}轮",
                "组别": f"第{ta.group_id}组",
                "桌号": m.table_no,
                "队伍A": m.team_a_name,
                "队伍B": m.team_b_name,
                "① 北": m.pos_north or "",
                "②": m.pos_p5 or "",
                "③ 东": m.pos_east or "",
                "④ 南": m.pos_south or "",
                "⑤": m.pos_p6 or "",
                "⑥ 西": m.pos_west or "",
            }
        else:
            row = {
                "轮次": f"第{m.round_no}轮",
                "组别": f"第{ta.group_id}组",
                "桌号": m.table_no,
                "队伍A": m.team_a_name,
                "队伍B": m.team_b_name,
                "北(N)": m.pos_north or "",
                "东(E)": m.pos_east or "",
                "南(S)": m.pos_south or "",
                "西(W)": m.pos_west or "",
            }
        export_data.append(row)
    df = pd.DataFrame(export_data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name="小组赛对阵")
    output.seek(0)
    log_act("Export Group Matches", f"Group stage matches exported for {t.name}.", t.id)
    return send_file(output, as_attachment=True, download_name=f"小组赛对阵_{t.name}.xlsx")

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

def _team_score_row(i, team, show_group=False):
    group_badge = f'<span class="badge bg-secondary me-1">第{team.group_id}组</span>' if show_group and team.group_id else ''
    row = (f"<tr><td>{i}</td><td class='text-info fw-bold'>{group_badge}{team.name}</td>"
           f"<td class='text-warning'>{team.current_score}</td><td class='text-success'>{team.round_score}</td>"
           f"<td>{team.players} <a href='#' data-bs-toggle='modal' data-bs-target='#editScore{team.id}' class='text-secondary ms-2' style='text-decoration:none;'>✎</a></td></tr>")
    modal = (f"<div class='modal fade' id='editScore{team.id}'><div class='modal-dialog modal-dialog-centered'><div class='modal-content bg-dark text-white border-warning shadow-lg'>"
             f"<form action='/adjust_score/{team.id}' method='post'><div class='modal-body p-4 text-start'>"
             f"<h5 class='text-warning fw-bold mb-4'>修正成绩: {team.name}</h5>"
             f"<div class='mb-3'><label class='small opacity-75'>累计胜分</label><input name='c_score' type='number' class='form-control bg-secondary text-white border-0 py-2' value='{team.current_score}'></div>"
             f"<div class='mb-4'><label class='small opacity-75'>累计级分</label><input name='r_score' type='number' class='form-control bg-secondary text-white border-0 py-2' value='{team.round_score}'></div>"
             f"<button type='submit' class='btn btn-warning w-100 fw-bold py-2'>确认修正</button>"
             f"</div></form></div></div></div>")
    return row + modal

@app.route('/leaderboard')
def leaderboard():
    t = get_active_t()
    if not t: return redirect(url_for('setup'))
    conf = get_config(t.id)
    all_teams = Team.query.filter_by(tournament_id=t.id).all()

    GROUP_COLORS = ['#60A5FA','#34D399','#FBBF24','#F87171','#A78BFA','#FB923C','#38BDF8','#4ADE80']

    if conf.mode == 1 and conf.stage == 'group':
        # ===== 小组赛积分榜：按组分块 =====
        table_html = ""
        for g in range(1, conf.num_groups + 1):
            color = GROUP_COLORS[(g-1) % len(GROUP_COLORS)]
            g_teams = [tm for tm in all_teams if (tm.group_id or 0) == g]
            g_teams.sort(key=lambda x: (x.current_score, x.round_score), reverse=True)
            g_rows = "".join([_team_score_row(i+1, tm) for i, tm in enumerate(g_teams)])
            table_html += (
                f'<div class="mb-4" style="border:2px solid {color};border-radius:12px;overflow:hidden;">'
                f'<div style="background:{color}22;padding:12px 20px;color:{color};font-weight:900;font-size:1.1rem;">第{g}组 · Group {g}</div>'
                f'<div class="table-responsive"><table class="table table-dark table-hover text-center align-middle mb-0">'
                f'<thead><tr><th>名次</th><th>队伍</th><th>总胜分</th><th>总级分</th><th>选手</th></tr></thead>'
                f'<tbody>{g_rows}</tbody></table></div></div>'
            )
        # 颁奖屏用全部队伍最高分
        all_teams.sort(key=lambda x: (x.current_score, x.round_score), reverse=True)
        aw, as2, ab = (all_teams[0] if len(all_teams)>0 else None), (all_teams[1] if len(all_teams)>1 else None), (all_teams[2] if len(all_teams)>2 else None)
        stage_info = f'<div class="badge bg-warning text-dark fs-6 mb-3">🏟 小组循环赛进行中 · {conf.num_groups}组 · 每组出线{conf.advance_per_group}名</div>'
        content = f'<h3 class="text-info fw-bold mb-2">📊 实时积分排行榜</h3>{stage_info}{table_html}'

    elif conf.mode == 1 and conf.stage == 'finals':
        # ===== 决赛积分榜：决赛队在上，各小组成绩在下 =====
        finalists = [tm for tm in all_teams if tm.is_finalist]
        finalists.sort(key=lambda x: (x.current_score, x.round_score), reverse=True)
        f_rows = "".join([_team_score_row(i+1, tm, show_group=True) for i, tm in enumerate(finalists)])
        finals_table = (
            f'<div class="mb-4" style="border:2px solid #FFD700;border-radius:12px;overflow:hidden;">'
            f'<div style="background:rgba(255,215,0,0.12);padding:12px 20px;color:#FFD700;font-weight:900;font-size:1.1rem;">🏆 决赛排行榜 · Finals Leaderboard</div>'
            f'<div class="table-responsive"><table class="table table-dark table-hover text-center align-middle mb-0">'
            f'<thead><tr><th>名次</th><th>队伍</th><th>决赛胜分</th><th>决赛级分</th><th>选手</th></tr></thead>'
            f'<tbody>{f_rows}</tbody></table></div></div>'
        )
        # 各组成绩存档：从历史对阵记录中还原小组赛真实成绩
        finalist_ids = {tm.id for tm in all_teams if tm.is_finalist}
        all_completed = Match.query.filter_by(tournament_id=t.id, is_completed=True).all()
        gs_win = {}; gs_round = {}
        for m in all_completed:
            # 小组赛对阵判断：match.group_id>0 或至少一方非决赛队
            is_gs = (m.group_id and m.group_id > 0) or not (m.team_a_id in finalist_ids and m.team_b_id in finalist_ids)
            if not is_gs: continue
            for (my_id, my_s, op_s) in [(m.team_a_id, m.score_a, m.score_b), (m.team_b_id, m.score_b, m.score_a)]:
                if my_s < 0: continue
                gs_win.setdefault(my_id, 0); gs_round.setdefault(my_id, 0)
                if my_s > op_s: gs_win[my_id] += 3
                elif my_s == op_s: gs_win[my_id] += 1
                gs_round[my_id] += my_s
        group_tables = '<div class="mt-4"><small class="text-white-50 d-block mb-2">📋 小组赛成绩存档：</small>'
        for g in range(1, conf.num_groups + 1):
            color = GROUP_COLORS[(g-1) % len(GROUP_COLORS)]
            g_teams = sorted([tm for tm in all_teams if (tm.group_id or 0) == g],
                             key=lambda x: (gs_win.get(x.id, 0), gs_round.get(x.id, 0)), reverse=True)
            g_rows_lb = "".join([
                f'<tr><td class="text-info fw-bold">{"🏆 " if tm.is_finalist else ""}{tm.name}</td>'
                f'<td class="text-warning">{gs_win.get(tm.id, 0)}</td>'
                f'<td class="text-success">{gs_round.get(tm.id, 0)}</td>'
                f'<td class="text-white-50 small">{tm.players}</td></tr>'
                for tm in g_teams
            ])
            group_tables += (
                f'<div class="mb-3" style="border:1px solid {color};border-radius:10px;overflow:hidden;">'
                f'<div style="background:{color}22;padding:8px 16px;color:{color};font-weight:800;font-size:0.9rem;">第{g}组 · Group {g}</div>'
                f'<div class="table-responsive"><table class="table table-dark table-sm text-center align-middle mb-0">'
                f'<thead><tr><th>队伍</th><th>胜分</th><th>级分</th><th>选手</th></tr></thead>'
                f'<tbody>{g_rows_lb}</tbody></table></div></div>'
            )
        group_tables += '</div>'
        aw = finalists[0] if len(finalists)>0 else None
        as2 = finalists[1] if len(finalists)>1 else None
        ab = finalists[2] if len(finalists)>2 else None
        stage_info = f'<div class="badge bg-danger fs-6 mb-3">🏆 决赛循环赛进行中 · {len(finalists)}支队伍</div>'
        content = f'<h3 class="text-info fw-bold mb-2">📊 实时积分排行榜</h3>{stage_info}{finals_table}{group_tables}'

    else:
        # ===== 普通循环赛：原有逻辑 =====
        all_teams.sort(key=lambda x: (x.current_score, x.round_score), reverse=True)
        rows = "".join([_team_score_row(i+1, tm) for i, tm in enumerate(all_teams)])
        aw = all_teams[0] if len(all_teams)>0 else None
        as2 = all_teams[1] if len(all_teams)>1 else None
        ab = all_teams[2] if len(all_teams)>2 else None
        content = (f'<div class="d-flex justify-content-between align-items-center mb-4"><div>'
                   f'<h3 class="text-info fw-bold m-0">📊 {T("实时积分排行榜","Live Leaderboard")}</h3>'
                   f'<small class="text-white-50">{T("点击队员名旁的 ✎ 可手动微调成绩","Click ✎ to adjust score manually")}</small>'
                   f'</div></div>'
                   f'<div class="glass-card p-5 shadow-lg"><div class="table-responsive">'
                   f'<table class="table table-dark table-hover text-center align-middle">'
                   f'<thead><tr><th>{T("名次","Rank")}</th><th>{T("队伍","Team")}</th><th>{T("总胜分","Win Pts")}</th><th>{T("总级分","Round Pts")}</th><th>{T("选手 (编辑)","Players (Edit)")}</th></tr></thead>'
                   f'<tbody>{rows}</tbody></table></div>')

    award_modal = (f'<div class="modal fade" id="awardModal"><div class="modal-dialog modal-fullscreen">'
                   f'<div class="modal-content text-center text-white" style="background:radial-gradient(circle,#1e293b 0%,#0f172a 100%);">'
                   f'<audio id="victoryMusic" loop><source src="{conf.bg_music_url}" type="audio/mpeg"></audio>'
                   f'<div class="container py-5">'
                   f'<h1 class="display-1 text-warning fw-bold mb-5">🏆 {t.name} {T("荣耀颁奖","Awards")}</h1>'
                   f'<div class="glass-card p-5 border-warning w-75 mx-auto mb-5 shadow-lg"><p class="display-4 text-warning mb-2">🥇 {T("冠 军","Champion")}</p><h1 class="display-2 fw-bold">{aw.name if aw else "-"}</h1><p class="fs-2 text-info mt-3">{aw.players if aw else ""}</p></div>'
                   f'<div class="row w-75 mx-auto gap-4"><div class="col glass-card p-4 border-info"><p class="h2 text-info mb-2">🥈 {T("亚 军","Runner-up")}</p><h2>{as2.name if as2 else "-"}</h2><p class="text-info opacity-75 fs-4 mt-2">{as2.players if as2 else ""}</p></div>'
                   f'<div class="col glass-card p-4 border-light"><p class="h2 text-light mb-2">🥉 {T("季 军","Third Place")}</p><h2>{ab.name if ab else "-"}</h2><p class="text-light opacity-75 fs-4 mt-2">{ab.players if ab else ""}</p></div></div>'
                   f'<div class="mt-5"><button class="btn btn-outline-danger btn-lg rounded-pill" onclick="document.getElementById(\'victoryMusic\').pause();" data-bs-dismiss="modal">{T("返回后台","Return")}</button></div>'
                   f'</div><script>document.getElementById("awardModal").addEventListener("shown.bs.modal",function(){{document.getElementById("victoryMusic").play().catch(e=>console.log("Block"));}});</script>'
                   f'</div></div></div>')

    award_btn = f'<button class="btn btn-warning w-100 py-4 mt-4 fw-bold rounded-pill shadow-lg fs-4" data-bs-toggle="modal" data-bs-target="#awardModal">🎊 {T("开启颁奖大屏","Open Awards Screen")}</button>'

    if conf.mode == 1 and conf.stage in ('group', 'finals'):
        return render_layout(f'<div class="glass-card p-4 shadow-lg">{content}{award_btn}</div>{award_modal}', "leaderboard")
    else:
        return render_layout(f'{content}{award_btn}</div>{award_modal}', "leaderboard")

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
    conf = get_config(t.id)
    all_teams = Team.query.filter_by(tournament_id=t.id).all()
    all_matches = Match.query.filter_by(tournament_id=t.id).order_by(Match.round_no).all()
    output = io.BytesIO()

    if conf.mode == 1 and conf.stage == 'finals':
        # ===== 决赛模式：小组赛成绩 + 决赛成绩 分两个 Sheet =====
        finalist_ids = {tm.id for tm in all_teams if tm.is_finalist}
        # 判断每场对阵归属
        gs_matches = [m for m in all_matches if
                      (m.group_id and m.group_id > 0) or
                      not (m.team_a_id in finalist_ids and m.team_b_id in finalist_ids)]
        finals_matches = [m for m in all_matches if m not in gs_matches]

        # Sheet 1：小组赛成绩
        gs_data = []
        for g in range(1, conf.num_groups + 1):
            g_teams = [tm for tm in all_teams if (tm.group_id or 0) == g]
            for team in g_teams:
                t_matches = [m for m in gs_matches if m.team_a_id == team.id or m.team_b_id == team.id]
                win_s = rnd_s = 0
                row = {"小组": f"第{g}组", "队名": team.name, "选手": team.players, "晋级决赛": "✅" if team.is_finalist else ""}
                for m in t_matches:
                    if m.score_a < 0: continue
                    is_a = m.team_a_id == team.id
                    my_s = m.score_a if is_a else m.score_b
                    op_s = m.score_b if is_a else m.score_a
                    wp = 3 if my_s > op_s else (1 if my_s == op_s else 0)
                    win_s += wp; rnd_s += my_s
                    row[f"第{m.round_no}轮 胜分"] = wp
                    row[f"第{m.round_no}轮 级分"] = my_s
                row["小组赛总胜分"] = win_s
                row["小组赛总级分"] = rnd_s
                gs_data.append(row)

        # Sheet 2：决赛成绩
        finalists = sorted([tm for tm in all_teams if tm.is_finalist],
                           key=lambda x: (x.current_score, x.round_score), reverse=True)
        finals_data = []
        for i, team in enumerate(finalists):
            row = {"名次": i+1, "队名": team.name, "选手": team.players,
                   "决赛总胜分": team.current_score, "决赛总级分": team.round_score}
            for m in finals_matches:
                if m.team_a_id != team.id and m.team_b_id != team.id: continue
                if m.score_a < 0: continue
                is_a = m.team_a_id == team.id
                my_s = m.score_a if is_a else m.score_b
                op_s = m.score_b if is_a else m.score_a
                wp = 3 if my_s > op_s else (1 if my_s == op_s else 0)
                row[f"决赛第{m.round_no}轮 胜分"] = wp
                row[f"决赛第{m.round_no}轮 级分"] = my_s
            finals_data.append(row)

        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            pd.DataFrame(gs_data).to_excel(writer, index=False, sheet_name="小组赛成绩")
            pd.DataFrame(finals_data).to_excel(writer, index=False, sheet_name="决赛成绩")

    else:
        # ===== 普通/小组赛模式：单 Sheet =====
        all_teams.sort(key=lambda x: (x.current_score, x.round_score), reverse=True)
        export_data = []
        for i, team in enumerate(all_teams):
            row = {"排名": i+1, "队名": team.name, "选手": team.players,
                   "总胜分": team.current_score, "总级分": team.round_score}
            for m in all_matches:
                if m.team_a_id != team.id and m.team_b_id != team.id: continue
                if m.score_a < 0: continue
                is_a = m.team_a_id == team.id
                my_s = m.score_a if is_a else m.score_b
                op_s = m.score_b if is_a else m.score_a
                wp = 3 if my_s > op_s else (1 if my_s == op_s else 0)
                row[f"第{m.round_no}轮 胜分"] = wp
                row[f"第{m.round_no}轮 级分"] = my_s
            export_data.append(row)
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            pd.DataFrame(export_data).to_excel(writer, index=False, sheet_name="成绩记录")

    output.seek(0)
    log_act("Export Excel", f"Downloaded full records (mode={conf.mode} stage={conf.stage}).", t.id)
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

@app.route('/delete_tournament/<int:tid>', methods=['POST'])
def delete_tournament(tid):
    if session.get('username') != 'admin': abort(403)
    t = Tournament.query.get_or_404(tid)
    name = t.name
    AuditLog.query.filter_by(tournament_id=tid).delete()
    Match.query.filter_by(tournament_id=tid).delete()
    Team.query.filter_by(tournament_id=tid).delete()
    SystemConfig.query.filter_by(tournament_id=tid).delete()
    TournamentInfo.query.filter_by(tournament_id=tid).delete()
    db.session.delete(t)
    db.session.commit()
    log_act("Delete Tournament", f"Deleted tournament: {name}")
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

@app.route('/confirm_finals', methods=['POST'])
def confirm_finals():
    """确认小组赛出线队伍，切换到决赛循环赛"""
    t = get_active_t()
    if not t: return redirect(url_for('setup'))
    conf = get_config(t.id)
    qualifiers = get_group_qualifiers(t.id)
    # 标记晋级队伍，重置决赛积分（保留历史）
    for team in Team.query.filter_by(tournament_id=t.id).all():
        team.is_finalist = False
    for q in qualifiers:
        q.is_finalist = True
        q.current_score = 0; q.round_score = 0
        q.history_opponents = ""; q.had_bye = False
        q.seat_ns_count = 0; q.seat_ew_count = 0
    conf.stage = 'finals'
    conf.current_round = 1
    db.session.flush()
    # 生成决赛第一轮（仅晋级队，使用普通 swiss pairing）
    random.shuffle(qualifiers)
    bye_team = None
    working = list(qualifiers)
    if len(working) % 2 == 1:
        bye_team = working[-1]; working = working[:-1]
        bye_team.had_bye = True; bye_team.current_score += 3
        log_act("Bye Round", f"Finals R1 bye: {bye_team.name} (+3)", t.id)
    for i in range(0, len(working)-1, 2):
        t1, t2 = working[i], working[i+1]
        p1 = [x.strip() for x in t1.players.replace('，',',').split(',')]
        p2 = [x.strip() for x in t2.players.replace('，',',').split(',')]
        is_6p = len(p1) >= 3 and len(p2) >= 3
        seats = assign_seats(t1, t2, p1, p2, is_6p)
        db.session.add(Match(tournament_id=t.id, round_no=1, table_no=i//2+1,
                             team_a_id=t1.id, team_b_id=t2.id,
                             team_a_name=t1.name, team_b_name=t2.name,
                             group_id=0, **seats))
    log_act("Start Finals", f"{len(qualifiers)}支队伍参加决赛循环赛", t.id)
    db.session.commit()
    return redirect(url_for('matches'))

@app.route('/next_r')
def next_r():
    t = get_active_t()
    conf = get_config(t.id)
    conf.current_round += 1
    if conf.mode == 1 and conf.stage == 'group':
        # 小组赛模式：按配对方式对每个小组分别配对
        table_counter = 1
        for g in range(1, conf.num_groups + 1):
            if (conf.pairing_mode or 'swiss') == 'roundrobin':
                pairs, bye_team = group_roundrobin_pairing(t.id, conf.current_round, g)
            else:
                pairs, bye_team = group_swiss_pairing(t.id, conf.current_round, g)
            if bye_team:
                bye_team.had_bye = True; bye_team.current_score += 3
                log_act("Bye Round", f"Group {g} R{conf.current_round} bye: {bye_team.name}", t.id)
            for t1, t2 in pairs:
                p1 = [x.strip() for x in t1.players.replace('，',',').split(',')]
                p2 = [x.strip() for x in t2.players.replace('，',',').split(',')]
                is_6p = len(p1) >= 3 and len(p2) >= 3
                seats = assign_seats(t1, t2, p1, p2, is_6p)
                db.session.add(Match(tournament_id=t.id, round_no=conf.current_round, table_no=table_counter,
                                     team_a_id=t1.id, team_b_id=t2.id,
                                     team_a_name=t1.name, team_b_name=t2.name,
                                     group_id=g, **seats))
                table_counter += 1
        log_act("Next Round (Group)", f"Round {conf.current_round} all groups", t.id)
    else:
        # 普通循环赛 / 决赛：原有逻辑不变
        if conf.mode == 1 and conf.stage == 'finals':
            # 决赛只对晋级队配对
            finalist_ids = [tm.id for tm in Team.query.filter_by(tournament_id=t.id, is_finalist=True).all()]
            teams = Team.query.filter(Team.id.in_(finalist_ids)).all()
            teams.sort(key=lambda x: (x.current_score, x.round_score), reverse=True)
            total_r = conf.total_rounds
            bye_team = None
            working = list(teams)
            if len(working) % 2 == 1:
                for team in reversed(working):
                    if not team.had_bye: bye_team = team; break
                if bye_team is None: bye_team = working[-1]
                working = [tm for tm in working if tm.id != bye_team.id]
                bye_team.had_bye = True; bye_team.current_score += 3
                log_act("Bye Round", f"Finals R{conf.current_round} bye: {bye_team.name}", t.id)
            result = _backtrack_pair(working, conf.current_round, total_r, set(), [], 0)
            if result is None:
                result = [(working[i], working[i+1]) for i in range(0, len(working)-1, 2)]
            for i, (t1, t2) in enumerate(result):
                p1 = [x.strip() for x in t1.players.replace('，',',').split(',')]
                p2 = [x.strip() for x in t2.players.replace('，',',').split(',')]
                is_6p = len(p1) >= 3 and len(p2) >= 3
                seats = assign_seats(t1, t2, p1, p2, is_6p)
                db.session.add(Match(tournament_id=t.id, round_no=conf.current_round, table_no=i+1,
                                     team_a_id=t1.id, team_b_id=t2.id,
                                     team_a_name=t1.name, team_b_name=t2.name,
                                     group_id=0, **seats))
            log_act("Next Round (Finals)", f"Round {conf.current_round}", t.id)
        else:
            if (conf.pairing_mode or 'swiss') == 'roundrobin':
                pairs, bye_team = roundrobin_pairing(t.id, conf.current_round)
                mode_label = "Round-Robin"
            else:
                pairs, bye_team = swiss_pairing(t.id, conf.current_round)
                mode_label = "Swiss V2"
            if bye_team:
                bye_team.had_bye = True; bye_team.current_score += 3
                log_act("Bye Round", f"Round {conf.current_round} bye: {bye_team.name} (+3 win pts)", t.id)
            for i, (t1, t2) in enumerate(pairs):
                p1 = [x.strip() for x in t1.players.replace('，',',').split(',')]
                p2 = [x.strip() for x in t2.players.replace('，',',').split(',')]
                is_6p = len(p1) >= 3 and len(p2) >= 3
                seats = assign_seats(t1, t2, p1, p2, is_6p)
                db.session.add(Match(tournament_id=t.id, round_no=conf.current_round, table_no=i+1,
                                     team_a_id=t1.id, team_b_id=t2.id,
                                     team_a_name=t1.name, team_b_name=t2.name, **seats))
            log_act("Next Round", f"Round {conf.current_round} ({mode_label})", t.id)
    db.session.commit()
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
        try: db.session.execute(text("ALTER TABLE team ADD COLUMN seat_ns_count INTEGER DEFAULT 0")); db.session.commit()
        except Exception: db.session.rollback()
        try: db.session.execute(text("ALTER TABLE team ADD COLUMN seat_ew_count INTEGER DEFAULT 0")); db.session.commit()
        except Exception: db.session.rollback()
        try: db.session.execute(text("ALTER TABLE team ADD COLUMN had_bye BOOLEAN DEFAULT FALSE")); db.session.commit()
        except Exception: db.session.rollback()
        # 小组赛扩展迁移
        try: db.session.execute(text("ALTER TABLE system_config ADD COLUMN mode INTEGER DEFAULT 0")); db.session.commit()
        except Exception: db.session.rollback()
        try: db.session.execute(text("ALTER TABLE system_config ADD COLUMN num_groups INTEGER DEFAULT 0")); db.session.commit()
        except Exception: db.session.rollback()
        try: db.session.execute(text("ALTER TABLE system_config ADD COLUMN advance_per_group INTEGER DEFAULT 0")); db.session.commit()
        except Exception: db.session.rollback()
        try: db.session.execute(text("ALTER TABLE system_config ADD COLUMN stage VARCHAR(20)")); db.session.commit()
        except Exception: db.session.rollback()
        try: db.session.execute(text("ALTER TABLE system_config ADD COLUMN pairing_mode VARCHAR(20) DEFAULT 'swiss'")); db.session.commit()
        except Exception: db.session.rollback()
        try: db.session.execute(text("ALTER TABLE team ADD COLUMN group_id INTEGER DEFAULT 0")); db.session.commit()
        except Exception: db.session.rollback()
        try: db.session.execute(text("ALTER TABLE team ADD COLUMN is_finalist BOOLEAN DEFAULT FALSE")); db.session.commit()
        except Exception: db.session.rollback()
        try: db.session.execute(text("ALTER TABLE match ADD COLUMN group_id INTEGER DEFAULT 0")); db.session.commit()
        except Exception: db.session.rollback()
        if not User.query.filter_by(username='admin').first():
            db.session.add(User(username='admin', password=generate_password_hash('123')))
            db.session.commit()

init_db()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)