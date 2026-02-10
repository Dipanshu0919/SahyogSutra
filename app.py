import os
import json
import random
import datetime
import time
import threading
import zoneinfo
import requests
import sqlitecloud as sq
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any

from fastapi import FastAPI, Request, Form, Depends, Response, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
import socketio
from dotenv import load_dotenv
from googletrans import Translator

# Import modules
from modules import sendlog, sendmail, sendmailthread, del_event, detailsformat
from modules import add_event as add_event_mod
from modules import delete_event as delete_event_mod

load_dotenv()

ist = zoneinfo.ZoneInfo("Asia/Kolkata")
translations_lock = threading.Lock()
active_events = 0

all_translations = {}
non_file_translations = {}

# --- Helper Functions (Threads & Utils) ---

def load_translations():
    global all_translations
    try:
        if os.path.exists("translations.json"):
            with open("translations.json", "r", encoding="utf-8") as f:
                all_translations = json.load(f)
                print("Translations loaded successfully.")
                sendlog(f"Translations file loaded successfully with {len(all_translations)} texts.")
    except Exception as e:
        print(f"Translation file error: {e}")
        sendlog(f"Translation file error: {e}")

def save_translations():
    global all_translations
    try:
        with open("translations.json", "w", encoding="utf-8") as f:
            json.dump(all_translations, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"Error saving translation file: {e}")
        sendlog(f"Error saving translation file: {e}")

def translation_file_thread():
    while True:
        time.sleep(60)
        with translations_lock:
            save_translations()
            try:
                with open("translations_backup.json", "w", encoding="utf-8") as f:
                    json.dump(all_translations, f, indent=4, ensure_ascii=False)
            except Exception:
                pass

def translate_thread(text, lang, save_file):
    global all_translations
    global non_file_translations
    try:
        t = Translator()
        # googletrans is blocking, but running in thread is fine
        translated = t.translate(text, dest=lang).text
        print(f"Translated '{text}' to '{translated}' in language '{lang}'")
    except Exception as e:
        print(f"Translation error: {e}")
        translated = text

    with translations_lock:
        translate_dict = non_file_translations if not save_file else all_translations
        existing = translate_dict.get(text, {})
        existing[lang] = translated
        translate_dict[text] = existing

def checkevent():
    while True:
        time.sleep(30 + random.randint(0,10))
        try:
            # In FastAPI dev mode, port is usually 8000 or defined by uvicorn
            # We will just call the logic directly or use a self-request if strictly needed
            # But here we can't easily self-request without knowing domain.
            # We'll rely on the logic being called or an external pinger.
            pass
        except Exception as e:
            print(f"Check event loop error: {e}")
            time.sleep(60)

# --- FastAPI Setup ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    load_translations()
    threading.Thread(target=checkevent, name="CheckEventExist", daemon=True).start()
    threading.Thread(target=translation_file_thread, name="TranslationFileThread", daemon=True).start()
    yield
    # Shutdown
    pass

app = FastAPI(lifespan=lifespan)

# Session Middleware (Replaces Flask signed cookies)
app.add_middleware(SessionMiddleware, secret_key=os.environ.get("FLASK_SECRET", "supersecretkey"))

# SocketIO Setup
sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')
socket_app = socketio.ASGIApp(sio, app)
# Note: To run this, pass 'app:socket_app' to uvicorn, or mount it if separate paths were possible,
# but wrapping is standard for python-socketio + fastapi.
# However, to keep 'app' as the main entry point for routes, we usually do:
app.mount("/socket.io", socketio.ASGIApp(sio)) # Mount for socket path
# But standard is wrapping. We will modify the entry point at bottom.

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# --- Database Dependency ---
def get_db():
    db = sq.connect(os.environ.get("SQLITECLOUD"))
    db.row_factory = sq.Row
    c = db.cursor()
    try:
        yield c
        db.commit()
    finally:
        db.close()

# --- Template Filters & Globals ---

