import json
import os
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
from LLMcall import classify_ticket
import db_csv

load_dotenv()

ALLOWED_CLASSES = [
    "BILLING", "TECHNICAL", "ACCOUNT", "OTHER",
    "SECURITY", "SALES", "FEATURE_REQUEST",
]
ALLOWED_PRIORITIES = ["LOW", "MEDIUM", "HIGH"]


def _classify_and_update(ticket_id: int, subject: str, body: str):
    try:
        result = classify_ticket(subject, body)

        if not result or not isinstance(result, dict):
            print(f"  Ticket #{ticket_id}: LLM returned unparseable output → failed")
            db_csv.update_ticket(ticket_id, {"Status": "failed"})
            return

        predicted_class = str(result.get("class", "")).strip().upper()
        predicted_priority = str(result.get("priority", "")).strip().upper()
        predicted_summary = str(result.get("summary", "")).strip()

        if predicted_class not in ALLOWED_CLASSES or predicted_priority not in ALLOWED_PRIORITIES:
            print(f"  Ticket #{ticket_id}: Invalid class/priority → failed")
            db_csv.update_ticket(ticket_id, {"Status": "failed"})
            return

        db_csv.update_ticket(ticket_id, {
            "class": predicted_class,
            "priority": predicted_priority,
            "summary": predicted_summary,
            "Status": "classified",
        })
        print(f"  Ticket #{ticket_id}: CLASSIFIED [{predicted_class}] [{predicted_priority}]")

    except Exception as e:
        print(f"  Ticket #{ticket_id}: Error — {e}")
        db_csv.update_ticket(ticket_id, {"Status": "failed"})


def process_json_data(json_list):
    executor = ThreadPoolExecutor(max_workers=4)
    futures = []

    for item in json_list:
        raw_id = item.get("id")
        numeric_id = int(raw_id.replace("t-", ""))
        subject = item.get("subject", "")
        body = item.get("body", "")

        check = db_csv.get_ticket(numeric_id)
        if check:
            print(f"Skipping {raw_id}: Already in database.")
            continue

        db_csv.insert_ticket({
            "id": numeric_id,
            "subject": subject,
            "body": body,
            "Status": "pending",
        })
        print(f"Inserted {raw_id} -> queuing classification")

        fut = executor.submit(_classify_and_update, numeric_id, subject, body)
        futures.append(fut)

    print(f"\nAll inserts done. Waiting for {len(futures)} classification(s)")
    for fut in as_completed(futures):
        fut.result()

    executor.shutdown(wait=True)
    print("Done.")


if __name__ == "__main__":
    dataset = json.load(open("dataset1.json", encoding="utf-8"))
    process_json_data(dataset)