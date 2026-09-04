"""
main.py — FastAPI backend for the Email Ticket Dashboard.

Serves the frontend, exposes CRUD + filter API routes for tickets,
and runs LLM classification as a decoupled background task with
retry logic and timeout protection.
"""

import time
import logging
import traceback
import os
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from contextlib import asynccontextmanager

from fastapi import FastAPI, BackgroundTasks, HTTPException, Query
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from supabase import create_client, Client

load_dotenv()

# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

TABLE_NAME = "email_dataset"
LLM_TIMEOUT_SECONDS = 120  # Max time to wait for a single LLM call
MAX_RETRIES = 2            # Number of retries before marking as 'failed'

ALLOWED_CLASSES = [
    "BILLING", "TECHNICAL", "ACCOUNT", "OTHER",
    "SECURITY", "SALES", "FEATURE_REQUEST",
]
ALLOWED_PRIORITIES = ["LOW", "MEDIUM", "HIGH"]

# ---------------------------------------------------------
# Logging
# ---------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ticket-dashboard")

# ---------------------------------------------------------
# Supabase Client
# ---------------------------------------------------------
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Thread pool used to run the synchronous LLM call with a timeout
_executor = ThreadPoolExecutor(max_workers=2)

# ---------------------------------------------------------
# Lifespan (startup / shutdown)
# ---------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Dashboard server starting up…")
    yield
    logger.info("Shutting down — draining thread pool…")
    _executor.shutdown(wait=False)


app = FastAPI(
    title="Email Ticket Dashboard",
    version="1.0.0",
    lifespan=lifespan,
)

# Serve static files (index.html, etc.)
app.mount("/static", StaticFiles(directory="static"), name="static")

# ---------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------
class TicketCreate(BaseModel):
    subject: str = Field(..., min_length=1, max_length=500)
    body: str = Field(..., min_length=1, max_length=10000)


class TicketResponse(BaseModel):
    id: int
    subject: str | None = None
    body: str | None = None
    Status: str | None = None

    # These are nullable — only populated after classification
    class_: str | None = Field(None, alias="class")
    priority: str | None = None
    summary: str | None = None

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------
# LLM Background Task
# ---------------------------------------------------------
def _run_llm_classification(subject: str, body: str) -> dict | None:
    """
    Import and call classify_ticket from LLMcall.py.
    Isolated in a function so we can run it inside the thread pool
    with a timeout.
    """
    from LLMcall import classify_ticket
    return classify_ticket(subject, body)


def classify_ticket_background(ticket_id: int, subject: str, body: str):
    """
    Background worker that:
      1. Calls the LLM with a timeout guard
      2. Validates the output schema
      3. Retries on failure up to MAX_RETRIES times
      4. Updates Supabase to 'classified' or 'failed'
    """
    logger.info(f"[BG] Starting classification for ticket #{ticket_id}")

    for attempt in range(1, MAX_RETRIES + 2):  # attempts 1, 2, 3
        try:
            # Run the synchronous LLM call in a thread with a timeout
            future = _executor.submit(_run_llm_classification, subject, body)
            result = future.result(timeout=LLM_TIMEOUT_SECONDS)

            # --- Handle unparseable output ---
            if not result or not isinstance(result, dict):
                logger.warning(
                    f"[BG] Ticket #{ticket_id} attempt {attempt}: "
                    f"Model output was unparseable or empty."
                )
                if attempt > MAX_RETRIES:
                    break
                continue

            # --- Extract and normalise ---
            predicted_class = str(result.get("class", "")).strip().upper()
            predicted_priority = str(result.get("priority", "")).strip().upper()
            predicted_summary = str(result.get("summary", "")).strip()

            # --- Validation guard (the "trap") ---
            if predicted_class not in ALLOWED_CLASSES or predicted_priority not in ALLOWED_PRIORITIES:
                logger.warning(
                    f"[BG] Ticket #{ticket_id} attempt {attempt}: "
                    f"Invalid values — class='{predicted_class}', "
                    f"priority='{predicted_priority}'. Retrying…"
                )
                if attempt > MAX_RETRIES:
                    break
                continue

            # --- Success: persist to Supabase ---
            supabase.table(TABLE_NAME).update({
                "class": predicted_class,
                "priority": predicted_priority,
                "summary": predicted_summary,
                "Status": "classified",
            }).eq("id", ticket_id).execute()

            logger.info(
                f"[BG] Ticket #{ticket_id} CLASSIFIED — "
                f"[{predicted_class}] [{predicted_priority}]"
            )
            return  # ← success, exit the retry loop

        except FuturesTimeoutError:
            logger.error(
                f"[BG] Ticket #{ticket_id} attempt {attempt}: "
                f"LLM timed out after {LLM_TIMEOUT_SECONDS}s."
            )
            if attempt > MAX_RETRIES:
                break

        except Exception:
            logger.error(
                f"[BG] Ticket #{ticket_id} attempt {attempt}: "
                f"Unexpected error:\n{traceback.format_exc()}"
            )
            if attempt > MAX_RETRIES:
                break

    # --- All retries exhausted → mark as failed ---
    logger.error(f"[BG] Ticket #{ticket_id} FAILED after {MAX_RETRIES + 1} attempts.")
    try:
        supabase.table(TABLE_NAME).update({
            "Status": "failed",
        }).eq("id", ticket_id).execute()
    except Exception:
        logger.error(
            f"[BG] Could not update ticket #{ticket_id} to 'failed': "
            f"{traceback.format_exc()}"
        )