def datetimeformat(value):
    if isinstance(value, str):
        try:
            return datetime.datetime.strptime(value, "%Y-%m-%d").strftime("%d %B %Y")
        except:
            return value
    return value

templates.env.filters["datetimeformat"] = datetimeformat

def translate_text(text, lang=None, save_file=True):
    # We need a way to get session lang inside template.
    # In FastAPI jinja, we can pass context, but here we are registering a global function.
    # We will pass lang explicitly from template or use a default.
    # NOTE: In the Flask app, it used session.get("lang").
    # To support this in FastAPI templates, we will inject 'user_language' into context
    # and pass it to this function, or the function acts on the text.
    # The original function signature in template is {{ translate("Text") }} without lang.
    # We'll rely on the context processor to partial this function or just handle it.

    # Since we can't easily access request.session inside a global jinja function without passing context,
    # we will rely on the "user_language" variable passed to the template context.

    global all_translations
    global non_file_translations

    # If lang is not provided, we return text (client-side logic or context processor logic needed)
    # But strictly following Python logic:
    if not lang or lang == "en":
        return text

    combined_translations = {**all_translations, **non_file_translations}

    if not combined_translations.get(text) or not combined_translations.get(text).get(lang):
        thread = threading.Thread(target=translate_thread, args=(text, lang, save_file))
        thread.start()

    translationss = all_translations if save_file else non_file_translations
    return translationss.get(text, {}).get(lang, text)

# We need to wrap translate_text to accept the language from the request context
# We will do this via a custom context processor in the route handler or inject it.

# --- Routes ---

@app.get("/")
def home(request: Request, c = Depends(get_db)):
    session = request.session
    currentuser = session.get("name", "User")
    currentuname = session.get("username")
    print("Welcome", currentuser)

    # Update active_events count logic
    # In flask this was calculated in show_campaigns mostly, here we just read global
    # or recalculate. To be safe, let's recalculate quickly or use global.
    global active_events

    isadmin = False
    userdetails = {}
    if currentuname:
        ud = c.execute("SELECT * FROM userdetails WHERE username=?", (currentuname, )).fetchone()
        if ud:
            if ud["role"] == "admin":
                isadmin = True
            userdetails = dict(ud) # Convert Row to dict

    template_name = session.get("template", "index.html")

    # Helper for template translation
    user_lang = session.get("lang", "en")
    def bound_translate(text, save_file=True):
        return translate_text(text, lang=user_lang, save_file=save_file)

    return templates.TemplateResponse(request, template_name, {
        "active_events_length": active_events,
        "fullname": currentuser,
        "c_user": str(currentuname).strip(),
        "isadmin": bool(isadmin),
        "userdetails": userdetails,
        "translate": bound_translate,
        "user_language": user_lang,
        "fvalues": {} # Just in case index needs it
    })

@app.post("/sendsignupotp")
async def sendotp(request: Request, email: str = Form(...), c = Depends(get_db)):
    otp = random.randint(1111,9999)
    request.session["signupotp"] = otp
    checkexists = c.execute("SELECT * FROM userdetails where email=?", (email,)).fetchone()
    if checkexists:
        return Response(content="Email already exists! Please try different email.", media_type="text/plain")

    sendmailthread(email, "Signup OTP", f"Your signup OTP is {otp}.\nUse it to sign up in DEcoCamp\n\nThankyou :)")
    return Response(content=f"OTP Sent to {email}! Please check spam folder if cant find it.", media_type="text/plain")

@app.post("/setlanguage/{lang}")
def setlanguage(request: Request, lang: str):
    request.session["lang"] = lang
    return Response(content="Language Set", media_type="text/plain")

