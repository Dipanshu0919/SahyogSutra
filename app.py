import ast
from ntpath import splitdrive
import os
import json
import random
import datetime
import time
import threading
import zoneinfo
import httpx
import asyncio
import sqlitecloud as sq
import csv
import io
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from functools import wraps
from typing import Optional, Dict, Any

from fastapi import FastAPI, Request, Form, Depends, Response, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
import socketio
from dotenv import load_dotenv
from googletrans import Translator
from google import genai

# Import modules
from modules import sendlog, sendmail, sendmailthread, del_event, detailsformat
from modules import add_event as add_event_mod
from modules import delete_event as delete_event_mod
from modules import email_send_message

load_dotenv()

ist = zoneinfo.ZoneInfo("Asia/Kolkata")
translations_lock = threading.Lock()
active_events = 0
app_running_port = int(os.environ.get("PORT", 8000))
app_running_host = "0.0.0.0"
# hostsite = None
#
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")

client = genai.Client(api_key=GOOGLE_API_KEY)

all_translations = {}
non_file_translations = {}

# --- In-Memory Stores ---
rate_limit_store: dict[str, float] = {}  # {ip: timestamp}
_translation_executor = ThreadPoolExecutor(max_workers=10)

# --- Campaigns Cache ---
_campaigns_cache: dict = {"data": None, "ts": 0}
CAMPAIGNS_CACHE_TTL = 30  # seconds

# --- Leaderboard Cache ---
_leaderboard_cache: dict = {"data": None, "ts": 0}
LEADERBOARD_CACHE_TTL = 60  # seconds

# --- Helper Functions ---

def load_translations():
    global all_translations
    try:
        if os.path.exists("translations.json"):
            with open("translations.json", "r", encoding="utf-8") as f:
                all_translations = json.load(f)
                print("Translations loaded successfully.")
    except Exception as e:
        print(f"Translation file error: {e}")
        sendlog(f"Translation file error: {e}")

def save_translations():
    global all_translations
    try:
        tmp = "translations.json.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(all_translations, f, indent=4, ensure_ascii=False)
        os.replace(tmp, "translations.json")
        import shutil
        shutil.copy2("translations.json", "translations_backup.json")
    except Exception as e:
        print(f"Error saving translation file: {e}")
        sendlog(f"Error saving translation file: {e}")


def translation_file_thread():
    while True:
        time.sleep(60)
        with translations_lock:
            save_translations()


def translate_thread(text, lang, save_file):
    global all_translations
    global non_file_translations
    try:
        async def _translate():
            async with Translator() as t:
                return await t.translate(text, dest=lang)
        result = asyncio.run(_translate())
        translated = result.text
        print(f"Translated '{text}' to '{translated}' in language '{lang}'")
    except Exception as e:
        print(f"Translation error: {e}")
        translated = text

    with translations_lock:
        translate_dict = non_file_translations if not save_file else all_translations
        existing = translate_dict.get(text, {})
        existing[lang] = translated
        translate_dict[text] = existing


async def checkevent():
    while True:
        await asyncio.sleep(30 + random.randint(0, 10))
        try:
            async with httpx.AsyncClient() as client:
                await client.get(f"http://{app_running_host}:{app_running_port}/checkeventloop")
        except Exception as e:
            print(f"Check event loop error: {e}")
            await asyncio.sleep(60)

# --- Rate Limiter Helper ---
def check_rate_limit(ip: str, window: int = 30) -> tuple[bool, int]:
    """
    Returns (is_allowed, wait_seconds).
    Also prunes stale entries to prevent memory leak.
    """
    now = time.time()
    # Prune entries older than 2x the window
    expired = [k for k, v in rate_limit_store.items() if now - v > window * 2]
    for k in expired:
        del rate_limit_store[k]

    if ip in rate_limit_store:
        elapsed = now - rate_limit_store[ip]
        if elapsed < window:
            return False, int(window - elapsed)

    rate_limit_store[ip] = now
    return True, 0

def _prune_rate_limit_store():
    while True:
        time.sleep(300)  # every 5 minutes
        now = time.time()
        expired = [k for k, v in list(rate_limit_store.items()) if now - v > 120]
        for k in expired:
            del rate_limit_store[k]

# --- FastAPI Setup ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    load_translations()
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _init_pool)   # fill queue with exactly _DB_POOL_MAX connections
    threading.Thread(target=translation_file_thread, name="TranslationFileThread", daemon=True).start()
    threading.Thread(target=_prune_rate_limit_store, daemon=True, name="RateLimitPruner").start()
    task = asyncio.create_task(checkevent())
    print("Starting background check also")
    yield
    # Shutdown — drain the queue and close every connection
    task.cancel()
    _translation_executor.shutdown(wait=False)
    while not _db_idle_queue.empty():
        try:
            db = _db_idle_queue.get_nowait()
            _pool_close_one(db)
        except Exception:
            pass

app = FastAPI(lifespan=lifespan)

# Session Middleware
app.add_middleware(SessionMiddleware, secret_key=os.environ.get("FLASK_SECRET", "supersecretkey"))

# SocketIO Setup — single mount only
sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# --- Database Connection Pool (Queue-based, strict max 10) ---
# --- Database Connection Pool (Queue + auto-refill, strict max) ---
#
# Design:
#   _db_idle_queue  — unbounded Queue holding idle connections ready to use
#   _db_open_count  — atomic counter of ALL open connections (idle + in-use)
#   _db_count_lock  — protects _db_open_count for check-then-increment
#
# Acquire logic:
#   1. Try to grab an idle connection instantly (non-blocking get).
#   2. If none idle AND count < MAX → open a brand-new one right now (fast path).
#   3. If none idle AND count == MAX → block-wait on the queue (all slots busy).
#   After every acquire a background thread tries to pre-open one replacement
#   so the queue is always topped up and the NEXT caller gets an instant hit.
#
# This guarantees:
#   • Total open connections NEVER exceeds _DB_POOL_MAX (hard cap via lock).
#   • database=NULL never appears (USE DATABASE called on every new connection).
#   • No fallback that bypasses the cap; no racing refill threads.

import queue as _queue_mod

_DB_POOL_MAX  = int(os.environ.get("DB_POOL_MAX", "10"))
_DB_POOL_INIT = min(3, _DB_POOL_MAX)   # open 3 eagerly at startup, grow on demand

