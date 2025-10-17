import os
import uvicorn
import httpx
import asyncio
import sys
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
import logging
from datetime import datetime

# --- Configuration ---
# Load from environment variables
SETTINGS = {
    "EVALUATION_URL": os.getenv("EVALUATION_URL", "http://127.0.0.1:8000/notify"),
    "STUDENT_API_ENDPOINT": os.getenv("STUDENT_API_ENDPOINT"),
    "SHARED_SECRET": os.getenv("SHARED_SECRET"),
    # "STUDENT_API_ENDPOINT": "http://127.0.0.1:8000/task",
    # "SHARED_SECRET": "your_secret_key_here",
    # "EVALUATION_URL": "http://127.0.0.1:8002/notify",
    "ALLOWED_ORIGINS": os.getenv("ALLOWED_ORIGINS", "*").split(",")
}

# Basic Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Ensure subprocess-capable event loop on Windows before any loop is created
if sys.platform == "win32" and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# --- In-Memory Database ---
# A simple dictionary to act as our database for storing task state and results.
# In a real application, you would use a proper database like PostgreSQL or SQLite.
DB: Dict[str, Dict[str, Any]] = {}

# --- Pydantic Models ---
# Models for data validation and serialization.

class Attachment(BaseModel):
    filename: str
    content: str


class TaskRequest(BaseModel):
    email: str
    secret: str
    task: str
    round: int
    nonce: str
    brief: str
    checks: List[str]
    evaluation_url: str
    attachments: List[Attachment]

class StudentSubmission(BaseModel):
    email: str
    task: str
    round: int
    nonce: str
    repo_url: str
    commit_sha: str
    pages_url: str

class CheckResult(BaseModel):
    check: str
    passed: bool
    details: str

class EvaluationResult(BaseModel):
    status: str = "pending"
    submitted_at: Optional[datetime] = None
    submission_data: Optional[StudentSubmission] = None
    evaluation_completed_at: Optional[datetime] = None
    check_results: List[CheckResult] = []


# --- Test Case Data ---
# Hardcoded test cases from our design document.
TEST_CASES: Dict[str, Dict[int, Dict]] = {
    "sales-report": {
        1: {
            "task": "sales-report-a8b3d",
            "nonce": "nonce-1a2b-3c4d",
            "brief": "Create a single-page site that processes an attached CSV file named 'sales.csv'. Calculate the sum of the 'sales' column and display the total inside an HTML element with the id '#total-sales'. The page title must be 'Sales Summary'.",
            "checks": [
                "Page title is 'Sales Summary'",
                "Page contains an element with id '#total-sales'",
                "The text content of '#total-sales' is '650.75'"
            ],
            "attachments": [{"filename": "sales.csv", "content": "data:text/csv;base64,cHJvZHVjdCxzYWxlcwpBcHAyMCwxNTAuNTAKQmFuYW5hLDIwMC4yNQpDaGVycnksMzAw"}]
        },
        2: {
            "task": "sales-report-a8b3d",
            "nonce": "nonce-5e6f-7g8h",
            "brief": "Update the sales report. Add a table with the id '#sales-table' that displays each product and its corresponding sale amount from 'sales.csv'. The table should have a header row (Product, Sales). The '#total-sales' element must remain correct.",
            "checks": [
                "Page contains a table with id '#sales-table'",
                "Table has at least 3 data rows",
                "The text content of '#total-sales' remains '650.75'"
            ],
            "attachments": [{"filename": "sales.csv", "content": "data:text/csv;base64,cHJvZHVjdCxzYWxlcwpBcHAyMCwxNTAuNTAKQmFuYW5hLDIwMC4yNQpDaGVycnksMzAw"}]
        }
    },
    "github-user-info": {
        1: {
            "task": "github-user-info-c7e4f",
            "nonce": "nonce-i9j0-k1l2",
            "brief": "Create a page with an input field ('#username-input') and a button ('#fetch-btn'). When the button is clicked, fetch user data from 'https://api.github.com/users/{username}' and display the 'created_at' date in an element with id '#creation-date'.",
            "checks": [
                "Page has an input with id '#username-input' and a button with id '#fetch-btn'",
                "After entering 'octocat' and clicking the button, '#creation-date' contains '2011-01-25'"
            ],
            "attachments": []
        },
        2: {
            "task": "github-user-info-c7e4f",
            "nonce": "nonce-m3n4-o5p6",
            "brief": "Update the GitHub user page. Add a status element with id '#api-status'. It should display 'Loading...' during the API fetch. If the fetch is successful, it should be empty. If the fetch fails (e.g., for a non-existent user), it should display 'User not found'.",
            "checks": [
                "When fetching user 'octocat', '#api-status' shows 'Loading...' and then becomes empty.",
                "When fetching a user that does not exist like 'nonexistentuser123456789', '#api-status' displays 'User not found'."
            ],
            "attachments": []
        }
    },
    # ... Add other test cases here ...
}


