import os
import smtplib
import ssl
import threading

from email.message import EmailMessage

from .sendlog_model import sendlog


def sendmailthread(receiver, subject, message):
    sender = "dipanshuashokagarwal@gmail.com"
    password = os.environ.get("MAIL_APP_PASS")
    context = ssl.create_default_context()
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = receiver
    msg["Subject"] = subject
    msg.set_content(message)  # handles UTF-8 automatically

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as smtp:
        smtp.login(sender, password)
        smtp.send_message(msg)
        sendlog(f"Email sent to {receiver}")

# def sendmailthread(receiver, subject, message):
    # api_token = "mlsn.84bc5ebb7a97a83db3f7f73bb52e9ecc282ed8e9b7f84824539df975f91e9a58"
    # domain = "test-zxk54v8qwqxljy6v.mlsender.net"

    # from mailersend import MailerSendClient, EmailBuilder

    # ms = MailerSendClient(api_key=api_token)

    # email = (EmailBuilder()
    #     .from_email(f"dipanshu@{domain}", "dipanshu")
    #         .to_many([{"email": "dipanshu0919@gmail.com", "name": "Recipient"}])
    #         .subject("Hello from dipanshu!")
    #         .html("Hello bro!")
    #         .text("Hello World!")
    #         .build())

    # response = ms.emails.send(email)
    # print(response)



def sendmail(receiver, subject, message):
    thread = threading.Thread(target=sendmailthread, args=(receiver, subject, message))
    thread.start()
