import os
from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

class MemoryDB:
    def __init__(self):
        self.client = create_client(SUPABASE_URL, SUPABASE_KEY)

    def add_memory(self, content, category="一般", sender_name="未知"):
        self.client.table("memory_v2").insert({
            "category": category,
            "content": content,
            "sender_name": sender_name
        }).execute()

    def get_by_category(self, category):
        response = self.client.table("memory_v2").select("content, sender_name").eq("category", category).execute()
        return [f"{r['sender_name']}: {r['content']}" for r in response.data]

    def get_all_memory(self):
        response = self.client.table("memory_v2").select("category, content, sender_name").execute()
        return [f"[{r['category']}] {r['sender_name']}: {r['content']}" for r in response.data]

    def forget_all(self):
        self.client.table("memory_v2").delete().neq("id", 0).execute()

    def set_preference(self, key, value):
        self.client.table("preferences").upsert({"key": key, "value": value}).execute()

    def get_preference(self, key):
        response = self.client.table("preferences").select("value").eq("key", key).execute()
        if response.data:
            return response.data[0]["value"]
        return None

    # 行事曆
    def add_event(self, title, category, event_date, reminder_days=1, created_by="未知"):
        self.client.table("calendar").insert({
            "title": title,
            "category": category,
            "event_date": event_date,
            "reminder_days": reminder_days,
            "created_by": created_by
        }).execute()

    def get_upcoming_events(self, days=7):
        from datetime import date, timedelta
        today = date.today().isoformat()
        future = (date.today() + timedelta(days=days)).isoformat()
        response = self.client.table("calendar").select("*").gte("event_date", today).lte("event_date", future).order("event_date").execute()
        return response.data

    def get_all_events(self):
        response = self.client.table("calendar").select("*").order("event_date").execute()
        return response.data

    def delete_event(self, event_id):
        self.client.table("calendar").delete().eq("id", event_id).execute()

    # 購物清單
    def add_shopping(self, item, quantity="1", added_by="未知"):
        self.client.table("shopping").insert({
            "item": item,
            "quantity": quantity,
            "added_by": added_by
        }).execute()

    def get_shopping_list(self):
        response = self.client.table("shopping").select("*").eq("done", False).execute()
        return response.data

    def done_shopping(self, item_name):
        self.client.table("shopping").update({"done": True}).ilike("item", f"%{item_name}%").execute()

    def clear_shopping(self):
        self.client.table("shopping").delete().eq("done", True).execute()

    # 記帳
    def add_expense(self, amount, category, description, added_by="未知"):
        from datetime import date
        self.client.table("expenses").insert({
            "amount": amount,
            "category": category,
            "description": description,
            "added_by": added_by,
            "expense_date": date.today().isoformat()
        }).execute()

    def get_monthly_expenses(self):
        from datetime import date
        month_start = date.today().replace(day=1).isoformat()
        response = self.client.table("expenses").select("*").gte("expense_date", month_start).execute()
        return response.data
