"""Initialize the configured database without deleting existing business data."""
import re,sys
from pathlib import Path
from sqlalchemy import create_engine,text
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import db


def main():
 name=db.engine.url.database
 if not name or not re.fullmatch(r'[A-Za-z0-9_]+',name):raise ValueError('数据库名只支持字母、数字和下划线')
 admin=create_engine(db.engine.url.set(database='mysql'))
 with admin.begin() as conn:conn.execute(text(f'CREATE DATABASE IF NOT EXISTS \u0060{name}\u0060 CHARACTER SET utf8mb4'))
 admin.dispose()
 schema=(Path(__file__).resolve().parents[1]/'schema.sql').read_text(encoding='utf-8')
 lines='\n'.join(line for line in schema.splitlines() if not line.strip().startswith('--'))
 with db.engine.begin() as conn:
  for statement in lines.split(';'):
   if statement.strip() and not statement.strip().upper().startswith(('CREATE DATABASE','USE ')):conn.execute(text(statement))
 from app.migrations import ensure_delivery_schema
 ensure_delivery_schema()
 print('数据库与增量迁移已就绪（保留已有数据）')

if __name__=='__main__':main()
