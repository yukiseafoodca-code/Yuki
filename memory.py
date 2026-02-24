import sqlite3
import os

DB_PATH = "memory.db"

class MemoryDB:
    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.create_table()

    def create_table(self):
        cursor = self.conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory TEXT
            )
        """)
        self.conn.commit()

    def add_memory(self, text):
        cursor = self.conn.cursor()
        cursor.execute("INSERT INTO memory (memory) VALUES (?)", (text,))
        self.conn.commit()

    def get_all_memory(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT memory FROM memory")
        rows = cursor.fetchall()
        return [r[0] for r in rows]
