import os
from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

class MemoryDB:
    def __init__(self):
        self.client = create_client(SUPABASE_URL, SUPABASE_KEY)

    def add_memory(self, text):
        self.client.table("memory").insert({"memory": text}).execute()

    def get_all_memory(self):
        response = self.client.table("memory").select("memory").execute()
        return [row["memory"] for row in response.data]

    def set_preference(self, key, value):
        self.client.table("preferences").upsert({"key": key, "value": value}).execute()

    def get_preference(self, key):
        response = self.client.table("preferences").select("value").eq("key", key).execute()
        if response.data:
            return response.data[0]["value"]
        return None