_db_idle_queue: _queue_mod.Queue = _queue_mod.Queue()   # idle connections (no maxsize)
_db_open_count: int  = 0                                 # total open (idle + in-use)
_db_count_lock: threading.Lock = threading.Lock()        # guards _db_open_count

def _open_connection():
    """Open and configure one SQLiteCloud connection, explicitly selecting the database."""
    db = sq.connect(os.environ.get("SQLITECLOUD"))
    db.row_factory = sq.Row
    # Explicitly USE DATABASE so it never shows as NULL in LIST CONNECTIONS
    try:
        conn_str = os.environ.get("SQLITECLOUD", "")
        db_name = conn_str.split("/")[-1].split("?")[0].strip()
        if db_name:
            db.execute(f"USE DATABASE {db_name}")
    except Exception:
        pass
    return db

def _try_open_and_enqueue() -> bool:
    """
    Open one new connection and put it in the idle queue — only if under MAX.
    Thread-safe. Returns True if a connection was actually opened.
    """
    global _db_open_count
    with _db_count_lock:
        if _db_open_count >= _DB_POOL_MAX:
            return False          # already at cap, nothing to do
        _db_open_count += 1       # reserve the slot before releasing the lock

    try:
        conn = _open_connection()
        _db_idle_queue.put(conn)
        return True
    except Exception as e:
        with _db_count_lock:
            _db_open_count -= 1   # give the slot back on failure
        print(f"Pool refill error: {e}")
        return False

def _init_pool():
    """Open _DB_POOL_INIT connections eagerly; the rest grow on demand."""
    for _ in range(_DB_POOL_INIT):
        _try_open_and_enqueue()
    print(f"DB pool ready: {_db_open_count}/{_DB_POOL_MAX} connections (auto-refill active)")

def _pool_acquire(timeout: int = 30) -> tuple:
    """
    Borrow one connection from the pool.
      • Instant if an idle connection is available.
      • Opens a fresh one if under MAX (still fast — no wait).
      • Blocks up to `timeout` seconds only if all MAX slots are in use.
    After handing off the connection, spawns a background thread to
    pre-open a replacement so the next caller also gets an instant hit.
    """
    global _db_open_count

    # Fast path 1: grab an already-idle connection (non-blocking)
    try:
        db = _db_idle_queue.get_nowait()
    except _queue_mod.Empty:
        db = None

    if db is None:
        # Fast path 2: no idle connections, but we can open a new one right now
        with _db_count_lock:
            if _db_open_count < _DB_POOL_MAX:
                _db_open_count += 1
                can_open = True
            else:
                can_open = False

        if can_open:
            try:
                db = _open_connection()
            except Exception:
                with _db_count_lock:
                    _db_open_count -= 1
                raise
        else:
            # Slow path: every slot is taken — block until one is returned
            try:
                db = _db_idle_queue.get(block=True, timeout=timeout)
            except _queue_mod.Empty:
                raise RuntimeError(
                    f"DB pool exhausted — all {_DB_POOL_MAX} connections busy for "
                    f"{timeout}s. Raise DB_POOL_MAX or check for slow queries."
                )

    # Proactively open a replacement in the background so the queue stays full
    # for the next caller — this is the "auto-refill" step.
    threading.Thread(target=_try_open_and_enqueue, daemon=True, name="DBPoolRefill").start()

    return db, db.cursor()

def _pool_release(db):
    """Return a connection to the idle queue (always — the count never changes on release)."""
    try:
        db.commit()
    except Exception:
        pass
    _db_idle_queue.put(db)

def _pool_close_one(db):
    """Close a connection and decrement the open count (used by admin/shutdown)."""
    global _db_open_count
    try:
        db.close()
    except Exception:
        pass
    with _db_count_lock:
        _db_open_count = max(0, _db_open_count - 1)

# Keep sqldb decorator working for any legacy @sqldb-decorated helpers
def sqldb(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        db, c = _pool_acquire()
        try:
            final = function(c, *args, **kwargs)
            db.commit()
            return final
        finally:
            _pool_release(db)
    return wrapper

async def run_query(query: str, params: tuple = (), fetchmode: str = "all"):
    """Run a single query asynchronously from the pool."""
    loop = asyncio.get_event_loop()

    def _execute():
        db, c = _pool_acquire()
        try:
            c.execute(query, params)
            if fetchmode == "all":
                result = c.fetchall()
            elif fetchmode == "one":
                result = c.fetchone()
            else:
                result = None
            db.commit()
            return result
        finally:
            _pool_release(db)

    return await loop.run_in_executor(None, _execute)

async def run_queries_parallel(*queries):
    """Run multiple (query, params, fetchmode) tuples in parallel."""
    tasks = [run_query(q, p, f) for q, p, f in queries]
    return await asyncio.gather(*tasks)

# --- Synchronous DB for non-async contexts (SocketIO, background tasks) ---
def sync_db():
    db, c = _pool_acquire()
    return db, c

def close_db(db):
    _pool_release(db)

# --- FastAPI DB Dependency (async-safe) ---
class AsyncDB:
    """Async-compatible DB wrapper — borrows from pool, returns on close."""
    def __init__(self, db, cursor):
        self._db = db
        self._c = cursor
        self._loop = asyncio.get_event_loop()

    def _run(self, fn):
        return self._loop.run_in_executor(None, fn)

    async def execute(self, query, params=()):
        def _do():
            self._c.execute(query, params)
            return self._c
        await self._run(_do)
        return self

    async def fetchone(self):
        def _do():
            return self._c.fetchone()
        return await self._run(_do)

    async def fetchall(self):
        def _do():
            return self._c.fetchall()
        return await self._run(_do)

    async def commit(self):
        def _do():
            self._db.commit()
        await self._run(_do)

    def close(self):
        _pool_release(self._db)

async def get_db():
    loop = asyncio.get_event_loop()
    db, c = await loop.run_in_executor(None, _pool_acquire)
    adb = AsyncDB(db, c)
    try:
        yield adb
        await adb.commit()
    finally:
        adb.close()

# --- Template Filters & Globals ---

def datetimeformat(value):
    if isinstance(value, str):
        try:
            return datetime.datetime.strptime(value, "%Y-%m-%d").strftime("%d %B %Y")
        except Exception:
            return value
    return value

templates.env.filters["datetimeformat"] = datetimeformat

def translate_text(text, lang=None, save_file=True):
    global all_translations
    global non_file_translations
    text = text.replace("\n", "")
    text = " ".join(text.split())
    if not lang or lang == "en":
        return text
    combined_translations = {**all_translations, **non_file_translations}
    if not combined_translations.get(text) or not combined_translations.get(text).get(lang):
        # Use thread pool instead of spawning raw threads
        _translation_executor.submit(translate_thread, text, lang, save_file)
    translationss = all_translations if save_file else non_file_translations
    return translationss.get(text, {}).get(lang, text)

@app.post("/translate_event")
async def translate_event(request: Request):
    data = await request.json()
    lang = request.session.get("lang", "en")
    output = {}
    threads = []

    def transl(text, lang, key):
        async def _do():
            async with Translator() as t:
                return await t.translate(text, dest=lang)
        result = asyncio.run(_do())
        output[key] = result.text

    for field, value in data.items():
        threads.append(threading.Thread(target=transl, args=(value, lang, field)))

    for x in threads:
        x.start()
    for x in threads:
        x.join()

    return JSONResponse(content=output)

# --- Exception Handlers ---

@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request, exc):
    return templates.TemplateResponse("error.html", {
        "request": request,
        "status_code": exc.status_code,
        "detail": exc.detail
    }, status_code=exc.status_code)

