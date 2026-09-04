import json
import os
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
from supabase import create_client, Client
from LLMcall import classify_ticket

load_dotenv()

# ---------------------------------------------------------
# Supabase connection
# ---------------------------------------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

ALLOWED_CLASSES = [
    "BILLING", "TECHNICAL", "ACCOUNT", "OTHER",
    "SECURITY", "SALES", "FEATURE_REQUEST",
]
ALLOWED_PRIORITIES = ["LOW", "MEDIUM", "HIGH"]


def _classify_and_update(ticket_id: int, subject: str, body: str):
    """Classify a single ticket via the LLM and update the DB."""
    try:
        result = classify_ticket(subject, body)

        if not result or not isinstance(result, dict):
            print(f"  Ticket #{ticket_id}: LLM returned unparseable output → failed")
            supabase.table("email_dataset").update({"Status": "failed"}).eq("id", ticket_id).execute()
            return

        predicted_class = str(result.get("class", "")).strip().upper()
        predicted_priority = str(result.get("priority", "")).strip().upper()
        predicted_summary = str(result.get("summary", "")).strip()

        if predicted_class not in ALLOWED_CLASSES or predicted_priority not in ALLOWED_PRIORITIES:
            print(f"  Ticket #{ticket_id}: Invalid class/priority → failed")
            supabase.table("email_dataset").update({"Status": "failed"}).eq("id", ticket_id).execute()
            return

        supabase.table("email_dataset").update({
            "class": predicted_class,
            "priority": predicted_priority,
            "summary": predicted_summary,
            "Status": "classified",
        }).eq("id", ticket_id).execute()
        print(f"  Ticket #{ticket_id}: CLASSIFIED [{predicted_class}] [{predicted_priority}]")

    except Exception as e:
        print(f"  Ticket #{ticket_id}: Error — {e}")
        supabase.table("email_dataset").update({"Status": "failed"}).eq("id", ticket_id).execute()


def process_json_data(json_list):
    """Insert tickets and classify them concurrently."""
    executor = ThreadPoolExecutor(max_workers=4)
    futures = []

    for item in json_list:
        raw_id = item.get("id")
        numeric_id = int(raw_id.replace("t-", ""))
        subject = item.get("subject", "")
        body = item.get("body", "")

        # Skip duplicates
        check = supabase.table("email_dataset").select("id").eq("id", numeric_id).execute()
        if check.data:
            print(f"Skipping {raw_id}: Already in database.")
            continue

        # Insert as pending
        supabase.table("email_dataset").insert({
            "id": numeric_id,
            "subject": subject,
            "body": body,
            "Status": "pending",
        }).execute()
        print(f"Inserted {raw_id} → queuing classification…")

        # Immediately submit classification to a background thread
        fut = executor.submit(_classify_and_update, numeric_id, subject, body)
        futures.append(fut)

    # Wait for all classifications to finish
    print(f"\nAll inserts done. Waiting for {len(futures)} classification(s)…")
    for fut in as_completed(futures):
        fut.result()  # surfaces any unhandled exceptions

    executor.shutdown(wait=True)
    print("Done.")


# Run
dataset = json.load(open("dataset1.json"))
process_json_data(dataset)