@app.post("/generate_ai_description")
async def generate_ai_description(request: Request):
    try:
        form_data = await request.form()
        field = ["eventname", "starttime", "endtime", "eventdate", "enddate", "location", "category"]
        values = [[x, form_data.get(x)] for x in field if form_data.get(x)]
        u_lang = request.session.get("lang", "en")

        content = f"""Generate a description based on following details in pure '{u_lang}' language all words shold be in this language only and its content also in pure '{u_lang}' language:
        Details of event is as followes: {values}
        Generate total 4x descriptions, each description should be within 500 words. Include hastags in it. And reply me in a json format as below:
        {{"desc1": "description1 in formal tone", "desc2": "description2 in informal tone", "desc3": "description3 in promotional tone", "desc4": "description4 in entertaining tone"}}
        dont include any other text other than json format in your response. dont even include any word count or anything else dont even include category name in it. Just pure description in json format."""

        response = requests.post(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.environ.get('OPENROUTER_API_KEY')}",
                    "Content-Type": "application/json",
            },
            data = json.dumps({
                "model": "nvidia/nemotron-nano-9b-v2:free",
                "messages": [{"role": "user", "content": content}]
            }))
        data = response.json()
        print(data)
        output = data["choices"][0]["message"]["content"]
        to_json = json.loads(output)
        return JSONResponse(content=to_json)
    except Exception as e:
        print(f"AI Description Generation Error: {e}")
        return Response(content="Error generating description. Please try again later.", media_type="text/plain")

@app.get("/group-chat/from-event/{eventid}")
def group_chat_from_event(request: Request, eventid: int, c = Depends(get_db)):
    currentuname = request.session.get("username", "anonymous")
    eventdetail = c.execute("SELECT * FROM eventdetail WHERE eventid=?", (eventid,)).fetchone()
    if not eventdetail:
        return Response(content="No such event found.", media_type="text/plain")

    all_msgs = c.execute("SELECT * FROM messages WHERE eventid=? ORDER BY time ASC", (eventid,)).fetchall()

    # Construct message list manually since we don't zip directly in template easily usually
    messages = []
    if all_msgs:
        for x in all_msgs:
            messages.append((x["username"], x["message"], x["time"]))

    return templates.TemplateResponse(request, "groupchat.html", {
        "messages": messages,
        "eventid": eventid,
        "currentuname": currentuname,
        "eventname": eventdetail["eventname"]
    })

@app.get("/user/{username}")
def user_profile(request: Request, username: str, c = Depends(get_db)):
    userfulldetails = c.execute("SELECT * FROM userdetails WHERE username=?", (username,)).fetchone()
    if not userfulldetails:
        return Response(content="User not found.", media_type="text/plain")

    # Inject translation helper
    user_lang = request.session.get("lang", "en")
    def bound_translate(text, save_file=True):
        return translate_text(text, lang=user_lang, save_file=save_file)

    return templates.TemplateResponse(request, "userprofile.html", {
        "userdetails": dict(userfulldetails),
        "translate": bound_translate
    })

@app.get("/changetemplate")
def changetemplate(request: Request):
    ct = request.session.get("template", "index.html")
    if ct == "index.html":
        request.session["template"] = "index2.html"
    else:
        request.session["template"] = "index.html"
    return Response(content="Template Changed", media_type="text/plain")

@app.get("/show_add_form")
def show_add_form(request: Request):
    fv = {}
    fi = ["eventname", "email", "starttime", "endtime", "eventdate", "enddate", "location", "category", "description"]
    for x in fi:
        fv[x] = request.session.get(x, "")

    user_lang = request.session.get("lang", "en")
    def bound_translate(text, save_file=True):
        return translate_text(text, lang=user_lang, save_file=save_file)

    return templates.TemplateResponse(request, "addevent.html", {
        "fvalues": fv,
        "translate": bound_translate
    })

