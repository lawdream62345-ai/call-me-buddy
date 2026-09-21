import os
import urllib.parse
import requests
from datetime import datetime, timedelta
import pytz
from fastapi import FastAPI, Request, HTTPException, Response

app = FastAPI(title="Satyam's PW Academic Assistant")

# Core Variables
QSTASH_TOKEN = os.environ.get("QSTASH_TOKEN", "").strip()
PW_TOKEN = os.environ.get("PW_BEARER_TOKEN", "").strip()
PRIMARY_BATCH_ID = os.environ.get("PRIMARY_BATCH_ID", "").strip()

# Twilio & Security Variables
TWILIO_SID = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
TWILIO_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
TWILIO_PHONE = os.environ.get("TWILIO_PHONE_NUMBER", "").strip()
MY_PHONE = os.environ.get("MY_PHONE_NUMBER", "").strip()
APP_SECRET = os.environ.get("APP_SECRET", "super_secret_satyam").strip()

raw_render_url = os.environ.get("RENDER_URL", "").strip()
RENDER_URL = raw_render_url if raw_render_url.startswith("http") else f"https://{raw_render_url}"

@app.get("/health")
def health_check():
    return {"status": "awake", "name": "Satyam's PW Bot"}

@app.api_route("/twiml", methods=["GET", "POST"])
def generate_twiml(msg: str = "It is time for your class"):
    """Serves high-definition TwiML voice instructions to Twilio."""
    twiml_script = (
        f"<?xml version='1.0' encoding='UTF-8'?>"
        f"<Response>"
        f"<Pause length='2'/>"
        f"<Say voice='Polly.Aditi' language='en-IN'>{msg}</Say>"
        f"</Response>"
    )
    return Response(content=twiml_script, media_type="application/xml")

@app.post("/execute-call")
async def execute_scheduled_call(request: Request):
    """Webhook triggered by QStash when the timer finishes."""
    data = await request.json()
    
    # Secures your endpoint so random bots can't trigger your phone
    if data.get("secret") != APP_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized call trigger")

    task_message = data.get("task", "No task provided")
    
    # Prepares the secure webhook URL Twilio will read from
    safe_msg = urllib.parse.quote(task_message)
    webhook_url = f"{RENDER_URL}/twiml?msg={safe_msg}"
    
    # Triggers the Twilio Call using REST API
    call_url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Calls.json"
    twilio_data = {
        "To": MY_PHONE,
        "From": TWILIO_PHONE,
        "Url": webhook_url,
        "Method": "GET"
    }
    
    response = requests.post(call_url, data=twilio_data, auth=(TWILIO_SID, TWILIO_TOKEN))
    
    if response.status_code in [200, 201]:
        return {"status": "Call Dispatched!", "task": task_message}
    else:
        return {"error": "Twilio Call failed", "details": response.text}

@app.post("/morning-routine")
def automate_daily_schedule():
    ist = pytz.timezone('Asia/Kolkata')
    now_ist = datetime.now(ist)
    
    headers = {"Authorization": PW_TOKEN, "Content-Type": "application/json"}
    
    try:
        response = requests.get("https://api.penpencil.co/v1/batches/my-schedule", headers=headers)
        pw_data = response.json()
    except Exception:
        return {"error": "Failed to fetch PW schedule."}
    
    for lecture in pw_data.get("data", []):
        if lecture.get("batchId") != PRIMARY_BATCH_ID:
            continue

        start_time_iso = lecture.get("startTime")
        if not start_time_iso:
            continue

        class_time = datetime.fromisoformat(start_time_iso.replace('Z', '+00:00')).astimezone(ist)
        alarm_time = class_time - timedelta(minutes=5)
        
        if alarm_time > now_ist:
            voice_msg = f"Hello Satyam. Your {lecture.get('topicName')} lecture starts in exactly 5 minutes."
            
            q_headers = {
                "Authorization": f"Bearer {QSTASH_TOKEN}", 
                "Upstash-Not-Before": str(int(alarm_time.timestamp())), 
                "Content-Type": "application/json"
            }
            
            # Packages the message AND the secret key for QStash to hold onto
            payload = {
                "task": voice_msg,
                "secret": APP_SECRET
            }
            
            requests.post(
                f"https://qstash.upstash.io/v2/publish/{RENDER_URL}/execute-call", 
                headers=q_headers, 
                json=payload
            )

    return {"status": "Automated successfully"}