@app.exception_handler(500)
async def internal_exception_handler(request, exc):
    return templates.TemplateResponse("error.html", {
        "request": request,
        "status_code": 500,
        "detail": "Internal Server Error"
    }, status_code=500)

# --- Routes ---

@app.get("/")
async def home(request: Request, preview: bool = False, db: AsyncDB = Depends(get_db)):
    # global hostsite
    # if not hostsite:
    #     hostsite = request.base_url
    session = request.session
    print(session)
    currentuser = session.get("name", "User")
    currentuname = session.get("username")

    if not session.get("lang") and not preview:
        return templates.TemplateResponse(request, "selectlanguage.html")

    global active_events

    isadmin = False
    userdetails = {}
    top_organizers = []
    admin_stats = {}

    if currentuname:
        # Use session data — no DB hit needed for basic user info
        isadmin = session.get("role") == "admin"
        userdetails = {
            "username": currentuname,
            "name": session.get("name", ""),
            "email": session.get("email", ""),
            "role": session.get("role", "user"),
            "events": session.get("events", None),
        }
        if isadmin:
            # Admin stats still need DB — but only for admins
            results = await run_queries_parallel(
                ("SELECT COUNT(*) as count FROM userdetails", (), "one"),
                ("SELECT COUNT(*) as count FROM eventreq", (), "one"),
                ("SELECT COUNT(*) as count FROM eventdetail", (), "one"),
            )
            admin_stats = {
                "total_users": results[0]["count"],
                "pending_requests": results[1]["count"],
                "active_threads": threading.active_count(),
                "total_events": results[2]["count"]
            }

    # Leaderboard is now fetched client-side via /api/leaderboard for speed
    top_organizers = []  # populated by JS after page load

    template_name = session.get("template", "index.html")
    user_lang = session.get("lang", "en")

    def bound_translate(text, save_file=True):
        return translate_text(text.strip(), lang=user_lang, save_file=save_file)

    return templates.TemplateResponse(request, template_name, {
        "active_events_length": active_events,
        "fullname": currentuser,
        "c_user": str(currentuname).strip(),
        "isadmin": bool(isadmin),
        "userdetails": userdetails,
        "translate": bound_translate,
        "user_language": user_lang,
        "fvalues": {},
        "top_organizers": top_organizers,
        "admin_stats": admin_stats
    })

@app.get("/event/{eventid}")
async def eventfromeventid(request: Request, eventid: int, db: AsyncDB = Depends(get_db)):
    session = request.session
    await db.execute("SELECT * FROM eventdetail WHERE eventid=(?)", (eventid, ))
    getevent = await db.fetchone()
    currentuname = session.get("username")
    user_lang = session.get("lang", "en")
    isadmin = session.get("role") == "admin"
    ud = {
        "username": currentuname or "",
        "name": session.get("name", ""),
        "email": session.get("email", ""),
        "role": session.get("role", "user"),
        "events": session.get("events", ""),
    } if currentuname else {}

    def bound_translate(text, save_file=True):
        return translate_text(text.strip(), lang=user_lang, save_file=save_file)

    return templates.TemplateResponse(request, "viewevent.html", {
        "isadmin": bool(isadmin),
        "c_user": str(currentuname).strip(),
        "eventdetails": getevent,
        "translate": bound_translate,
        "user_language": user_lang,
        "userdetails": ud
    })

@app.post("/forgetpassword")
async def forgetpassword(request: Request, db: AsyncDB = Depends(get_db)):
    pass
    formdata = await request.form()
    otp = str(request.session.get("forgetotp"))
    formotp = str(formdata.get("forgetotp"))
    formemail = formdata.get("forgetemail")
    formpassword = formdata.get("newpassword")
    cpassword = formdata.get("confirmnewpassword")

    splited = otp.split("_")

    await db.execute("SELECT email FROM userdetails WHERE email=(?) OR username=(?)", (formemail,formemail))
    email = await db.fetchone()
    email = email["email"]

    if (splited[0] != formotp) or (splited[1] != email):
        return Response(content="Wrong OTP!", media_type="text/plain")

    if formpassword != cpassword:
        return Response(content="Wrong Confirm Password!", media_type="text/plain")

    await db.execute("UPDATE userdetails SET password=(?) WHERE email=(?)", (cpassword, email))
    request.session.pop("forgetotp")
    return Response(content="Password Change Success!", media_type="text/plain")


@app.post("/sendforgetotp")
async def sendforgetotp(request: Request, email: str = Form(...), db: AsyncDB = Depends(get_db)):
    client_ip = request.client.host
    allowed, wait = check_rate_limit(client_ip, window=60)
    if not allowed:
        return Response(
            content=f"Please wait {wait} seconds before requesting another OTP.",
            media_type="text/plain",
            status_code=429
        )

    getemail = await db.execute("SELECT email FROM userdetails WHERE email=(?) OR username=(?)", (email,email))
    getemail = await db.fetchone()

    if not getemail:
        return Response(content="Email/Username doesnt exists! Please try different email.", media_type="text/plain")

    email = getemail["email"]
    otp = random.randint(1111,9999)
    request.session["forgetotp"] = f"{otp}_{email}"
    sendmailthread(email, "Reset Password OTP For Sahyog Sutra", f"Use this OTP to reset your password in the Sahyog Setu!\n\nOTP: {otp}")
    return Response(content=f"OTP Sent to {email}! Please check spam folder if can't find it.", media_type="text/plain")


