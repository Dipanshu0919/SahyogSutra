from . import sendlog, sendmail
from .detailformat import detailsformat

def del_event(c, eventid):
    try:
        edetail = c.execute("SELECT * FROM eventdetail WHERE eventid=?", (eventid,)).fetchone()
        if not edetail: return

        details = c.execute("SELECT * FROM userdetails WHERE username=?", (edetail["username"],)).fetchone()

        insert_in_ended_query = """INSERT INTO `endedevent` (`eventid`,`eventname`,`email`,`eventstarttime`,`eventendtime`,`eventstartdate`,`eventenddate`,`location`,`category`,`description`,`username`,`likes`)
                   SELECT `eventid`,`eventname`,`email`,`eventstarttime`,`eventendtime`,`eventstartdate`,`eventenddate`,`location`,`category`,`description`,`username`,`likes` FROM `eventdetail` WHERE `eventid` = (?)"""

        c.execute(insert_in_ended_query, (eventid,))

        c.execute("DELETE FROM eventdetail where eventid=?", (eventid,))
        c.execute("DELETE FROM messages where eventid=?", (eventid,))

        if details and details["events"]:
            events = details["events"].split(",")
            if str(eventid) in events:
                events.remove(str(eventid))
                new = ",".join(events)
                if events == []:
                    c.execute("UPDATE userdetails SET events=NULL WHERE username=?", (details["username"], ))
                else:
                    c.execute("UPDATE userdetails SET events=? WHERE username=?", (new, details["username"]))

        if details and details["likes"]:
            likes = details["likes"].split(",")
            if str(eventid) in likes:
                likes.remove(str(eventid))
                newl = ",".join(likes)
                if likes == []:
                    c.execute("UPDATE userdetails SET likes=NULL WHERE username=?", (details["username"], ))
                else:
                    c.execute("UPDATE userdetails SET likes=? WHERE username=?", (newl, details["username"]))

    except Exception as e:
        sendlog(f"Error Deleting Event {eventid}: {e}")
        print(f"Error Deleting Event {eventid}: {e}")


def delete_eventfromid(c, eventid, session: dict):
    uname = session.get("username")
    if not uname:
        return "Login First"

    c.execute("SELECT * FROM eventdetail WHERE eventid=?", (eventid,))
    fe = c.fetchone()
    if not fe:
        return "Event not found"

    extra = c.execute("SELECT * FROM userdetails WHERE username=?", (fe["username"], )).fetchone()
    c.execute("SELECT * FROM userdetails WHERE username=?", (uname,))
    fe2 = c.fetchone()

    if fe["username"] == uname or (fe2 and fe2["role"]=="admin"):
        try:
            del_event(c, eventid)
            details = detailsformat(fe)
            if extra:
                sendmail(extra["email"], "Event Deleted", f"Hey {extra['name']}! Your event was deleted by {uname}.\n\nEvent Details:\n\n{details}\n\nThank You!")
            sendlog(f"#EventDelete \nEvent Deleted by {uname}.\nEvent Details:\n\n{details}")
            return "REDIRECT_HOME"
        except Exception as e:
            sendlog(f"Error Deleting Event {eventid}: {e}")
            return f"Error: {e}"
    return "Unauthorized"
