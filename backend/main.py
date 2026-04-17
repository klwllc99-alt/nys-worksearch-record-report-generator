from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path
from time import perf_counter
import re

from pypdf import PdfReader

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from openpyxl import Workbook
from pydantic import BaseModel

from admin_auth import AdminAuthStore
from metrics import MetricsStore
from parser import WorkSearchEntry, parse_work_search_file
from pdf_generator import ClaimantInfo, build_blank_form_pdf, create_output_document

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = BASE_DIR / "data"
CSV_TEMPLATE_PATH = STATIC_DIR / "ws5_template.csv"
ROOT_WS5_PATH = BASE_DIR.parent / "WS5.pdf"
BLANK_FORM_PATH = STATIC_DIR / "ws5_blank.pdf" if (STATIC_DIR / "ws5_blank.pdf").exists() else ROOT_WS5_PATH
APP_PAGE_PATH = STATIC_DIR / "index.html"
ADMIN_DASHBOARD_PATH = STATIC_DIR / "admin_dashboard.html"
LEGACY_ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "local-support-token").strip()
DEFAULT_ADMIN_EMAIL = os.getenv("DEFAULT_ADMIN_EMAIL", "klwllc99@gmail.com").strip().lower()
DEFAULT_ADMIN_PASSWORD = os.getenv("DEFAULT_ADMIN_PASSWORD", "99klwllc").strip()
ADMIN_USERS_FILE = DATA_DIR / "admin_users.json"
metrics_store = MetricsStore(max_events=int(os.getenv("METRICS_MAX_EVENTS", "2000")))
auth_store = AdminAuthStore(
    storage_path=ADMIN_USERS_FILE,
    default_email=DEFAULT_ADMIN_EMAIL,
    default_password=DEFAULT_ADMIN_PASSWORD,
)

DEFAULT_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

raw_origins = os.getenv("ALLOWED_ORIGINS", "")
allowed_origins = [origin.strip() for origin in raw_origins.split(",") if origin.strip()] or DEFAULT_ORIGINS

app = FastAPI(
    title="NYS Work Search Record PDF Generator",
    version="0.1.0",
    description="Generate WS5-compatible PDFs from CSV/XLSX uploads.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, str) else "Something went wrong."
    return JSONResponse(status_code=exc.status_code, content={"message": detail})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError):
    issues = []
    for error in exc.errors():
        field = error.get("loc", ["field"])[-1]
        issues.append(f"{field}: {error.get('msg', 'Invalid value')}")
    message = "Please review your input and try again."
    if issues:
        message = "Please fix the following: " + "; ".join(issues)
    return JSONResponse(status_code=422, content={"message": message})


@app.exception_handler(Exception)
async def generic_exception_handler(_: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"message": "An unexpected error occurred while generating your form. Please try again."})


class AdminLoginPayload(BaseModel):
    email: str
    password: str


class AdminCreateUserPayload(BaseModel):
    email: str
    password: str


class AdminChangePasswordPayload(BaseModel):
    email: str = ""
    new_password: str


def _extract_admin_token(request: Request) -> str | None:
    header_token = request.headers.get("x-admin-token", "").strip()
    if header_token:
        return header_token

    auth_header = request.headers.get("authorization", "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()

    query_token = request.query_params.get("token")
    return query_token.strip() if query_token else None


def require_admin_access(request: Request) -> dict[str, str]:
    token = _extract_admin_token(request)
    session = auth_store.verify_session(token)
    if session:
        return session

    if LEGACY_ADMIN_TOKEN and token == LEGACY_ADMIN_TOKEN:
        return {"email": DEFAULT_ADMIN_EMAIL}

    raise HTTPException(status_code=401, detail="Unauthorized.")


def entry_to_row(entry: WorkSearchEntry) -> dict[str, str]:
    return {
        "date": entry.date.isoformat(),
        "position_or_activity": entry.position_or_activity,
        "business_name": entry.business_name,
        "person_contacted": entry.person_contacted,
        "method_of_contact": entry.method_of_contact,
        "contact_information": entry.contact_information,
        "result": entry.result,
        "record_type": entry.record_type,
    }


def get_ws5_dropdown_metadata() -> dict[str, list[str]]:
    method_options = ["In person", "Phone", "Fax", "Email", "Web site"]
    result_options = ["Interview", "Waiting for response", "Not hired"]

    try:
        reader = PdfReader(str(BLANK_FORM_PATH))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)

        method_match = re.search(r"In person,\s*phone,\s*fax,\s*email,\s*web\s*site,\s*etc\.?", text, re.IGNORECASE)
        if method_match:
            extracted = re.sub(r"\betc\.?", "", method_match.group(0), flags=re.IGNORECASE)
            method_options = [item.strip().title() for item in extracted.split(",") if item.strip()]

        result_match = re.search(r"Interview,\s*waiting for\s*response,\s*not hired", text, re.IGNORECASE)
        if result_match:
            result_options = [item.strip().capitalize() for item in result_match.group(0).split(",") if item.strip()]
    except Exception:
        pass

    return {
        "method_options": method_options,
        "result_options": result_options,
    }


