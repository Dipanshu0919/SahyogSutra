import sqlitecloud, os
from dotenv import load_dotenv
load_dotenv()
db = sqlitecloud.connect(os.environ['SQLITECLOUD'])
rows = db.execute('LIST CONNECTIONS').fetchall()
for r in rows:
    try: db.execute(f'CLOSE CONNECTION {r[0]}')
    except Exception as e:
        print(f"ERROR CLOSING CONNECTION {r[0]}: {e}")
print(f'Done — closed {len(rows)} connections')
