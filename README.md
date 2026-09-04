# Email Ticket Dashboard

## How to Run

1. **Install dependencies** (first time only)

   ```
   pip install -r requirements.txt
   ```

2. **Start the server**

   ```
   uvicorn main:app --reload --port 8000
   ```

   Open **http://localhost:8000** in your browser.


3. **Load the dataset on a new terminal** (first time only)

   ```
   python ticketIngestion.py
   ```

---

## Design Notes

- **Storage:** Supabase (hosted Postgres), single table `email_dataset`.

- **Async work:** `POST /api/tickets` returns 202 immediately and runs classification in a FastAPI `BackgroundTasks` callback, which submits the LLM call to a `ThreadPoolExecutor` with a 120s timeout.

- **Concurrency:** `max_workers=2` — at most 2 classifications run in parallel.

- **Restart:** In-flight background tasks are lost. Affected tickets stay `pending` and can be retried via the reclassify endpoint.

- **Retries & failure:** 3 attempts total (`MAX_RETRIES=2`). A ticket becomes `failed` after all attempts fail due to timeout, unparseable output, invalid class/priority values, or uncaught exceptions.

- **Prompt injection:** User input is wrapped in `<ticket>` XML tags with a system prompt instructing the model to ignore embedded instructions. Output is validated against an allowed enum — any class or priority outside the whitelist is rejected.

- **API shape:**

  | Method | Path | Status | Body / Params |
  |--------|------|--------|---------------|
  | `POST` | `/api/tickets` | `202` | `{ "subject", "body" }` |
  | `GET` | `/api/tickets` | `200` | `?status=&class=&priority=` |
  | `GET` | `/api/tickets/{id}` | `200` | — |
  | `POST` | `/api/tickets/{id}/reclassify` | `202` | — |
  | `GET` | `/api/stats` | `200` | — |

  Errors: `{ "detail": "..." }` with `404`, `422`, or `500`.