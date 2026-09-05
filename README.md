# Email Ticket Dashboard
### Note: I have left a set of read/write keys for a temporary sandbox Supabase database in the .env file so you can test this out of the box without needing to configure your own database. These keys will be revoked after the review.
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
  | `POST` | `/api/tickets/reclassify-failed` | `202` | — |
  | `GET` | `/api/stats` | `200` | — |

  Errors: `{ "detail": "..." }` with `404`, `422`, or `500`.

### Bonus Objective Completed: Re-classify Tickets

Both single-ticket and bulk re-classification have been implemented to handle model timeouts and prompt injections. 
- You can retry an individual failed ticket from the table by expanding it and clicking "Reclassify".
- You can bulk-retry all failed tickets using the "Retry Failed" button next to the Auto-refresh toggle. This triggers `POST /api/tickets/reclassify-failed`, queuing background tasks for all failed tickets. 

### What I would change or add with more time; and anything I consider a weakness of my solution.
   
   **Durable Task Queue:**
   Currently, background tasks are run in server memory. If the server restarts or crashes, in-flight tickets are lost (though they remain 'pending' and can be manually reclassified). In a production environment, I would replace FastAPI's `BackgroundTasks` with a durable queue like Celery and Redis/RabbitMQ to guarantee execution.


   
   **Model Hosting:**
   The LLM is currently loaded directly into the web server's memory. In a multi-worker production deployment, this would bloat memory usage. I would decouple the LLM inference into its own dedicated microservice (using something like vLLM or Ollama) and call it via an internal API.
   
   **Pagination:** The `GET /api/tickets` endpoint currently fetches all tickets that match the applied filters. For a production system with millions of tickets, I would implement standard `limit` and `offset` query parameters to paginate the database response and update the frontend table to handle infinite scrolling or numbered pages.

### AI use.

   **UI:** 
   Used AI to help with the design and code of the UI.
   
   **Script stitching:** 
   Used AI to stitch together scripts that I made in order to run them in a unified manner.
   
   **README.md:** 
   Used AI to help with the design and content of the README.md file.
