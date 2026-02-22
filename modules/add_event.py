from . import sendlog, sendmail, detailsformat

def addevent(c, form_data: dict, owner_username: str):
    field = ["eventname", "email", "eventstarttime", "eventendtime", "eventstartdate", "eventenddate", "location", "category", "description", "username"]
    event_values = []
    for f in field:
        if f == "username":
            event_values.append(owner_username)
        else:
            event_values.append(form_data.get(f))

    check = c.execute("SELECT * FROM eventdetail WHERE eventname=?", (event_values[0],))
    fetchall = check.fetchall()
    for ab in fetchall:
        if all(ab[x] == y for x, y in zip(field, event_values)):
            return "Event Already Exists"

    tuple_all = ", ".join(field)
    vals = ", ".join(["?"] * len(event_values))

    try:
        c.execute(f"INSERT INTO eventdetail({tuple_all}) VALUES ({vals})", tuple(event_values))

        lastid = c.execute("SELECT eventid FROM eventdetail ORDER BY eventid DESC LIMIT 1").fetchone()

        # Delete matched request by eventid (accurate post-insert)
        c.execute("DELETE FROM eventreq WHERE eventid=?", (lastid["eventid"],))

        # Update userdetails 'events' column
        uud = c.execute("SELECT events FROM userdetails WHERE username=?", (owner_username,)).fetchone()
        if not uud or not uud["events"]:
            fe = []
        else:
            fe = uud["events"].split(",")

        fe.append(str(lastid["eventid"]))
        joint = ",".join(fe)
        c.execute("UPDATE userdetails SET events=? WHERE username=?", (joint, owner_username))

        # Fetch details for email
        eventdetails = c.execute("SELECT * FROM eventdetail WHERE eventid=?", (lastid["eventid"],)).fetchone()
        details = detailsformat(eventdetails)

        sendmail(event_values[1], "Event Approved", f'Congragulations\n\nYour Event is approved and now visible on Campaigns Page.\n\nEvent Details:\n\n{details}\n\nThank You!')

        sendlog(f"#EventAdd \nNew Event Added:\n{details}")
        return "Event added!"

    except Exception as e:
        print(f"Error adding event: {e}")
        return f"Error adding event: {str(e)}"


def addeventrequest(c, form_data: dict, session: dict):
    uuname, uemail = session.get("username"), session.get("email")

    if not uuname:
        return "Please Login First To Add Event."

    field = ["eventname", "email", "eventstarttime", "eventendtime", "eventstartdate", "eventenddate", "location", "category", "description", "username"]

    event_values = []
    for f in field:
        if f == "username":
            event_values.append(uuname)
        elif f == "email":
            event_values.append(uemail)
        else:
            event_values.append(form_data.get(f))

    # Check if event already exists in approved events
    fetchall = c.execute("SELECT * FROM eventdetail WHERE eventname=?", (event_values[0],)).fetchall()
    for ab in fetchall:
        if all(ab[x] == y for x, y in zip(field, event_values)):
            return "Event Already Exists"

    # Check if event request already submitted and pending approval
    fetchall2 = c.execute("SELECT * FROM eventreq WHERE eventname=?", (event_values[0],)).fetchall()
    for ab in fetchall2:
        if all(ab[x] == y for x, y in zip(field, event_values)):
            return "Event Already Submitted! Please Wait For Approval"

    efields = ", ".join(field)
    vals = ", ".join(["?"] * len(event_values))

    c.execute(f"INSERT INTO eventreq({efields}) VALUES ({vals})", tuple(event_values))

    # Clear draft fields from session (keep email/username)
    for x in field:
        if x not in ("email", "username"):
            session.pop(x, None)

    sendlog(f"#EventRequst \nNew Event Request: {event_values} by {uuname}")
    return "Event Registered ✅. Kindly wait for approval!"