@app.post("/sendsignupotp")
async def sendotp(request: Request, email: str = Form(...), db: AsyncDB = Depends(get_db)):
    client_ip = request.client.host
    allowed, wait = check_rate_limit(client_ip, window=60)
    if not allowed:
        return Response(
            content=f"Please wait {wait} seconds before requesting another OTP.",
            media_type="text/plain",
            status_code=429
        )

    await db.execute("SELECT * FROM userdetails WHERE email=?", (email,))
    checkexists = await db.fetchone()
    if checkexists:
        return Response(content="Email already exists! Please try different email.", media_type="text/plain")

    otp = random.randint(1111, 9999)
    request.session["signupotp"] = f"{otp}_{email}"

    sendmailthread(email, "Signup OTP For Sahyog Sutra", email_send_message(otp), type="html")
    return Response(content=f"OTP Sent to {email}! Please check spam folder if can't find it.", media_type="text/plain")

@app.post("/setlanguage/{lang}")
async def setlanguage(request: Request, lang: str):
    request.session["lang"] = lang
    return Response(content="Language Set", media_type="text/plain")


# @app.post("/generate_ai_description")
# async def generate_ai_description(request: Request):
#     client_ip = request.client.host
#     allowed, wait = check_rate_limit(client_ip, window=60)
#     if not allowed:
#         return Response(content="Please wait a moment before generating again.", media_type="text/plain", status_code=429)

#     try:
#         form_data = await request.form()
#         field = ["eventname", "starttime", "endtime", "eventstartdate", "enddate", "location", "category"]
#         values = [[x, form_data.get(x)] for x in field if form_data.get(x)]

#         content = f"""Generate a description based on following details in pure english language.
#         Context:
#         Details of event: {values}
#         Generate total 4x descriptions (max 500 words each). Include hashtags. Reply strictly in JSON:
#         {{"desc1": "Formal tone", "desc2": "Informal tone", "desc3": "Promotional tone", "desc4": "Entertaining/Fun tone"}}"""

#         # Use httpx async client instead of blocking requests
#         async with httpx.AsyncClient(timeout=30.0) as client:
#             response = await client.post(
#                 url="https://openrouter.ai/api/v1/chat/completions",
#                 headers={
#                     "Authorization": f"Bearer {os.environ.get('OPENROUTER_API_KEY')}",
#                     "Content-Type": "application/json",
#                 },
#                 json={
#                     "model": "nvidia/nemotron-nano-9b-v2:free",
#                     "messages": [{"role": "user", "content": content}]
#                 }
#             )

#         data = response.json()
#         output = data["choices"][0]["message"]["content"]

#         # Clean up markdown code fences if present
#         if "```json" in output:
#             output = output.replace("```json", "").replace("```", "")

#         to_json = json.loads(output.strip())
#         return JSONResponse(content=to_json)

#     except Exception as e:
#         print(f"AI Description Generation Error: {e}")
#         return Response(content="Error generating description. Please try again later.", media_type="text/plain", status_code=500)
#
@app.post("/generate_ai_description")
async def generate_ai_description(request: Request):
    client_ip = request.client.host
    allowed, wait = check_rate_limit(client_ip, window=60)
    if not allowed:
        return JSONResponse(content={"wait": wait}, status_code=429)

    try:
        form_data = await request.form()
        field = ["eventname", "starttime", "endtime", "eventstartdate", "enddate", "location", "category"]
        values = [[x, form_data.get(x)] for x in field if form_data.get(x)]

        content = f"""Generate a description based on following details in pure english language.
        Context:
        Details of event: {values}
        Generate total 4x descriptions (max 500 words each). Include hashtags. Reply strictly in JSON:
        {{"desc1": "Formal tone", "desc2": "Informal tone", "desc3": "Promotional tone", "desc4": "Entertaining/Fun tone"}}"""

        response = client.models.generate_content(
            model="gemini-3-flash-preview", contents=content
        )

        output = response.text

        # Clean up markdown code fences if present
        if "```json" in output:
            output = output.replace("```json", "").replace("```", "")

        to_json = json.loads(output.strip())
        return JSONResponse(content=to_json)

    except Exception as e:
        print(f"AI Description Generation Error: {e}")
        return Response(content="Error generating description. Please try again later.", media_type="text/plain", status_code=500)


@app.get("/group-chat/from-event/{eventid}")
async def group_chat_from_event(request: Request, eventid: int, db: AsyncDB = Depends(get_db)):
    currentuname = request.session.get("username", "anonymous")

    await db.execute("SELECT * FROM eventdetail WHERE eventid=?", (eventid,))
    eventdetail = await db.fetchone()
    if not eventdetail:
        return Response(content="No such event found.", media_type="text/plain")

    await db.execute("SELECT * FROM messages2 WHERE eventid=?", (eventid,))
    all_msgs_row = await db.fetchone()
    all_msgs_str = all_msgs_row["msgs"] if all_msgs_row else None

    if all_msgs_str:
        all_msgs = ast.literal_eval(all_msgs_str)
    else:
        all_msgs = []

    messages = [(x[0], x[1], x[2]) for x in all_msgs] if all_msgs else []

    return templates.TemplateResponse(request, "groupchat.html", {
        "messages": messages,
        "eventid": eventid,
        "currentuname": currentuname,
        "eventname": eventdetail["eventname"]
    })

