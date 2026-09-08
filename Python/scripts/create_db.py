# scripts/create_db.py
"""
Create a fresh radon.db from schema.sql.
WARNING: deletes any existing radon.db in the current directory.
"""
import sqlite3
from pathlib import Path

DB_PATH = Path("texashydro.db")
SCHEMA_PATH = Path(r"C:\Users\whe255\dev\texashydrodatabase\SQL\schema\schema.sql")

def create_db():
    if DB_PATH.exists():
        response = input(f"{DB_PATH} exists. Overwrite? [y/N]: ")
        if response.lower() != 'y':
            print("Aborted.")
            return
        DB_PATH.unlink()
    
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys = ON")
    
    with open(SCHEMA_PATH) as f:
        con.executescript(f.read())
    con.commit()
    con.close()
    
    print(f"Created {DB_PATH} from {SCHEMA_PATH}")

if __name__ == "__main__":
    create_db()