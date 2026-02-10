import zoneinfo
import threading
import requests
import datetime
import os


ist = zoneinfo.ZoneInfo("Asia/Kolkata")


def sendlogthread(message):
    link = f"https://api.telegram.org/bot{os.environ.get('TGBOTTOKEN')}/sendMessage"
    parameters = {"chat_id": "-1002945250812", "text": f'ㅤㅤㅤ\n🗓️ {datetime.datetime.now(ist).strftime("%Y-%m-%d %H:%M:%S")}\n{message}\nㅤㅤㅤ'}
    requests.get(link, params=parameters)

def sendlog(message):
    thread = threading.Thread(target=sendlogthread, args=(message,))
    thread.start()