@app.get("/user/{username}")
async def user_profile(request: Request, username: str, db: AsyncDB = Depends(get_db)):
    await db.execute("SELECT * FROM userdetails WHERE username=?", (username,))
    userfulldetails = await db.fetchone()
    if not userfulldetails:
        raise HTTPException(status_code=404, detail="User not found")

    user_lang = request.session.get("lang", "en")

    def bound_translate(text, save_file=True):
        return translate_text(text.strip(), lang=user_lang, save_file=save_file)

    current_user = request.session.get("username")
    is_own_profile = (current_user == username)
    if is_own_profile:
        request.session["role"] = str(userfulldetails["role"]) or "user"
        try:
            events = userfulldetails["events"]
            events = events.split(",")
            request.session["events"] = len(events)
        except: request.session["events"] = None

    return templates.TemplateResponse(request, "userprofile.html", {
        "userdetails": dict(userfulldetails),
        "translate": bound_translate,
        "is_own_profile": is_own_profile
    })

@app.get("/changetemplate")
async def changetemplate(request: Request):
    ct = request.session.get("template", "index.html")
    request.session["template"] = "index2.html" if ct == "index.html" else "index.html"
    return Response(content="Template Changed", media_type="text/plain")

@app.get("/show_add_form")
async def show_add_form(request: Request):
    fi = ["eventname", "email", "starttime", "endtime", "eventstartdate", "enddate", "location", "category", "description"]
    fv = {x: request.session.get(x, "") for x in fi}

    user_lang = request.session.get("lang", "en")
    def bound_translate(text, save_file=True):
        return translate_text(text.strip(), lang=user_lang, save_file=save_file)

    categories = {}
    with open("events.json", "r") as f:
        categories = json.load(f)

    return templates.TemplateResponse(request, "addevent.html", {
        "fvalues": fv,
        "translate": bound_translate,
        "categories": categories
    })

@app.get("/show_campaigns")
async def show_campaigns(request: Request, db: AsyncDB = Depends(get_db)):
    global _campaigns_cache, active_events
    currentuname = request.session.get("username")
    user_lang = request.session.get("lang", "en")

    # Serve from cache if fresh
    if _campaigns_cache["data"] and time.time() - _campaigns_cache["ts"] < CAMPAIGNS_CACHE_TTL:
        cached = _campaigns_cache["data"]
        edetailslist = cached["edetailslist"]
        trending_events = cached["trending_events"]
        allevents = cached["allevents"]
        alleventscat = cached["alleventscat"]
        active_events = cached["active_events"]
    else:
        await db.execute("SELECT * FROM eventdetail")
        edetailslist = [dict(row) for row in await db.fetchall()]

        trending_events = sorted(edetailslist, key=lambda x: x['likes'], reverse=True)[:4]

        alleventscat = list({x["category"] for x in edetailslist})
        allevents = {}
        for x in edetailslist:
            allevents.setdefault(x["category"], []).append(x)

        active_events = sum(len(v) for v in allevents.values())

        _campaigns_cache = {
            "data": {
                "edetailslist": edetailslist,
                "trending_events": trending_events,
                "allevents": allevents,
                "alleventscat": alleventscat,
                "active_events": active_events,
            },
            "ts": time.time()
        }

    isadmin = request.session.get("role") == "admin"
    userdetails = {
        "username": currentuname or "",
        "name": request.session.get("name", ""),
        "email": request.session.get("email", ""),
        "role": request.session.get("role", "user"),
        "events": request.session.get("events", None),
    } if currentuname else {}

    viewuserevent = request.session.pop("vieweventusername", str(currentuname))
    ve = request.session.pop("viewyourevents", False)
    if viewuserevent == currentuname:
        await db.execute("SELECT events, role FROM userdetails WHERE username=?", (currentuname,))
        fet = await db.fetchone()
        request.session["role"] = str(fet["role"]) or "user"
        try:
            spl = fet["events"].split(",")
            request.session["events"] = len(spl)
        except:
            request.session["events"] = None

    sortby = request.session.get("sortby", "eventstartdate")

    def bound_translate(text, save_file=True):
        return translate_text(text.strip(), lang=user_lang, save_file=save_file)

    return templates.TemplateResponse(request, "campaigns.html", {
        "allevents": allevents,
        "userdetails": userdetails,
        "viewyourevents": ve,
        "sortby": sortby,
        "isadmin": bool(isadmin),
        "c_user": str(currentuname).strip(),
        "viewuserevent": viewuserevent,
        "translate": bound_translate,
        "trending_events": trending_events,
        "user_language": user_lang
    })

@app.post("/viewyourevents/{username}")
async def viewyourevents(request: Request, username: str):
    request.session["viewyourevents"] = True
    request.session["vieweventusername"] = username
    return Response(content="OK", media_type="text/plain")

@app.post("/setsortby/{sortby}")
async def setsortby(request: Request, sortby: str):
    request.session["sortby"] = sortby
    return Response(content="Sort by set", media_type="text/plain")

@app.post("/signup")
async def signup(request: Request, db: AsyncDB = Depends(get_db)):
    form_data = await request.form()
    username = form_data.get("username").lower()
    password = form_data.get("password")
    cpassword = form_data.get("cpassword")
    name = form_data.get("nameofuser")
    email = form_data.get("email")
    otp = form_data.get("signupotp")
    session_otp = str(request.session.get("signupotp"))

    # Run both existence checks in parallel
    results = await run_queries_parallel(
        ("SELECT username FROM userdetails WHERE username=?", (username,), "one"),
        ("SELECT email FROM userdetails WHERE email=?", (email,), "one"),
    )
    if results[0]:
        return Response(content="Username Already Exists", media_type="text/plain")
    if results[1]:
        return Response(content="Email Already Exists", media_type="text/plain")

    if (session_otp.split("_")[0] != str(otp).strip()):
        return Response(content="Wrong Signup OTP", media_type="text/plain")
    elif (session_otp.split("_")[1] != email):
        return Response(content="Email Address should match the email in which OTP is sent.", media_type="text/plain")
    elif password != cpassword:
        return Response(content="Wrong Confirm Password", media_type="text/plain")
    elif len(password) < 8:
        return Response(content="Password must be at least 8 characters long", media_type="text/plain")
    else:
        await db.execute(
            "INSERT INTO userdetails(username, password, name, email) VALUES(?, ?, ?, ?)",
            (username, password, name, email)
        )
        request.session["username"] = username
        request.session["name"] = name
        request.session["email"] = email
        request.session["role"] = "user"
        request.session["events"] = None
        request.session.pop("signupotp", None)
        sendlog(f"New Signup: {name} ({username})")
        return Response(content="Signup Success ✅", media_type="text/plain")

