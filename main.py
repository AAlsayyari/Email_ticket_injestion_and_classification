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

import db_csv

load_dotenv()

TABLE_NAME = "email_dataset"
LLM_TIMEOUT_SECONDS = 120  
MAX_RETRIES = 2            

ALLOWED_CLASSES = [
    "BILLING", "TECHNICAL", "ACCOUNT", "OTHER",
    "SECURITY", "SALES", "FEATURE_REQUEST",
]
ALLOWED_PRIORITIES = ["LOW", "MEDIUM", "HIGH"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ticket-dashboard")

_executor = ThreadPoolExecutor(max_workers=2)

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

app.mount("/static", StaticFiles(directory="static"), name="static")

class TicketCreate(BaseModel):
    subject: str = Field(..., min_length=1, max_length=500)
    body: str = Field(..., min_length=1, max_length=10000)


class TicketResponse(BaseModel):
    id: int
    subject: str | None = None
    body: str | None = None
    Status: str | None = None

    class_: str | None = Field(None, alias="class")
    priority: str | None = None
    summary: str | None = None

    model_config = {"populate_by_name": True}


def _run_llm_classification(subject: str, body: str) -> dict | None:

    from LLMcall import classify_ticket
    return classify_ticket(subject, body)


def classify_ticket_background(ticket_id: int, subject: str, body: str):

    logger.info(f"[BG] Starting classification for ticket #{ticket_id}")

    for attempt in range(1, MAX_RETRIES + 2): 
        try:
            future = _executor.submit(_run_llm_classification, subject, body)
            result = future.result(timeout=LLM_TIMEOUT_SECONDS)

            if not result or not isinstance(result, dict):
                logger.warning(
                    f"[BG] Ticket #{ticket_id} attempt {attempt}: "
                    f"Model output was unparseable or empty."
                )
                if attempt > MAX_RETRIES:
                    break
                continue

            predicted_class = str(result.get("class", "")).strip().upper()
            predicted_priority = str(result.get("priority", "")).strip().upper()
            predicted_summary = str(result.get("summary", "")).strip()

            if predicted_class not in ALLOWED_CLASSES or predicted_priority not in ALLOWED_PRIORITIES:
                logger.warning(
                    f"[BG] Ticket #{ticket_id} attempt {attempt}: "
                    f"Invalid values — class='{predicted_class}', "
                    f"priority='{predicted_priority}'. Retrying…"
                )
                if attempt > MAX_RETRIES:
                    break
                continue

            db_csv.update_ticket(ticket_id, {
                "class": predicted_class,
                "priority": predicted_priority,
                "summary": predicted_summary,
                "Status": "classified",
            })

            logger.info(
                f"[BG] Ticket #{ticket_id} CLASSIFIED — "
                f"[{predicted_class}] [{predicted_priority}]"
            )
            return  

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

    logger.error(f"[BG] Ticket #{ticket_id} FAILED after {MAX_RETRIES + 1} attempts.")
    try:
        db_csv.update_ticket(ticket_id, {
            "Status": "failed",
        })
    except Exception:
        logger.error(
            f"[BG] Could not update ticket #{ticket_id} to 'failed': "
            f"{traceback.format_exc()}"
        )


@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    return FileResponse("static/index.html")

@app.post("/api/tickets", status_code=202)
async def create_ticket(ticket: TicketCreate, background_tasks: BackgroundTasks):
    try:
        ticket_id = db_csv.get_next_ticket_id()
    except Exception as e:
        logger.error(f"Failed to fetch next ID: {e}")
        raise HTTPException(status_code=500, detail=f"Could not generate ticket ID: {e}")

    try:
        db_csv.insert_ticket({
            "id": ticket_id,
            "subject": ticket.subject,
            "body": ticket.body,
            "Status": "pending",
        })
    except Exception as e:
        logger.error(f"Failed to insert ticket: {e}")
        raise HTTPException(status_code=500, detail=f"CSV storage insert failed: {e}")

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
    try:
        tickets = db_csv.list_tickets(status=status, ticket_class=ticket_class, priority=priority)
    except Exception as e:
        logger.error(f"Failed to fetch tickets: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch tickets: {e}")

    return {"tickets": tickets, "count": len(tickets)}


@app.get("/api/tickets/{ticket_id}")
async def get_ticket(ticket_id: int):
    try:
        ticket = db_csv.get_ticket(ticket_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve ticket: {e}")

    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found.")

    return ticket


@app.post("/api/tickets/{ticket_id}/reclassify", status_code=202)
async def reclassify_ticket(ticket_id: int, background_tasks: BackgroundTasks):
    try:
        ticket = db_csv.get_ticket(ticket_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve ticket: {e}")

    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found.")

    db_csv.update_ticket(ticket_id, {
        "Status": "pending",
        "class": None,
        "priority": None,
        "summary": None,
    })

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


@app.post("/api/tickets/reclassify-failed", status_code=202)
async def reclassify_failed_tickets(background_tasks: BackgroundTasks):
    try:
        failed_tickets = db_csv.list_tickets(status="failed")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Query failed: {e}")

    if not failed_tickets:
        return {"message": "No failed tickets to reclassify.", "count": 0}

    count = len(failed_tickets)

    try:
        db_csv.update_tickets_by_status("failed", {
            "Status": "pending",
            "class": None,
            "priority": None,
            "summary": None,
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Update failed: {e}")

    for ticket in failed_tickets:
        background_tasks.add_task(
            classify_ticket_background,
            ticket["id"],
            ticket.get("subject", ""),
            ticket.get("body", ""),
        )

    return {
        "message": f"Successfully queued {count} failed tickets for reclassification.",
        "count": count,
    }


@app.get("/api/stats")
async def get_stats():
    try:
        return db_csv.get_stats()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to calculate stats: {e}")
