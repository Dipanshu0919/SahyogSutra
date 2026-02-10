def detailsformat(details):
    id = details["eventid"]
    name = details["eventname"]
    email = details["email"]
    stime = details["starttime"]
    etime = details["endtime"]
    edate = details["eventdate"]
    enddate = details["enddate"]
    location = details["location"]
    category = details["category"]
    description = details["description"]
    username = details["username"]
    text = f"Event ID: {id}\nEvent Name: {name}\nEmail: {email}\nStart Time: {stime}\nEnd Time: {etime}\nEvent Date: {edate}\nEnd Date: {enddate}\nLocation: {location}\nCategory: {category}\nDescription: {description}\nUsername: {username}"
    return text