@app.post("/login")
async def login(request: Request, db: AsyncDB = Depends(get_db)):
    form_data = await request.form()
    username = form_data.get("loginusername").lower()
    password = form_data.get("loginpassword")

    await db.execute(
        "SELECT * FROM userdetails WHERE username=? OR email=?",
        (username, username)
    )
    fetched = await db.fetchone()
    if not fetched:
        return Response(content="No username found", media_type="text/plain")
    elif password != fetched["password"]:
        return Response(content="Wrong Password", media_type="text/plain")
    else:
        request.session["username"] = fetched["username"]
        request.session["name"] = fetched["name"]
        request.session["email"] = fetched["email"]
        request.session["role"] = fetched["role"] or "user"
        request.session["events"] = fetched["events"] or None
        sendlog(f"User Login: {fetched['name']} ({fetched['username']})")
        return Response(content="Login Success ✅", media_type="text/plain")

@app.post("/addevent")
async def addnewevent(request: Request, db: AsyncDB = Depends(get_db)):
    form_data = await request.form()
    session_username = request.session.get("username")
    target_username = session_username

    if session_username and request.session.get("role") == "admin":
        if form_data.get("username"):
            target_username = form_data.get("username")
            try:
                await db.execute(
                    "DELETE FROM eventreq WHERE eventname=? AND username=?",
                    (form_data.get("eventname"), target_username)
                )
            except Exception as e:
                print(f"Error cleaning up eventreq: {e}")

    # Module still uses sync cursor — wrap in executor
    loop = asyncio.get_event_loop()
    res = await loop.run_in_executor(
        None,
        lambda: add_event_mod.addevent(db._c, dict(form_data), target_username)
    )
    return Response(content=res, media_type="text/plain")

@app.post("/addeventreq")
async def addeventreq(request: Request, db: AsyncDB = Depends(get_db)):
    form_data = await request.form()
    loop = asyncio.get_event_loop()
    res = await loop.run_in_executor(
        None,
        lambda: add_event_mod.addeventrequest(db._c, dict(form_data), request.session)
    )
    return Response(content=res, media_type="text/plain")

@app.get("/show_pending_events")
async def pendingevents(request: Request, db: AsyncDB = Depends(get_db)):
    uname = request.session.get("username")
    if not uname:
        return Response(content="Login First", media_type="text/plain")

    if request.session.get("role") != "admin":
        return RedirectResponse(url="/", status_code=303)
    await db.execute("SELECT * FROM eventreq")
    pe = [dict(row) for row in await db.fetchall()]

    categories = {}
    with open("events.json", "r") as f:
        categories = json.load(f)

    return templates.TemplateResponse(request, "pendingevents.html", {"pendingevents": pe, "categories": categories})

@app.get("/deleteevent/{eventid}")
async def deleteevent(request: Request, eventid: int, db: AsyncDB = Depends(get_db)):
    loop = asyncio.get_event_loop()
    res = await loop.run_in_executor(
        None,
        lambda: delete_event_mod.delete_eventfromid(db._c, eventid, request.session)
    )
    # Invalidate campaigns cache on delete
    _campaigns_cache["ts"] = 0
    if res == "REDIRECT_HOME":
        return RedirectResponse(url="/", status_code=303)
    return Response(content=res, media_type="text/plain")

@app.get("/logout")
async def logout(request: Request):
    u = request.session.pop('username', None)
    n = request.session.pop('name', None)
    e = request.session.pop('email', None)
    request.session.pop('role', None)
    request.session.pop('events', None)
    sendlog(f"User Logout: {n} ({u}) {e}")
    return RedirectResponse(url="/", status_code=303)

@app.post("/save_draft")
async def save_draft(request: Request):
    form_data = await request.form()
    field = form_data.get("field")
    value = form_data.get("value")
    if value and value.strip():
        request.session[field] = value.strip()
    return Response(content="DRAFT", media_type="text/plain")

@app.get("/decline_event/{eventid}/{reason}")
async def decline_event(request: Request, eventid: int, reason: str, db: AsyncDB = Depends(get_db)):
    u = request.session.get("username")
    if u and request.session.get("role") == "admin":
        if True:
            await db.execute("SELECT * FROM eventreq WHERE eventid=?", (eventid,))
            email_row = await db.fetchone()

            await db.execute("DELETE FROM eventreq WHERE eventid=?", (eventid,))

            await db.execute("SELECT * FROM sqlite_sequence WHERE name=?", ("eventreq",))
            seq = await db.fetchone()
            await db.execute(
                "UPDATE sqlite_sequence SET seq=? WHERE name=?",
                (seq["seq"], "eventdetail")
            )

            details = detailsformat(dict(email_row))
            sendmail(email_row['email'], "Event Declined",
                     f"We sorry to inform to you that your event was declined for following reason:\n{reason}.\n\nEvent Details:\n\n{details}\n\nThank You!")
            sendlog(f"#EventDecline \nEvent Declined by {u}\nReason: {reason}.\nEvent Details:\n\n{details}")

    await db.execute("SELECT eventid FROM eventreq")
    remaining = await db.fetchone()
    if remaining:
        return RedirectResponse(url="/#pending", status_code=303)
    else:
        return RedirectResponse(url="/", status_code=303)

@app.get("/clearsession")
async def clearsession(request: Request):
    request.session.clear()
    sendlog("Session Cleared")
    return RedirectResponse(url="/", status_code=303)

@app.get("/dummyevent")
async def dummyevent(request: Request):
    request.session["eventname"] = random.choice(["Community Tree Plantation", "Neighborhood Blood Donation Camp", "Local Cleanliness Drive"])
    request.session["description"] = "Join us for a community tree plantation drive to make our neighborhood greener and healthier!"
    request.session["location"] = random.choice(["Central Park", "Community Center", "City Hall", "Riverside Park", "Downtown Square"])
    request.session["category"] = random.choice(["Tree Plantation", "Blood Donation", "Cleanliness Drive"])
    request.session["eventstartdate"] = f"{random.randint(2026, 2028)}-{random.randint(10, 12):02d}-{random.randint(10, 28):02d}"
    request.session["enddate"] = f"{random.randint(2026, 2028)}-{random.randint(10, 12):02d}-{random.randint(10, 28):02d}"
    request.session["starttime"] = f"{random.randint(10, 12)}:{random.randint(10, 59)}"
    request.session["endtime"] = f"{random.randint(10, 12)}:{random.randint(10, 59)}"
    return RedirectResponse(url="/#add", status_code=303)