@app.middleware("http")
async def capture_request_metrics(request: Request, call_next):
    started_at = perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        duration_ms = (perf_counter() - started_at) * 1000
        metrics_store.record(request, status_code=500, duration_ms=duration_ms)
        raise

    duration_ms = (perf_counter() - started_at) * 1000
    metrics_store.record(request, status_code=response.status_code, duration_ms=duration_ms)
    response.headers["X-Process-Time-Ms"] = f"{duration_ms:.2f}"
    return response


@app.get("/")
def root():
    return FileResponse(APP_PAGE_PATH, media_type="text/html")


@app.get("/api")
def api_info() -> JSONResponse:
    return JSONResponse(
        {
            "name": "nys-worksearch-record-report-generator",
            "status": "ok",
            "docs": "/docs",
            "admin_dashboard": "/admin/dashboard",
            "default_admin_email": DEFAULT_ADMIN_EMAIL,
        }
    )


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/template/ws5-metadata")
def ws5_metadata():
    return JSONResponse(get_ws5_dropdown_metadata())


@app.post("/api/parse")
async def parse_upload(file: UploadFile = File(...)):
    filename = file.filename or "upload"
    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        grouped_records = parse_work_search_file(filename, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    rows = []
    for entries in grouped_records.values():
        rows.extend(entry_to_row(entry) for entry in entries)

    return JSONResponse({"rows": rows, "count": len(rows)})


@app.api_route("/admin", methods=["GET", "HEAD"])
def admin_root():
    return RedirectResponse(url="/admin/dashboard", status_code=307)


@app.get("/admin/dashboard")
def admin_dashboard():
    return FileResponse(ADMIN_DASHBOARD_PATH, media_type="text/html")


@app.post("/api/admin/login")
def admin_login(payload: AdminLoginPayload):
    token = auth_store.authenticate(payload.email, payload.password)
    if not token:
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    return JSONResponse(
        {
            "token": token,
            "email": payload.email.strip().lower(),
            "message": "Login successful.",
        }
    )


@app.get("/api/admin/session")
def admin_session(request: Request):
    session = require_admin_access(request)
    return JSONResponse({"email": session["email"]})


@app.get("/api/admin/users")
def admin_list_users(request: Request):
    require_admin_access(request)
    return JSONResponse({"users": auth_store.list_users()})


@app.post("/api/admin/users")
def admin_create_user(payload: AdminCreateUserPayload, request: Request):
    require_admin_access(request)
    try:
        created = auth_store.create_user(payload.email, payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({"message": "Admin user created.", "user": created})


@app.post("/api/admin/users/change-password")
def admin_change_password(payload: AdminChangePasswordPayload, request: Request):
    session = require_admin_access(request)
    target_email = payload.email.strip().lower() or session["email"]
    try:
        auth_store.change_password(target_email, payload.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({"message": f"Password updated for {target_email}."})


@app.get("/api/admin/metrics/summary")
def admin_metrics_summary(request: Request):
    require_admin_access(request)
    return JSONResponse(metrics_store.summary())


@app.get("/api/admin/metrics/requests")
def admin_metrics_requests(request: Request, limit: int = 100):
    require_admin_access(request)
    safe_limit = max(1, min(limit, 500))
    return JSONResponse({"requests": metrics_store.recent_requests(limit=safe_limit)})


@app.post("/api/generate")
async def generate(
    file: UploadFile = File(...),
    first_name: str = Form(""),
    last_name: str = Form(""),
    nys_id: str = Form(""),
    ssn_last4: str = Form(""),
    output_mode: str = Form("single"),
    generate_all: bool = Form(False),
):
    filename = file.filename or "upload"
    payload = await file.read()

    if not payload:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    normalized_mode = output_mode.strip().lower()
    if normalized_mode not in {"per_week", "single"}:
        raise HTTPException(status_code=400, detail="output_mode must be 'per_week' or 'single'.")

    normalized_first_name = first_name.strip()
    normalized_last_name = last_name.strip()
    normalized_nys_id = nys_id.strip().upper()
    cleaned_ssn = "".join(ch for ch in ssn_last4 if ch.isdigit())

    if normalized_first_name and len(normalized_first_name) < 2:
        raise HTTPException(status_code=400, detail="First name must be at least 2 characters if provided.")
    if normalized_last_name and len(normalized_last_name) < 2:
        raise HTTPException(status_code=400, detail="Last name must be at least 2 characters if provided.")
    if normalized_nys_id and (len(normalized_nys_id) > 20 or not normalized_nys_id.replace('-', '').isalnum()):
        raise HTTPException(status_code=400, detail="NYS ID must be letters and numbers only if provided.")
    if ssn_last4.strip() and len(cleaned_ssn) != 4:
        raise HTTPException(status_code=400, detail="SSN last 4 must contain exactly 4 digits if provided.")

    try:
        grouped_records = parse_work_search_file(filename, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    claimant = ClaimantInfo(
        first_name=normalized_first_name,
        last_name=normalized_last_name,
        nys_id=normalized_nys_id,
        ssn_last4=cleaned_ssn,
    )

    output_bytes, download_name, media_type = create_output_document(
        grouped_records=grouped_records,
        claimant=claimant,
        output_mode=normalized_mode,
        generate_all=generate_all,
        blank_form_path=BLANK_FORM_PATH,
    )

    headers = {"Content-Disposition": f'attachment; filename="{download_name}"'}
    return StreamingResponse(BytesIO(output_bytes), media_type=media_type, headers=headers)


@app.get("/api/template/csv")
def download_csv_template():
    if CSV_TEMPLATE_PATH.exists():
        return FileResponse(CSV_TEMPLATE_PATH, media_type="text/csv", filename="ws5_template.csv")

    fallback = (
        "Date,Position Applied / Activity,Business/Employer Name,Person Contacted / Title,Method of Contact,Contact Information,Result,Record Type\n"
        "2026-04-13,Customer Support Representative,Example Company,Jamie Recruiter,Website,https://example.com/careers,Submitted application,employer_contact\n"
        "2026-04-15,Attended workforce workshop,,,,Local Career Center,Completed workshop,other_activity\n"
    )
    headers = {"Content-Disposition": 'attachment; filename="ws5_template.csv"'}
    return StreamingResponse(BytesIO(fallback.encode("utf-8")), media_type="text/csv", headers=headers)


@app.get("/api/template/xlsx")
def download_xlsx_template():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "WorkSearch"

    headers = [
        "Date",
        "Position Applied / Activity",
        "Business/Employer Name",
        "Person Contacted / Title",
        "Method of Contact",
        "Contact Information",
        "Result",
        "Record Type",
    ]
    sample_row = [
        "2026-04-13",
        "Customer Support Representative",
        "Example Company",
        "Jamie Recruiter",
        "Website",
        "https://example.com/careers",
        "Submitted application",
        "employer_contact",
    ]

    sheet.append(headers)
    sheet.append(sample_row)
    for column_cells in sheet.columns:
        sheet.column_dimensions[column_cells[0].column_letter].width = 26

    output = BytesIO()
    workbook.save(output)
    output.seek(0)

    response_headers = {"Content-Disposition": 'attachment; filename="ws5_template.xlsx"'}
    media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return StreamingResponse(output, media_type=media_type, headers=response_headers)


@app.get("/api/template/ws5")
def download_blank_ws5_form():
    if BLANK_FORM_PATH.exists():
        return FileResponse(BLANK_FORM_PATH, media_type="application/pdf", filename="ws5_blank.pdf")

    output = BytesIO(build_blank_form_pdf())
    headers = {"Content-Disposition": 'attachment; filename="ws5_blank_placeholder.pdf"'}
    return StreamingResponse(output, media_type="application/pdf", headers=headers)