@app.get("/show_campaigns")
def show_campaigns(request: Request, c = Depends(get_db)):
    currentuname = request.session.get("username")
    c.execute("SELECT * FROM eventdetail")
    edetailslist = [dict(row) for row in c.fetchall()]

    alleventscat = []
    for x in edetailslist:
        cate = x["category"]
        if not cate in alleventscat:
            alleventscat.append(cate)

    allevents = {}
    for x in edetailslist:
        if x["category"] not in allevents:
            allevents[x["category"]] = []
        if x["category"] in alleventscat:
            allevents[x["category"]].append(x)

    global active_events
    total_events = [len(events) for events in allevents.values()]
    active_events = sum(total_events)

    isadmin = False
    userdetails = {}
    if currentuname:
        ud = c.execute("SELECT * FROM userdetails WHERE username=?", (currentuname, )).fetchone()
        if ud and ud["role"] == "admin":
            isadmin = True
        userdetails = dict(ud) if ud else {}

    viewuserevent = request.session.get("vieweventusername", f"{currentuname}")
    ve = request.session.get("viewyourevents", False)

    request.session.pop("vieweventusername", None)
    request.session.pop("viewyourevents", None)

    sortby = request.session.get("sortby", "eventdate")

    user_lang = request.session.get("lang", "en")
    def bound_translate(text, save_file=True):
        return translate_text(text, lang=user_lang, save_file=save_file)

    return templates.TemplateResponse(request, "campaigns.html", {
        "allevents": allevents,
        "userdetails": userdetails,
        "viewyourevents": ve,
        "sortby": sortby,
        "isadmin": bool(isadmin),
        "c_user": str(currentuname).strip(),
        "viewuserevent": viewuserevent,
        "translate": bound_translate
    })

@app.post("/viewyourevents/{username}")
def viewyourevents(request: Request, username: str):
    request.session["viewyourevents"] = True
    request.session["vieweventusername"] = username
    return Response(content="OK", media_type="text/plain")

@app.post("/setsortby/{sortby}")
def setsortby(request: Request, sortby: str):
    request.session["sortby"] = sortby
    return Response(content="Sort by set", media_type="text/plain")

@app.post("/signup")
async def signup(request: Request, c = Depends(get_db)):
    form_data = await request.form()
    username = form_data.get("username").lower()
    password = form_data.get("password")
    cpassword = form_data.get("cpassword")
    name = form_data.get("nameofuser")
    email = form_data.get("email")
    otp = form_data.get("signupotp")

    if c.execute("SELECT * FROM userdetails where username=?", (username,)).fetchone():
        return Response(content="Username Already Exists", media_type="text/plain")
    if c.execute("SELECT * FROM userdetails where email=?", (email,)).fetchone():
        return Response(content="Email Already Exists", media_type="text/plain")

    if str(request.session.get("signupotp")) != str(otp).strip():
        return Response(content="Wrong Signup OTP", media_type="text/plain")
    elif password != cpassword:
        return Response(content="Wrong Confirm Password", media_type="text/plain")
    elif len(password) < 8:
        return Response(content="Password must be at least 8 characters long", media_type="text/plain")
    else:
        c.execute("INSERT INTO userdetails(username, password, name, email) VALUES(?, ?, ?, ?)", (username, password, name, email))
        request.session["username"] = username
        request.session["name"] = name
        request.session["email"] = email
        request.session.pop("signupotp", None)
        sendlog(f"New Signup: {name} ({username})")
        return Response(content="Signup Success ✅", media_type="text/plain")

@app.post("/login")
async def login(request: Request, c = Depends(get_db)):
    form_data = await request.form()
    username = form_data.get("loginusername").lower()
    password = form_data.get("loginpassword")

    c.execute("SELECT * FROM userdetails where username=? or email=?", (username, username))
    fetched = c.fetchone()
    if not fetched:
        return Response(content="No username found", media_type="text/plain")
    elif password != fetched["password"]:
        return Response(content="Wrong Password", media_type="text/plain")
    else:
        request.session["username"] = fetched["username"]
        request.session["name"] = fetched["name"]
        request.session["email"] = fetched["email"]
        sendlog(f"User Login: {fetched['name']} ({fetched['username']})")
        return Response(content="Login Success ✅", media_type="text/plain")

@app.post("/addevent")
async def addnewevent(request: Request, c = Depends(get_db)):
    form_data = await request.form()
    username = request.session.get("username")
    # addevent_mod expects form_data dict and a username for validation
    res = add_event_mod.addevent(c, dict(form_data), username)
    return Response(content=res, media_type="text/plain")

