from flask import Flask, render_template_string, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
import random

app = Flask(__name__)
app.secret_key = "guandan_secret" # 用于显示弹窗消息
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///guandan_v2.db'
db = SQLAlchemy(app)

# --- 数据库模型 ---
class Team(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    players = db.Column(db.String(200)) 
    score = db.Column(db.Integer, default=0) 
    game_mode = db.Column(db.String(10), default="4人赛")

class Match(db.Model): # 新增：对阵表模型
    id = db.Column(db.Integer, primary_key=True)
    table_no = db.Column(db.Integer)
    team_a_name = db.Column(db.String(50))
    team_b_name = db.Column(db.String(50))
    mode = db.Column(db.String(10))

# --- HTML 模板 (增加对阵展示区) ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>掼蛋赛事专业管理系统</title>
    <style>
        body { font-family: 'Microsoft YaHei', sans-serif; margin: 20px; background: #f0f2f5; }
        .container { max-width: 1000px; margin: auto; background: white; padding: 25px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.1); }
        h2, h3 { color: #1a1a1a; border-left: 5px solid #1890ff; padding-left: 15px; }
        table { width: 100%; border-collapse: collapse; margin: 15px 0; background: #fff; }
        th, td { border: 1px solid #e8e8e8; padding: 12px; text-align: center; }
        th { background-color: #fafafa; color: #555; }
        .btn { padding: 8px 20px; border: none; border-radius: 6px; cursor: pointer; text-decoration: none; font-size: 14px; transition: 0.3s; }
        .btn-blue { background: #1890ff; color: white; }
        .btn-orange { background: #fa8c16; color: white; margin-bottom: 10px; }
        .btn-green { background: #52c41a; color: white; }
        .match-card { background: #e6f7ff; border: 1px solid #91d5ff; padding: 10px; margin: 5px; border-radius: 4px; display: inline-block; width: 30%; }
        form { background: #f9f9f9; padding: 20px; border-radius: 8px; margin-bottom: 30px; }
        .alert { color: #f5222d; background: #fff1f0; padding: 10px; border-radius: 4px; margin-bottom: 10px; }
    </style>
</head>
<body>
    <div class="container">
        <h2>掼蛋赛事管理后台 (支持4人/6人)</h2>
        
        {% with messages = get_flashed_messages() %}
          {% if messages %}{% for msg in messages %}<div class="alert">{{ msg }}</div>{% endfor %}{% endif %}
        {% endwith %}

        <form action="/add_team" method="post">
            <strong>1. 队伍报名：</strong>
            队名: <input type="text" name="name" required style="width:120px;"> 
            队员: <input type="text" name="players" placeholder="P1,P2..." style="width:150px;">
            模式: <select name="game_mode">
                <option value="4人赛">4人赛</option>
                <option value="6人赛">6人赛</option>
            </select>
            <button type="submit" class="btn btn-blue">确认报名</button>
        </form>

        <div style="background:#fffbe6; padding:15px; border:1px solid #ffe58f; border-radius:8px;">
            <strong>2. 赛事调度中心：</strong><br><br>
            <a href="/generate_matches" class="btn btn-orange">🎲 一键自动生成本轮对阵表</a>
            <a href="/clear_matches" style="color:#999; margin-left:20px; font-size:12px;">重置对阵</a>
            
            <div style="margin-top:15px;">
                {% if matches %}
                    {% for m in matches %}
                    <div class="match-card">
                        <strong>第 {{ m.table_no }} 桌 ({{ m.mode }})</strong><br>
                        {{ m.team_a_name }} <span style="color:#ff4d4f;">VS</span> {{ m.team_b_name }}
                    </div>
                    {% endfor %}
                {% else %}
                    <p style="color:#999;">暂无对阵信息，请先添加队伍并点击生成。</p>
                {% endif %}
            </div>
        </div>

        <h3>3. 实时积分排行榜</h3>
        <table>
            <tr>
                <th>排名</th><th>队名</th><th>模式</th><th>成员</th><th>当前积分</th><th>管理操作</th>
            </tr>
            {% for team in teams %}
            <tr>
                <td>{{ loop.index }}</td>
                <td><strong>{{ team.name }}</strong></td>
                <td>{{ team.game_mode }}</td>
                <td><small>{{ team.players }}</small></td>
                <td style="color:#1890ff; font-weight:bold;">{{ team.score }}</td>
                <td>
                    <a href="/add_score/{{ team.id }}" class="btn btn-green">胜局 +1</a>
                    <a href="/delete/{{ team.id }}" style="color:#ff4d4f; margin-left:10px;">删除</a>
                </td>
            </tr>
            {% endfor %}
        </table>
    </div>
</body>
</html>
"""

# --- 路由逻辑 ---

@app.route('/')
def index():
    teams = Team.query.order_by(Team.score.desc()).all()
    matches = Match.query.all()
    return render_template_string(HTML_TEMPLATE, teams=teams, matches=matches)

@app.route('/add_team', methods=['POST'])
def add_team():
    new_team = Team(name=request.form['name'], players=request.form['players'], game_mode=request.form['game_mode'])
    db.session.add(new_team)
    db.session.commit()
    return redirect(url_for('index'))

@app.route('/generate_matches')
def generate_matches():
    # 清除旧对阵
    Match.query.delete()
    
    # 分模式获取队伍（4人赛和6人赛通常分开打）
    modes = ["4人赛", "6人赛"]
    table_counter = 1
    
    for mode in modes:
        teams = Team.query.filter_by(game_mode=mode).all()
        random.shuffle(teams) # 随机洗牌，保证公平
        
        if len(teams) < 2:
            if len(teams) == 1: flash(f"{mode}仅有1队，无法配对")
            continue
            
        for i in range(0, len(teams) - 1, 2):
            new_match = Match(
                table_no=table_counter,
                team_a_name=teams[i].name,
                team_b_name=teams[i+1].name,
                mode=mode
            )
            db.session.add(new_match)
            table_counter += 1
        
        if len(teams) % 2 != 0:
            flash(f"{mode}队伍总数为单数，最后一队（{teams[-1].name}）本轮轮空")

    db.session.commit()
    return redirect(url_for('index'))

@app.route('/clear_matches')
def clear_matches():
    Match.query.delete()
    db.session.commit()
    return redirect(url_for('index'))

@app.route('/add_score/<int:id>')
def add_score(id):
    team = Team.query.get(id)
    team.score += 1
    db.session.commit()
    return redirect(url_for('index'))

@app.route('/delete/<int:id>')
def delete_team(id):
    db.session.delete(Team.query.get(id))
    db.session.commit()
    return redirect(url_for('index'))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, port=5000)