# ---------------------------------------------------------
# Routes — Frontend
# ---------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    """Serve the single-page dashboard."""
    return FileResponse("static/index.html")


# ---------------------------------------------------------
# Routes — API
# ---------------------------------------------------------
@app.post("/api/tickets", status_code=202)
async def create_ticket(ticket: TicketCreate, background_tasks: BackgroundTasks):
    """
    Create a new ticket:
      1. Generate a unique numeric ID from the current timestamp.
      2. Insert into Supabase with Status='pending'.
      3. Return 202 Accepted immediately.
      4. Fire a background task to classify via the LLM.
    """
    # Sequential ID: find the current max and increment by 1
    try:
        max_row = (
            supabase.table(TABLE_NAME)
            .select("id")
            .lt("id", 1_000_000)          # ignore legacy timestamp-based IDs
            .order("id", desc=True)
            .limit(1)
            .execute()
        )
        ticket_id = (max_row.data[0]["id"] + 1) if max_row.data else 1001
    except Exception as e:
        logger.error(f"Failed to fetch max ID: {e}")
        raise HTTPException(status_code=500, detail=f"Could not generate ticket ID: {e}")

    # Insert as pending
    try:
        supabase.table(TABLE_NAME).insert({
            "id": ticket_id,
            "subject": ticket.subject,
            "body": ticket.body,
            "Status": "pending",
        }).execute()
    except Exception as e:
        logger.error(f"Failed to insert ticket: {e}")
        raise HTTPException(status_code=500, detail=f"Database insert failed: {e}")

    # Fire background classification
    background_tasks.add_task(
        classify_ticket_background,
        ticket_id,
        ticket.subject,
        ticket.body,
    )

    return {
        "message": "Ticket created and queued for classification.",
        "ticket_id": ticket_id,
        "status": "pending",
    }


@app.get("/api/tickets")
async def list_tickets(
    status: str | None = Query(None, alias="status"),
    ticket_class: str | None = Query(None, alias="class"),
    priority: str | None = Query(None, alias="priority"),
):
    """
    List tickets with optional filters for status, class, and priority.
    Returns all columns, ordered by id descending (newest first).
    """
    query = supabase.table(TABLE_NAME).select("*")

    if status:
        query = query.eq("Status", status)
    if ticket_class:
        query = query.eq("class", ticket_class.upper())
    if priority:
        query = query.eq("priority", priority.upper())

    query = query.order("id", desc=True)

    try:
        response = query.execute()
    except Exception as e:
        logger.error(f"Failed to fetch tickets: {e}")
        raise HTTPException(status_code=500, detail=f"Database query failed: {e}")

    return {"tickets": response.data, "count": len(response.data)}


@app.get("/api/tickets/{ticket_id}")
async def get_ticket(ticket_id: int):
    """Fetch a single ticket by its numeric ID."""
    try:
        response = (
            supabase.table(TABLE_NAME)
            .select("*")
            .eq("id", ticket_id)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database query failed: {e}")

    if not response.data:
        raise HTTPException(status_code=404, detail="Ticket not found.")

    return response.data[0]


@app.post("/api/tickets/{ticket_id}/reclassify", status_code=202)
async def reclassify_ticket(ticket_id: int, background_tasks: BackgroundTasks):
    """
    Re-trigger LLM classification for an existing ticket.
    Resets status to 'pending' and fires a new background task.
    """
    # Fetch the ticket
    try:
        response = (
            supabase.table(TABLE_NAME)
            .select("*")
            .eq("id", ticket_id)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database query failed: {e}")

    if not response.data:
        raise HTTPException(status_code=404, detail="Ticket not found.")

    ticket = response.data[0]

    # Reset to pending
    supabase.table(TABLE_NAME).update({
        "Status": "pending",
        "class": None,
        "priority": None,
        "summary": None,
    }).eq("id", ticket_id).execute()

    # Fire background classification
    background_tasks.add_task(
        classify_ticket_background,
        ticket_id,
        ticket.get("subject", ""),
        ticket.get("body", ""),
    )

    return {
        "message": "Ticket queued for reclassification.",
        "ticket_id": ticket_id,
        "status": "pending",
    }


@app.get("/api/stats")
async def get_stats():
    """Return aggregate counts for the stats bar."""
    try:
        all_tickets = supabase.table(TABLE_NAME).select("Status").execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database query failed: {e}")

    data = all_tickets.data
    total = len(data)
    pending = sum(1 for t in data if t.get("Status") == "pending")
    classified = sum(1 for t in data if t.get("Status") == "classified")
    failed = sum(1 for t in data if t.get("Status") == "failed")

    return {
        "total": total,
        "pending": pending,
        "classified": classified,
        "failed": failed,
    }