@app.post("/addeventreq")
async def addeventreq(request: Request, c = Depends(get_db)):
    form_data = await request.form()
    # addeventrequest modifies session in-place in Flask, here we pass the session object (dict-like)
    res = add_event_mod.addeventrequest(c, dict(form_data), request.session)
    return Response(content=res, media_type="text/plain")

@app.get("/show_pending_events")
def pendingevents(request: Request, c = Depends(get_db)):
    uname = request.session.get("username")
    if not uname:
        return Response(content="Login First", media_type="text/plain")
    f = c.execute("SELECT * FROM userdetails WHERE username=?", (uname, )).fetchone()
    if f["role"] == "admin":
        c.execute("SELECT * FROM eventreq")
        pe = [dict(row) for row in c.fetchall()]
        return templates.TemplateResponse(request, "pendingevents.html", {"pendingevents": pe})
    else:
        return RedirectResponse(url="/", status_code=303)

@app.get("/deleteevent/{eventid}")
def deleteevent(request: Request, eventid: int, c = Depends(get_db)):
    res = delete_event_mod.delete_eventfromid(c, eventid, request.session)
    if res == "REDIRECT_HOME":
        return RedirectResponse(url="/", status_code=303)
    return Response(content=res, media_type="text/plain")

@app.get("/logout")
def logout(request: Request):
    u = request.session.pop('username', None)
    n = request.session.pop('name', None)
    e = request.session.pop('email', None)
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
def decline_event(request: Request, eventid: int, reason: str, c = Depends(get_db)):
    u = request.session.get("username")
    if u:
        c.execute("SELECT * FROM userdetails WHERE username=?", (u, ))
        f = c.fetchone()
        if f["role"] == "admin":
            email = c.execute("SELECT * from eventreq WHERE eventid=?", (eventid, )).fetchone()
            c.execute("DELETE FROM eventreq WHERE eventid=?", (eventid, ))
            seq = c.execute("SELECT * FROM sqlite_sequence WHERE name=?", ("eventreq",)).fetchone()
            c.execute("UPDATE sqlite_sequence SET seq=? WHERE name=?", (seq["seq"], "eventdetail"))
            details = detailsformat(dict(email))
            sendmail(email['email'], "Event Declined", f"We sorry to inform to you that your event was declined for following reason:\n{reason}.\n\nEvent Details:\n\n{details}\n\nThank You!")
            sendlog(f"#EventDecline \nEvent Declined by {u}\nReason: {reason}.\nEvent Details:\n\n{details}")

    if c.execute("SELECT eventid FROM eventreq").fetchone():
        return RedirectResponse(url="/#pending", status_code=303)
    else:
        return RedirectResponse(url="/", status_code=303)

@app.get("/clearsession")
def clearsession(request: Request):
    request.session.clear()
    sendlog(f"Session Cleared")
    return RedirectResponse(url="/", status_code=303)

@app.get("/dummyevent")
def dummyevent(request: Request):
    request.session["eventname"] = random.choice(["Community Tree Plantation", "Neighborhood Blood Donation Camp", "Local Cleanliness Drive"])
    request.session["description"] = "Join us for a community tree plantation drive to make our neighborhood greener and healthier!"
    request.session["location"] = random.choice(["Central Park", "Community Center", "City Hall", "Riverside Park", "Downtown Square"])
    request.session["category"] = random.choice(["Tree Plantation", "Blood Donation", "Cleanliness Drive"])
    request.session["eventdate"] = f"{random.randint(2025,2028)}-{random.randint(10,12):02d}-{random.randint(10,28):02d}" # Fixed fmt for date input
    request.session["enddate"] = f"{random.randint(2025,2028)}-{random.randint(10,12):02d}-{random.randint(10,28):02d}"
    request.session["starttime"] = f"{random.randint(10,12)}:{random.randint(10,59)}"
    request.session["endtime"] = f"{random.randint(10,12)}:{random.randint(10,59)}"
    return Response(content="Dummy Event Added to Session", media_type="text/plain")