@app.get("/admin/pool/close")
async def admin_pool_close(request: Request):
    if request.session.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    loop = asyncio.get_event_loop()
    def _close_all():
        closed = 0
        while not _db_idle_queue.empty():
            try:
                db = _db_idle_queue.get_nowait()
                _pool_close_one(db)
                closed += 1
            except Exception:
                pass
        return closed
    closed = await loop.run_in_executor(None, _close_all)
    sendlog(f"Admin pool close: {closed} connections closed by {request.session.get('username')}")
    return JSONResponse({"status": "closed", "connections_closed": closed, "pool_size": 0})

@app.get("/admin/pool/open")
async def admin_pool_open(request: Request):
    if request.session.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    loop = asyncio.get_event_loop()
    def _open_all():
        opened = 0
        errors = 0
        needed = _DB_POOL_MAX - _db_open_count
        for _ in range(max(0, needed)):
            if _try_open_and_enqueue():
                opened += 1
            else:
                errors += 1
        return opened, errors
    opened, errors = await loop.run_in_executor(None, _open_all)
    current = _db_idle_queue.qsize()
    sendlog(f"Admin pool open: {opened} connections opened by {request.session.get('username')}")
    return JSONResponse({"status": "opened", "connections_opened": opened, "errors": errors, "pool_size": current})

@app.get("/admin/pool/status")
async def admin_pool_status(request: Request):
    if request.session.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")

    server_connections = None
    server_error = None
    loop = asyncio.get_event_loop()

    def _get_connections():
        db, c = _pool_acquire()
        try:
            c.execute("LIST CONNECTIONS")
            rows = c.fetchall()
            return [dict(r) for r in rows] if rows else []
        except Exception as e:
            return []
        finally:
            _pool_release(db)

    try:
        result = await loop.run_in_executor(None, _get_connections)
        server_connections = result
    except Exception as e:
        server_error = str(e)

    return JSONResponse({
        "pool_idle": _db_idle_queue.qsize(),
        "pool_open_total": _db_open_count,
        "pool_max": _DB_POOL_MAX,
        "total_server_connections": len(server_connections) if server_connections else 0,
        "server_connections": server_connections,
        "server_error": server_error,
        "hint": "Use /admin/pool/kill/{id} to close a specific connection"
    })

@app.get("/admin/pool/kill/{connection_id}")
async def admin_pool_kill(request: Request, connection_id: int):
    if request.session.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    loop = asyncio.get_event_loop()
    def _kill():
        db, c = _pool_acquire()
        try:
            c.execute(f"CLOSE CONNECTION {connection_id}")
            return True
        except Exception as e:
            return str(e)
        finally:
            _pool_release(db)
    result = await loop.run_in_executor(None, _kill)
    if result is True:
        sendlog(f"Admin killed server connection {connection_id} — {request.session.get('username')}")
        return JSONResponse({"status": "killed", "connection_id": connection_id})
    return JSONResponse({"status": "error", "detail": result})

@app.get("/admin/pool/killall")
async def admin_pool_killall(request: Request):
    """Kill ALL server-side connections except the one used to run this command."""
    if request.session.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    loop = asyncio.get_event_loop()
    def _killall():
        db, c = _pool_acquire()
        try:
            c.execute("LIST CONNECTIONS")
            rows = c.fetchall()
            ids = [r["id"] for r in rows]
            killed, failed = [], []
            for cid in ids:
                try:
                    c.execute(f"CLOSE CONNECTION {cid}")
                    killed.append(cid)
                except Exception:
                    failed.append(cid)
            return killed, failed
        except Exception as e:
            return [], [str(e)]
        finally:
            _pool_release(db)
    killed, failed = await loop.run_in_executor(None, _killall)
    # Drain idle queue and reset open count, then refill fresh
    while not _db_idle_queue.empty():
        try:
            db = _db_idle_queue.get_nowait()
            _pool_close_one(db)
        except Exception:
            pass
    loop2 = asyncio.get_event_loop()
    await loop2.run_in_executor(None, _init_pool)
    sendlog(f"Admin killall: killed={killed} failed={failed} — {request.session.get('username')}")
    return JSONResponse({"status": "done", "killed": killed, "failed": failed, "pool_cleared": True})

@app.get("/api/leaderboard")
async def api_leaderboard():
    """Returns top 5 organizers. Cached for 60s so it's near-instant."""
    global _leaderboard_cache
    now = time.time()
    if _leaderboard_cache["data"] and now - _leaderboard_cache["ts"] < LEADERBOARD_CACHE_TTL:
        return JSONResponse(content=_leaderboard_cache["data"])

    all_users = await run_query("SELECT name, username, events FROM userdetails", fetchmode="all")
    organizers = []
    for u in all_users:
        event_count = len(u["events"].split(",")) if u["events"] else 0
        if event_count > 0:
            organizers.append({"name": u["name"], "username": u["username"], "count": event_count})

    organizers.sort(key=lambda x: x["count"], reverse=True)
    top5 = organizers[:5]
    _leaderboard_cache = {"data": top5, "ts": now}
    return JSONResponse(content=top5)

async def api(request: Request, db: AsyncDB = Depends(get_db)):
    await db.execute("SELECT * FROM eventdetail")
    events = [dict(row) for row in await db.fetchall()]
    user = dict(request.session)
    user_details = "No user logged in"
    if user.get("username"):
        await db.execute("SELECT * FROM userdetails WHERE username=?", (user["username"],))
        ud = await db.fetchone()
        user_details = dict(ud) if ud else {}
    toreturn = {
        "active events": events,
        "current session including draft add event values": user,
        "current user": user_details
    }
    return JSONResponse(content=toreturn)


