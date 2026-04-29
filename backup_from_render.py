# -*- coding: utf-8 -*-
"""
Render PostgreSQL -> 本地 SQLite + SQL 完整备份脚本
用法: python backup_from_render.py
"""
import sys, os, json, sqlite3
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')

DATABASE_URL = 'postgresql://guandan_db_user:roBoNgHWpK33rcGIHa5dVusefmXyV4Am@dpg-d72a93ua2pns73eska3g-a.oregon-postgres.render.com/guandan_db'

BACKUP_DIR  = os.path.join(os.path.dirname(__file__), 'backups')
os.makedirs(BACKUP_DIR, exist_ok=True)

ts         = datetime.now().strftime('%Y%m%d_%H%M%S')
SQLITE_OUT = os.path.join(BACKUP_DIR, f'guandan_render_backup_{ts}.db')
SQL_OUT    = os.path.join(BACKUP_DIR, f'guandan_render_backup_{ts}.sql')
JSON_OUT   = os.path.join(BACKUP_DIR, f'guandan_render_backup_{ts}.json')

try:
    import psycopg2
except ImportError:
    print("Installing psycopg2-binary...")
    os.system("pip install psycopg2-binary -q")
    import psycopg2

print("=" * 60)
print(f"Render PostgreSQL 完整备份")
print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 60)

# ── 1. 连接 Render PostgreSQL ──────────────────────────────────
print("\n[1/4] 连接 Render PostgreSQL...")
pg_conn = psycopg2.connect(DATABASE_URL, sslmode='require')
pg_cur  = pg_conn.cursor()
print("      连接成功 ✓")

# ── 2. 读取所有表结构与数据 ────────────────────────────────────
print("\n[2/4] 读取所有表数据...")
pg_cur.execute("""
    SELECT tablename FROM pg_tables
    WHERE schemaname='public' ORDER BY tablename
""")
tables = [row[0] for row in pg_cur.fetchall()]

all_data   = {}
sql_lines  = [
    f"-- Render PostgreSQL Backup",
    f"-- Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    f"-- Tables: {', '.join(tables)}",
    "",
]

for tbl in tables:
    # 取列名
    pg_cur.execute(f"""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema='public' AND table_name='{tbl}'
        ORDER BY ordinal_position
    """)
    cols_info = pg_cur.fetchall()
    col_names = [c[0] for c in cols_info]

    # 取数据（加双引号避免保留字冲突，如 user）
    pg_cur.execute(f'SELECT * FROM "{tbl}"')
    rows = pg_cur.fetchall()

    all_data[tbl] = {
        'columns': col_names,
        'col_types': [c[1] for c in cols_info],
        'rows': [list(r) for r in rows]
    }

    print(f"      {tbl}: {len(rows)} 条记录 ✓")

    # 生成 SQL INSERT 语句
    sql_lines.append(f"\n-- Table: {tbl}")
    sql_lines.append(f"DELETE FROM {tbl};")
    for row in rows:
        vals = []
        for v in row:
            if v is None:
                vals.append("NULL")
            elif isinstance(v, bool):
                vals.append("TRUE" if v else "FALSE")
            elif isinstance(v, (int, float)):
                vals.append(str(v))
            else:
                escaped = str(v).replace("'", "''")
                vals.append(f"'{escaped}'")
        sql_lines.append(
            f"INSERT INTO {tbl} ({', '.join(col_names)}) VALUES ({', '.join(vals)});"
        )

pg_conn.close()

# ── 3. 保存 SQL 和 JSON 备份 ───────────────────────────────────
print(f"\n[3/4] 保存备份文件...")

with open(SQL_OUT, 'w', encoding='utf-8') as f:
    f.write('\n'.join(sql_lines))
print(f"      SQL 备份: {SQL_OUT} ✓")

def json_serial(obj):
    if hasattr(obj, 'isoformat'):
        return obj.isoformat()
    return str(obj)

with open(JSON_OUT, 'w', encoding='utf-8') as f:
    json.dump(all_data, f, ensure_ascii=False, indent=2, default=json_serial)
print(f"      JSON 备份: {JSON_OUT} ✓")

# ── 4. 写入本地 SQLite ─────────────────────────────────────────
print(f"\n[4/4] 写入本地 SQLite...")

# 类型映射 PostgreSQL -> SQLite
TYPE_MAP = {
    'integer': 'INTEGER', 'bigint': 'INTEGER', 'smallint': 'INTEGER',
    'serial': 'INTEGER', 'bigserial': 'INTEGER',
    'boolean': 'INTEGER',
    'text': 'TEXT', 'character varying': 'TEXT', 'varchar': 'TEXT', 'char': 'TEXT',
    'real': 'REAL', 'double precision': 'REAL', 'numeric': 'REAL', 'decimal': 'REAL',
    'timestamp without time zone': 'TEXT', 'timestamp with time zone': 'TEXT',
    'date': 'TEXT', 'time without time zone': 'TEXT',
    'json': 'TEXT', 'jsonb': 'TEXT',
}

sq_conn = sqlite3.connect(SQLITE_OUT)
sq_cur  = sq_conn.cursor()

for tbl, info in all_data.items():
    col_defs = []
    for cname, ctype in zip(info['columns'], info['col_types']):
        sq_type = TYPE_MAP.get(ctype, 'TEXT')
        col_defs.append(f"{cname} {sq_type}")

    sq_cur.execute(f'DROP TABLE IF EXISTS "{tbl}"')
    sq_cur.execute(f'CREATE TABLE "{tbl}" ({", ".join(col_defs)})')

    placeholders = ','.join(['?'] * len(info['columns']))
    for row in info['rows']:
        converted = []
        for v in row:
            if isinstance(v, bool):
                converted.append(1 if v else 0)
            elif hasattr(v, 'isoformat'):
                converted.append(v.isoformat())
            else:
                converted.append(v)
        sq_cur.execute(
            f'INSERT INTO "{tbl}" VALUES ({placeholders})', converted
        )
    print(f"      {tbl}: {len(info['rows'])} 条写入 ✓")

sq_conn.commit()
sq_conn.close()

print(f"      SQLite 备份: {SQLITE_OUT} ✓")

# ── 汇总 ───────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("备份完成！文件位置：")
print(f"  SQLite: {SQLITE_OUT}")
print(f"  SQL:    {SQL_OUT}")
print(f"  JSON:   {JSON_OUT}")
total_rows = sum(len(v['rows']) for v in all_data.values())
print(f"\n共备份 {len(tables)} 张表，{total_rows} 条记录")
print("=" * 60)