@app.get("/api")
def api(request: Request, c = Depends(get_db)):
    events = [dict(row) for row in c.execute("SELECT * FROM eventdetail").fetchall()]
    user = dict(request.session)
    user_details = "No user logged in"
    if user.get("username"):
        ud = c.execute("SELECT * FROM userdetails WHERE username=?", (user["username"],)).fetchone()
        user_details = dict(ud) if ud else {}
    toreturn = {"active events": events, "current session including draft add event values": user, "current user": user_details}
    return JSONResponse(content=toreturn)

@app.get("/checkeventloop")
def checkeventloop(c = Depends(get_db)):
    try:
        ch = c.execute("SELECT * FROM eventdetail").fetchall()
        hour24 = datetime.timedelta(hours=24)
        for x in ch:
            # Note: format matching relies on input validation.
            try:
                etime = datetime.datetime.strptime(f"{x['enddate']} {x['endtime']}", "%Y-%m-%d %H:%M").replace(tzinfo=ist)
                if etime <= datetime.datetime.now(ist):
                    del_event(c, x["eventid"])
                    details = detailsformat(dict(x))
                    sendmail(x["email"], "Event Ended", f"Hey there your event was ended, so it has been deleted!\n\nEvent Details:\n\n{details}\n\nThank You!")
                    sendlog(f"#EventEnd \nEvent Ended at {etime.strftime('%Y-%m-%d %H:%M:%S')}.\nEvent Details:\n\n{details}")
            except Exception as e:
                print(f"Date parse error for event {x['eventid']}: {e}")

        return Response(content="<h1>CHECK EVENT LOOP COMPLETED</h1>", media_type="text/html")
    except Exception as e:
        text = f"Checkk event loop error: {e}"
        sendlog(text)
        print(text)
        return Response(content=text, media_type="text/plain")

# --- SocketIO Events ---

# Helper to get DB connection manually inside socket events
def get_socket_db():
    db = sq.connect(os.environ.get("SQLITECLOUD"))
    db.row_factory = sq.Row
    c = db.cursor()
    return db, c

@sio.on("add_grp_msg")
async def add_group_msg(sid, data):
    username = data["username"]
    message = data["message"]
    eventid = data["eventid"]
    msg_time = datetime.datetime.now(ist).strftime("%Y-%m-%d %H:%M:%S")

    db, c = get_socket_db()
    try:
        c.execute("INSERT INTO messages(eventid, username, message, time) VALUES(?, ?, ?, ?)", (eventid, username, message, msg_time))
        db.commit()
    finally:
        db.close()

    await sio.emit("new_message", {"eventid": eventid, "username": username, "message": message, "time": msg_time})

@sio.on("addeventlike")
async def add_like(sid, data):
    eventid = data["eventid"]
    byuser = data["byuser"]
    type = data["type"]

    db, c = get_socket_db()
    try:
        ud = c.execute("SELECT * FROM userdetails WHERE username=?", (byuser,)).fetchone()
        if not ud["likes"]:
            liked_events = []
        else:
            liked_events = ud["likes"].split(",")

        if type == "add":
            if str(eventid) not in liked_events:
                liked_events.append(str(eventid))
                c.execute("UPDATE eventdetail SET likes = likes + 1 WHERE eventid=?", (eventid,))
        else:
            if str(eventid) in liked_events:
                liked_events.remove(str(eventid))
                c.execute("UPDATE eventdetail SET likes = likes - 1 WHERE eventid=?", (eventid,))

        new_likes_str = ",".join(liked_events)
        c.execute("UPDATE userdetails SET likes=? WHERE username=?", (new_likes_str, byuser))

        # Re-fetch for updated count
        new_likes = c.execute("SELECT likes FROM eventdetail WHERE eventid=?", (eventid,)).fetchone()["likes"]
        db.commit()
    finally:
        db.close()

    await sio.emit("update_like", {"eventid": eventid, "likes": new_likes})

# Wrap app at the end for uvicorn
app = socketio.ASGIApp(sio, app)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
