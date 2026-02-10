from . import sendlog, sendmail, detailsformat

def addevent(c, form_data: dict, session_username: str):
    field = ["eventname", "email", "starttime", "endtime", "eventdate", "enddate", "location", "category", "description", "username"]

    # Extract values from the dict (form_data)
    # The last field is username, which we get from the session passed in
    event_values = [form_data.get(y) for y in field[:-1]]
    event_values.append(session_username)

    check = c.execute("SELECT * FROM eventdetail WHERE eventname=(?)", (event_values[0],))
    fetchall = check.fetchall()
    for ab in fetchall:
        # Compare fields. field[:-1] because 'username' is not in form_data usually or handled separately
        if all(str(ab[x]) == str(y) for x, y in zip(field, event_values)):
            return "Event Already Exists"

    tuple_all = ", ".join(field)
    # Create placeholders
    vals = ", ".join(["?"] * len(event_values))

    c.execute(f"INSERT INTO eventdetail({tuple_all}) VALUES ({vals})", tuple(event_values))
    lastid = c.execute("SELECT eventid FROM eventdetail ORDER BY eventid DESC LIMIT 1").fetchone()
    c.execute("DELETE FROM eventreq WHERE eventid=(?)", (lastid["eventid"], ))

    uud = c.execute("SELECT events FROM userdetails WHERE username=?", (event_values[-1], )).fetchone()
    if not uud or not uud["events"]:
        fe = []
    else:
        fe = uud["events"].split(",")
    fe.append(str(lastid["eventid"]))
    joint = ",".join(fe)
    c.execute("UPDATE userdetails SET events=? WHERE username=?", (joint, event_values[-1]))

    eventdetails = c.execute("SELECT * FROM eventdetail WHERE eventid=?", (lastid["eventid"], )).fetchone()
    details = detailsformat(eventdetails)
    sendmail(event_values[1], "Event Approved", f'Congragulations\n\nYour Event is approved and now visible on Campaigns Page.\n\nEvent Details:\n\n{details}\n\nThank You!')
    sendlog(f"#EventAdd \nNew Event Added:\n{details}")
    return "Event added!"


def addeventrequest(c, form_data: dict, session: dict):
    uuname, uemail = session.get("username"), session.get("email")
    field = ["eventname", "email", "starttime", "endtime", "eventdate", "enddate", "location", "category", "description", "username"]

    # Construct values
    event_values = [form_data.get(y) for y in field]
    # Overwrite username and email from session
    event_values[-1], event_values[1] = uuname, uemail

    check = c.execute("SELECT * FROM eventdetail WHERE eventname=(?)", (event_values[0],))
    fetchall = check.fetchall()
    for ab in fetchall:
            if all(str(ab[x]) == str(y) for x, y in zip(field, event_values)):
                return "Event Already Exists"

    fetchall2 = c.execute("SELECT * FROM eventreq WHERE eventname=(?)", (event_values[0],)).fetchall()
    for ab in fetchall2:
            if all(str(ab[x]) == str(y) for x, y in zip(field, event_values)):
                return "Event Already Submitted! Please Wait For Approval"

    efields = ", ".join(field)
    vals = ", ".join(["?"] * len(event_values))
    if not uuname:
        return "Please Login First To Add Event."

    c.execute(f"INSERT INTO eventreq({efields}) VALUES ({vals})", tuple(event_values))

    # In Flask we popped session here, in FastAPI we modify the session dict passed to us
    for x in field:
        if x not in ("email", "username"):
            session.pop(x, None)

    sendlog(f"#EventRequst \nNew Event Request: {event_values} by {uuname}")
    return "Event Registered ✅. Kindly wait for approval!"
