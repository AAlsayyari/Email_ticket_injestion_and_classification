import os
import json
from supabase import create_client, Client

# 1. Set up your Supabase connection
# (It's best practice to store these in environment variables)
SUPABASE_URL = "https://kgptfrbyxgggizgfrgeb.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImtncHRmcmJ5eGdnZ2l6Z2ZyZ2ViIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODgzNTYzNzAsImV4cCI6MjEwMzkzMjM3MH0.gsBJ6wGQessT8KFpA26OjCKKR1yzhCaS_WuLj86YawQ"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def process_json_data(json_list):
    for item in json_list:
        raw_id = item.get("id")
        numeric_id = int(raw_id.replace("t-", ""))
        
        # 1. Update your db_item to include the new Status column
        db_item = {
            "id": numeric_id,
            "subject": item.get("subject"),
            "body": item.get("body"),
            "Status": "pending"  # <--- Added this line!
        }
        
        check_response = supabase.table('email_dataset').select('id').eq('id', numeric_id).execute()
        
        if len(check_response.data) > 0:
            print(f"Skipping {raw_id}: Already in database.")
            continue
            
        else:
            print(f"Inserting {raw_id}: New record found.")
            # 2. Insert the data (which now includes the pending status)
            insert_response = supabase.table('email_dataset').insert(db_item).execute()
            print(f"Successfully added {raw_id}")

# Your incoming JSON data (wrapped in a list so we can loop through multiple)
dataset = json.load(open('dataset1.json'))

# Run the function
process_json_data(dataset)