@app.get("/checkeventloop")
def checkeventloop():
    db, c = sync_db()
    try:
        c.execute("SELECT * FROM eventdetail")
        ch = c.fetchall()
        hour24 = datetime.timedelta(hours=24)

        for x in ch:
            try:
                etime = datetime.datetime.strptime(
                    f"{x['eventenddate']} {x['eventendtime']}", "%Y-%m-%d %H:%M"
                ).replace(tzinfo=ist)

                # etime = etime + hour24

                if etime <= datetime.datetime.now(ist):
                    print(f"Deleting event {x['eventid']}")
                    del_event(c, x["eventid"])
                    details = detailsformat(dict(x))
                    sendmail(x["email"], "Event Ended",
                             f"Hey there your event was ended, so it has been deleted!\n\nEvent Details:\n\n{details}\n\nThank You!")
                    sendlog(f"#EventEnd \nEvent Ended at {etime.strftime('%Y-%m-%d %H:%M:%S')}.\nEvent Details:\n\n{details}")
                    # Invalidate campaigns cache
                    _campaigns_cache["ts"] = 0
            except Exception as e:
                sendlog(f"Date parse error for event {x['eventid']}: {e}")

        return Response(content="<h1>CHECK EVENT LOOP COMPLETED</h1>", media_type="text/html")
    except Exception as e:
        text = f"Check event loop error: {e}"
        sendlog(text)
        return Response(content=text, media_type="text/plain")
    finally:
        close_db(db)

@app.get("/download_ics/{eventid}")
async def download_ics(eventid: int, db: AsyncDB = Depends(get_db)):
    await db.execute("SELECT * FROM eventdetail WHERE eventid=?", (eventid,))
    event = await db.fetchone()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    try:
        start_dt = f"{event['eventstartdate'].replace('-', '')}T{event['starttime'].replace(':', '')}00"
        end_dt = f"{event['enddate'].replace('-', '')}T{event['endtime'].replace(':', '')}00"
    except Exception:
        start_dt = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
        end_dt = start_dt

    ics_content = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//SahyogSutra//Events//EN
BEGIN:VEVENT
UID:SahyogSutra-{eventid}
DTSTAMP:{datetime.datetime.now().strftime('%Y%m%dT%H%M%S')}
DTSTART:{start_dt}
DTEND:{end_dt}
SUMMARY:{event['eventname']}
DESCRIPTION:{event['description']}
LOCATION:{event['location']}
END:VEVENT
END:VCALENDAR"""

    return Response(
        content=ics_content,
        media_type="text/calendar",
        headers={"Content-Disposition": f"attachment; filename=event_{eventid}.ics"}
    )

@app.get("/export_data")
async def export_data(request: Request, db: AsyncDB = Depends(get_db)):
    username = request.session.get("username")
    if not username:
        raise HTTPException(status_code=401, detail="Please login first")

    await db.execute("SELECT * FROM userdetails WHERE username=?", (username,))
    ud = await db.fetchone()
    if not ud:
        raise HTTPException(status_code=404, detail="User not found")

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["--- USER PROFILE ---"])
    writer.writerow(["Name", "Username", "Email", "Role", "Events IDs"])
    writer.writerow([ud["name"], ud["username"], ud["email"], ud["role"], ud["events"]])

    writer.writerow([])
    writer.writerow(["--- CREATED EVENTS ---"])
    if ud["events"]:
        event_ids = ud["events"].split(",")
        writer.writerow(["Event ID", "Name", "Location", "Category", "Date", "Description"])
        for eid in event_ids:
            await db.execute("SELECT * FROM eventdetail WHERE eventid=?", (eid,))
            ev = await db.fetchone()
            if ev:
                writer.writerow([ev["eventid"], ev["eventname"], ev["location"], ev["category"], ev["eventstartdate"], ev["description"]])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=SahyogSutra_data_{username}.csv"}
    )

# --- SocketIO Events ---

@sio.on("add_grp_msg")
async def add_group_msg(sid, data):
    username = data["username"]
    message = data["message"]
    eventid = data["eventid"]
    msg_time = datetime.datetime.now(ist).strftime("%Y-%m-%d %H:%M:%S")

    loop = asyncio.get_event_loop()

    def _insert():
        db, c = sync_db()
        try:
            find = c.execute("SELECT * FROM messages2 WHERE eventid=(?)", (eventid,)).fetchone()
            if not find:
                c.execute("INSERT INTO messages2(eventid, msgs) VALUES(?, ?)", (eventid, "[]"))
                find = c.execute("SELECT * FROM messages2 WHERE eventid=(?)", (eventid,)).fetchone()
            msg = find["msgs"]
            msg = ast.literal_eval(msg)
            updated = (username, message, msg_time)
            msg.append(updated)
            c.execute("UPDATE messages2 SET msgs=(?) WHERE eventid=(?)", (str(msg), eventid))
            db.commit()
        finally:
            close_db(db)

    await loop.run_in_executor(None, _insert)
    await sio.emit("new_message", {
        "eventid": eventid,
        "username": username,
        "message": message,
        "time": msg_time
    })

@sio.on("addeventlike")
async def add_like(sid, data):
    eventid = data["eventid"]
    byuser = data["byuser"]
    like_type = data["type"]

    loop = asyncio.get_event_loop()

    def _update_like():
        db, c = sync_db()
        try:
            ud = c.execute("SELECT * FROM userdetails WHERE username=?", (byuser,)).fetchone()
            liked_events = ud["likes"].split(",") if ud["likes"] else []

            if like_type == "add":
                if str(eventid) not in liked_events:
                    liked_events.append(str(eventid))
                    c.execute("UPDATE eventdetail SET likes = likes + 1 WHERE eventid=?", (eventid,))
            else:
                if str(eventid) in liked_events:
                    liked_events.remove(str(eventid))
                    c.execute("UPDATE eventdetail SET likes = likes - 1 WHERE eventid=?", (eventid,))

            new_likes_str = ",".join(liked_events)
            c.execute("UPDATE userdetails SET likes=? WHERE username=?", (new_likes_str, byuser))
            new_likes_val = c.execute("SELECT likes FROM eventdetail WHERE eventid=?", (eventid,)).fetchone()["likes"]
            db.commit()
            print(f"Like update: ID = {eventid}, Likes: {new_likes_val}, Type = {like_type}")
            return new_likes_val
        finally:
            close_db(db)

    # Run DB update in executor thread and capture the returned like count
    new_likes = await loop.run_in_executor(None, _update_like)

    # Emit using the value returned from the executor
    await sio.emit("update_like", {"eventid": eventid, "likes": new_likes})


# --- Final ASGI App: Single SocketIO mount ---
app = socketio.ASGIApp(sio, app)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host=app_running_host, port=app_running_port, reload=False)
