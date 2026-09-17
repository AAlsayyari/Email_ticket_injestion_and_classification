import os
import csv
import threading
from typing import List, Dict, Any, Optional

CSV_FILE = os.environ.get("TICKETS_CSV_PATH", "tickets.csv")

FIELDNAMES = ["id", "subject", "body", "Status", "class", "priority", "summary"]

_lock = threading.RLock()


def _ensure_csv_exists(file_path: str = CSV_FILE):
    if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
        with open(file_path, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()


def _read_all_tickets(file_path: str = CSV_FILE) -> List[Dict[str, Any]]:
    _ensure_csv_exists(file_path)
    tickets = []
    with open(file_path, mode="r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row or not row.get("id"):
                continue
            try:
                row_id = int(row["id"])
            except ValueError:
                continue
            tickets.append({
                "id": row_id,
                "subject": row.get("subject", ""),
                "body": row.get("body", ""),
                "Status": row.get("Status", "pending"),
                "class": row.get("class") or None,
                "priority": row.get("priority") or None,
                "summary": row.get("summary") or None,
            })
    return tickets


def _write_all_tickets(tickets: List[Dict[str, Any]], file_path: str = CSV_FILE):
    temp_file = f"{file_path}.tmp"
    with open(temp_file, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for t in tickets:
            writer.writerow({
                "id": t.get("id"),
                "subject": t.get("subject", ""),
                "body": t.get("body", ""),
                "Status": t.get("Status", "pending"),
                "class": t.get("class") or "",
                "priority": t.get("priority") or "",
                "summary": t.get("summary") or "",
            })
    os.replace(temp_file, file_path)


def get_next_ticket_id(file_path: str = CSV_FILE) -> int:
    with _lock:
        tickets = _read_all_tickets(file_path)
        numeric_ids = [t["id"] for t in tickets if t["id"] < 1_000_000]
        return (max(numeric_ids) + 1) if numeric_ids else 1001


def insert_ticket(ticket: Dict[str, Any], file_path: str = CSV_FILE) -> Dict[str, Any]:
    with _lock:
        tickets = _read_all_tickets(file_path)
        ticket_id = int(ticket["id"])
        # Check if already exists
        for existing in tickets:
            if existing["id"] == ticket_id:
                return existing

        new_ticket = {
            "id": ticket_id,
            "subject": ticket.get("subject", ""),
            "body": ticket.get("body", ""),
            "Status": ticket.get("Status", "pending"),
            "class": ticket.get("class") or None,
            "priority": ticket.get("priority") or None,
            "summary": ticket.get("summary") or None,
        }
        tickets.append(new_ticket)
        _write_all_tickets(tickets, file_path)
        return new_ticket


def update_ticket(ticket_id: int, updates: Dict[str, Any], file_path: str = CSV_FILE) -> Optional[Dict[str, Any]]:
    with _lock:
        tickets = _read_all_tickets(file_path)
        updated = None
        for t in tickets:
            if t["id"] == ticket_id:
                t.update(updates)
                updated = t
                break
        if updated:
            _write_all_tickets(tickets, file_path)
        return updated


def update_tickets_by_status(current_status: str, updates: Dict[str, Any], file_path: str = CSV_FILE) -> List[Dict[str, Any]]:
    with _lock:
        tickets = _read_all_tickets(file_path)
        matched = []
        for t in tickets:
            if t.get("Status") == current_status:
                t.update(updates)
                matched.append(t)
        if matched:
            _write_all_tickets(tickets, file_path)
        return matched


def get_ticket(ticket_id: int, file_path: str = CSV_FILE) -> Optional[Dict[str, Any]]:
    with _lock:
        tickets = _read_all_tickets(file_path)
        for t in tickets:
            if t["id"] == ticket_id:
                return t
        return None


def list_tickets(
    status: Optional[str] = None,
    ticket_class: Optional[str] = None,
    priority: Optional[str] = None,
    file_path: str = CSV_FILE
) -> List[Dict[str, Any]]:
    with _lock:
        tickets = _read_all_tickets(file_path)
        filtered = tickets

        if status:
            filtered = [t for t in filtered if t.get("Status") == status]
        if ticket_class:
            filtered = [t for t in filtered if (t.get("class") or "").upper() == ticket_class.upper()]
        if priority:
            filtered = [t for t in filtered if (t.get("priority") or "").upper() == priority.upper()]

        filtered.sort(key=lambda x: x["id"], reverse=True)
        return filtered


def get_stats(file_path: str = CSV_FILE) -> Dict[str, int]:
    with _lock:
        tickets = _read_all_tickets(file_path)
        total = len(tickets)
        pending = sum(1 for t in tickets if t.get("Status") == "pending")
        classified = sum(1 for t in tickets if t.get("Status") == "classified")
        failed = sum(1 for t in tickets if t.get("Status") == "failed")
        return {
            "total": total,
            "pending": pending,
            "classified": classified,
            "failed": failed,
        }