# --- FastAPI Application ---
app = FastAPI(
    title="LLM Code Deployment Evaluator",
    description="An application to send tasks to a student's API and evaluate the submissions.",
)

allowed_origins = [origin.strip() for origin in SETTINGS["ALLOWED_ORIGINS"] if origin.strip()] or ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- UI HTML ---
def get_html_content():
    """Generates the HTML for the UI."""
    buttons_html = ""
    for test_id, rounds in TEST_CASES.items():
        buttons_html += f"<div><strong>{test_id.replace('-', ' ').title()}</strong>: "
        for round_id in rounds:
            buttons_html += f"<button onclick=\"runTest('{test_id}', {round_id})\">Round {round_id}</button>"
        buttons_html += "</div>"

    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Evaluator UI</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 0 auto; max-width: 1024px; padding: 20px; background-color: #f8f9fa; color: #212529; }}
            h1, h2 {{ color: #343a40; }}
            #controls div {{ margin-bottom: 10px; }}
            button {{ background-color: #007bff; color: white; border: none; padding: 8px 12px; border-radius: 5px; cursor: pointer; margin-left: 5px; }}
            button:hover {{ background-color: #0056b3; }}
            .container {{ background-color: white; border: 1px solid #dee2e6; border-radius: 5px; padding: 15px; margin-top: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }}
            pre {{ background: #e9ecef; padding: 10px; border-radius: 5px; white-space: pre-wrap; word-wrap: break-word; font-family: "SF Mono", "Fira Code", "Consolas", monospace; }}
        </style>
    </head>
    <body>
        <h1>LLM Code Deployment Evaluator</h1>
        <div id="controls" class="container">
            <h2>Run a Test</h2>
            {buttons_html}
        </div>
        <div class="container">
            <h2>Request Sent</h2>
            <pre id="request-display">Awaiting test start...</pre>
        </div>
        <div class="container">
            <h2>Evaluation Results</h2>
            <button id="eval-again-btn" onclick="evalAgain()" disabled>Eval Again</button>
            <pre id="results-display">Awaiting submission...</pre>
        </div>

        <script>
            let pollInterval;
            let currentTaskId = null;

            async function runTest(testCaseId, roundId) {{
                if (pollInterval) clearInterval(pollInterval);

                const requestDisplay = document.getElementById('request-display');
                const resultsDisplay = document.getElementById('results-display');
                const evalAgainButton = document.getElementById('eval-again-btn');
                requestDisplay.textContent = 'Sending request...';
                resultsDisplay.textContent = 'Awaiting submission...';
                evalAgainButton.disabled = true;
                currentTaskId = null;

                try {{
                    const response = await fetch(`/start-test/${{testCaseId}}/${{roundId}}`, {{ method: 'POST' }});
                    if (!response.ok) {{
                        const error = await response.json();
                        requestDisplay.textContent = `Error sending request: ${{JSON.stringify(error, null, 2)}}`;
                        return;
                    }}
                    const data = await response.json();
                    const taskId = data.task_id;
                    currentTaskId = taskId;
                    
                    pollResults(taskId);

                }} catch (error) {{
                    requestDisplay.textContent = `Failed to send request: ${{error}}`;
                }}
            }}

            async function pollResults(taskId) {{
                const requestDisplay = document.getElementById('request-display');
                const resultsDisplay = document.getElementById('results-display');
                const evalAgainButton = document.getElementById('eval-again-btn');

                pollInterval = setInterval(async () => {{
                    try {{
                        const response = await fetch(`/results/${{taskId}}`);
                        if (!response.ok) {{
                            resultsDisplay.textContent = `Error fetching results for task ${{taskId}}`;
                            clearInterval(pollInterval);
                            return;
                        }}
                        const resultData = await response.json();

                        requestDisplay.textContent = JSON.stringify(resultData.request, null, 2);
                        resultsDisplay.textContent = JSON.stringify(resultData.evaluation, null, 2);

                        const status = resultData.evaluation.status;
                        if (status === 'completed' || status.includes('failed')) {{
                            evalAgainButton.disabled = status !== 'completed';
                            clearInterval(pollInterval);
                        }} else {{
                            evalAgainButton.disabled = true;
                        }}
                    }} catch (error) {{
                        resultsDisplay.textContent = `Error polling for results: ${{error}}`;
                        clearInterval(pollInterval);
                    }}
                }}, 2000); // Poll every 2 seconds
            }}

            async function evalAgain() {{
                if (!currentTaskId) {{
                    return;
                }}
                if (pollInterval) clearInterval(pollInterval);

                const resultsDisplay = document.getElementById('results-display');
                const evalAgainButton = document.getElementById('eval-again-btn');
                resultsDisplay.textContent = 'Restarting evaluation...';
                evalAgainButton.disabled = true;

                try {{
                    const response = await fetch(`/re-evaluate/${{currentTaskId}}`, {{ method: 'POST' }});
                    if (!response.ok) {{
                        const error = await response.json();
                        resultsDisplay.textContent = `Error restarting evaluation: ${{JSON.stringify(error, null, 2)}}`;
                        return;
                    }}
                    pollResults(currentTaskId);
                }} catch (error) {{
                    resultsDisplay.textContent = `Failed to restart evaluation: ${{error}}`;
                }}
            }}
        </script>
    </body>
    </html>
    """

# --- Selenium Evaluation Logic ---
async def run_evaluation_checks(pages_url: str, checks: List[str], task_id: str):
    """
    Uses Selenium to run automated checks against a deployed URL.
    """
    results = []
    try:
        import subprocess
        import json
        import tempfile
        
        # Create a temporary file to store check data
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
            json.dump({
                "pages_url": pages_url,
                "checks": checks
            }, f)
            data_file = f.name
        
        # Run the selenium check in a separate process
        process = subprocess.run([
            sys.executable, 
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "selenium_runner.py"),
            data_file
        ], capture_output=True, text=True)
        
        if process.returncode == 0:
            try:
                results = [CheckResult(**item) for item in json.loads(process.stdout)]
            except json.JSONDecodeError:
                logging.error(f"Failed to parse Selenium results: {process.stdout}")
                results = [CheckResult(check=c, passed=False, details="Failed to parse results") for c in checks]
        else:
            error_msg = process.stderr or "Unknown error in Selenium subprocess"
            logging.error(f"Selenium runner failed: {error_msg}")
            results = [CheckResult(check=c, passed=False, details=f"Runner error: {error_msg}") for c in checks]
            
        # Clean up temp file
        try:
            os.unlink(data_file)
        except:
            pass
            
    except Exception as e:
        error_message = f"Major Selenium failure for task {task_id} at URL {pages_url}: {e}"
        logging.error(error_message)
        # Add a failure result for all checks if Selenium itself fails
        results = [CheckResult(check=c, passed=False, details=error_message) for c in checks]

    # Update DB with results
    if task_id in DB:
        DB[task_id]["evaluation"]["status"] = "completed"
        DB[task_id]["evaluation"]["evaluation_completed_at"] = datetime.now()
        DB[task_id]["evaluation"]["check_results"] = results
        logging.info(f"Evaluation completed for task {task_id}.")


# --- API Endpoints ---

@app.on_event("startup")
async def startup_event():
    """Check for required environment variables on startup."""
    # Fix for Windows + Python 3.13 Playwright issue
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    if not SETTINGS["STUDENT_API_ENDPOINT"] or not SETTINGS["SHARED_SECRET"]:
        raise ValueError(
            "FATAL: STUDENT_API_ENDPOINT and SHARED_SECRET environment variables must be set."
        )
    logging.info("Evaluator application started successfully.")
    logging.info(f"Student API Endpoint: {SETTINGS['STUDENT_API_ENDPOINT']}")
    logging.info(f"Own Evaluation URL: {SETTINGS['EVALUATION_URL']}")


@app.get("/", response_class=HTMLResponse)
async def get_ui_page():
    """Serves the main HTML UI page."""
    return get_html_content()


@app.post("/start-test/{test_case_id}/{round_id}")
async def start_test(test_case_id: str, round_id: int):
    """
    Starts a test by sending a task request to the student's API endpoint.
    """
    if test_case_id not in TEST_CASES or round_id not in TEST_CASES[test_case_id]:
        raise HTTPException(status_code=404, detail="Test case or round not found.")

    test_data = TEST_CASES[test_case_id][round_id]
    task_id = test_data["task"]

    request_payload = TaskRequest(
        email="student@example.com",
        secret=SETTINGS["SHARED_SECRET"],
        evaluation_url=SETTINGS["EVALUATION_URL"],
        round=round_id,
        **test_data
    )

    payload_dict = request_payload.model_dump()
    
    # Store the task in our DB before sending
    DB[task_id] = {
        "request": payload_dict,
        "sent_at": datetime.now(),
        "student_response_code": None,
        "student_response_body": None,
        "evaluation": EvaluationResult().model_dump()
    }

    logging.info(f"Sending task {task_id} (Round {round_id}) to {SETTINGS['STUDENT_API_ENDPOINT']}")
    logging.info(f"Payload: {payload_dict}")

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                SETTINGS["STUDENT_API_ENDPOINT"],
                json=payload_dict,
                timeout=15.0
            )
            DB[task_id]["student_response_code"] = response.status_code
            DB[task_id]["student_response_body"] = response.text
            
            if response.status_code != 200:
                logging.error(f"Student API returned non-200 status: {response.status_code}")
                logging.error(f"Response body: {response.text}")
                DB[task_id]["evaluation"]["status"] = "failed_to_send"
                return {"status": "error", "detail": f"Student API returned {response.status_code}", "response": response.text}
            
            logging.info(f"Successfully sent task {task_id}. Student API responded with {response.status_code}.")
            return {"status": "sent", "task_id": task_id}
        except httpx.RequestError as e:
            logging.error(f"Failed to send task {task_id} to student API: {e}")
            DB[task_id]["evaluation"]["status"] = "failed_to_send"
            raise HTTPException(status_code=500, detail=f"Could not connect to student API: {e}")


@app.post("/notify")
async def notify_endpoint(submission: StudentSubmission, background_tasks: BackgroundTasks):
    """
    This is the evaluation_url endpoint that the student's application will call.
    It receives the repo details and triggers the Playwright checks.
    """
    task_id = submission.task
    logging.info(f"Received submission for task: {task_id}")

    if task_id not in DB:
        logging.warning(f"Received submission for an unknown task ID: {task_id}")
        raise HTTPException(status_code=404, detail="Task ID not found.")
    
    # Basic validation
    original_request = DB[task_id]["request"]
    if (original_request["nonce"] != submission.nonce or 
        original_request["round"] != submission.round):
        logging.error(f"Nonce or round mismatch for task {task_id}.")
        raise HTTPException(status_code=400, detail="Nonce or round mismatch.")

    # Update DB with submission data
    eval_entry = DB[task_id]["evaluation"]
    eval_entry["status"] = "evaluating"
    eval_entry["submitted_at"] = datetime.now()
    eval_entry["submission_data"] = submission.model_dump()

    logging.info(f"Starting background evaluation for {task_id} at URL: {submission.pages_url}")

    # Run the heavy Playwright checks in the background
    background_tasks.add_task(
        run_evaluation_checks,
        pages_url=submission.pages_url,
        checks=original_request["checks"],
        task_id=task_id
    )

    return {"status": "accepted", "detail": "Evaluation has started."}


@app.post("/re-evaluate/{task_id}")
async def re_evaluate(task_id: str, background_tasks: BackgroundTasks):
    """Re-run the evaluation for a task using the last submitted deployment."""
    if task_id not in DB:
        raise HTTPException(status_code=404, detail="Task ID not found.")

    task_entry = DB[task_id]
    submission_data = task_entry["evaluation"].get("submission_data")
    if not submission_data:
        raise HTTPException(status_code=400, detail="No submission data available to evaluate.")

    pages_url = submission_data.get("pages_url")
    if not pages_url:
        raise HTTPException(status_code=400, detail="Submission does not include a pages_url.")

    logging.info(f"Re-running evaluation for task {task_id} at URL: {pages_url}")

    # Reset evaluation state and start a fresh run
    eval_entry = task_entry["evaluation"]
    eval_entry["status"] = "re-evaluating"
    eval_entry["evaluation_completed_at"] = None
    eval_entry["check_results"] = []
    eval_entry["submitted_at"] = datetime.now()

    background_tasks.add_task(
        run_evaluation_checks,
        pages_url=pages_url,
        checks=task_entry["request"]["checks"],
        task_id=task_id
    )

    return {"status": "accepted", "detail": "Re-evaluation started."}


@app.get("/results")
async def get_all_results():
    """
    A simple endpoint to view the current state of all tasks and their results.
    """
    return DB

@app.get("/results/{task_id}")
async def get_task_result(task_id: str):
    """
    Get the result for a specific task.
    """
    if task_id not in DB:
        raise HTTPException(status_code=404, detail="Task ID not found.")
    return DB[task_id]


if __name__ == "__main__":
    # Fix for Windows + Python 3.13 Playwright issue
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    # How to run this application:
    # 1. Install required packages:
    #    pip install fastapi uvicorn python-dotenv httpx "playwright"
    #    playwright install
    #
    # 2. Create a .env file in the same directory with these contents:
    #    STUDENT_API_ENDPOINT="http://<url_of_your_llm_app>/api-endpoint"
    #    SHARED_SECRET="your-chosen-secret"
    #    # EVALUATION_URL is set automatically if running locally on port 8002
    #
    # 3. Run the server:
    #    uvicorn evaluator_app:app --reload
    #
    # 4. Interact with it:
    #    - Open your browser and navigate to http://127.0.0.1:8002
    #    - Click a button to start a test.
    
    uvicorn.run(app, host="0.0.0.0", port=8002)