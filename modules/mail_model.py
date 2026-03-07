import os
import smtplib
import ssl
import threading

import resend

from email.message import EmailMessage

from .sendlog_model import sendlog


# def sendmailthread(receiver, subject, message):
#     sender = "dipanshuashokagarwal@gmail.com"
#     password = os.environ.get("MAIL_APP_PASS")
#     context = ssl.create_default_context()
#     msg = EmailMessage()
#     msg["From"] = sender
#     msg["To"] = receiver
#     msg["Subject"] = subject
#     msg.set_content(message)  # handles UTF-8 automatically

#     with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as smtp:
#         smtp.login(sender, password)
#         smtp.send_message(msg)
#         sendlog(f"Email sent to {receiver}")

def sendmailthread(receiver, subject, message, type="text"):

    resend.api_key = os.environ.get("RESEND_API_KEY")

    r = resend.Emails.send({
      "from": "SahyogSutra Support <support@sahyogsutra.run.place>",
      "to": str(receiver),
      "subject": str(subject),
      type: f"{message}"
    })


def sendmail(receiver, subject, message):
    thread = threading.Thread(target=sendmailthread, args=(receiver, subject, message))
    thread.start()
