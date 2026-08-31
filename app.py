import os
import json
import logging
import urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo
import httpx
from fastapi import FastAPI, Request, BackgroundTasks, Response
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.memory import MemoryJobStore
from twilio.rest import Client
from groq import Groq

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("ai-caller")

# ── ENVIRONMENT VARIABLES ─────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
GROQ_API_KEY   = os.environ.get("GROQ_API_KEY", "")
TWILIO_SID     = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN   = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE   = os.environ.get("TWILIO_PHONE_NUMBER", "")
MY_PHONE       = os.environ.get("MY_PHONE_NUMBER", "")

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
IST = ZoneInfo("Asia/Kolkata")

# ── SCHEDULER & FASTAPI ───────────────────────────────────────────────────────
app = FastAPI(title="24/7 AI Task Caller")
scheduler = AsyncIOScheduler(timezone=IST, jobstores={"default": MemoryJobStore()})
groq_client = Groq(api_key=GROQ_API_KEY)

@app.on_event("startup")
def start_scheduler():
    if not scheduler.running:
        scheduler.start()
        logger.info("[Scheduler] Active in IST timezone.")

# ── CORE FUNCTIONS ────────────────────────────────────────────────────────────
def trigger_phone_call(task_message: str):
    """Executes outbound call via public URL to support trial accounts."""
    if not (TWILIO_SID and TWILIO_TOKEN and TWILIO_PHONE and MY_PHONE):
        logger.error("[Twilio] Credentials missing.")
        return

    try:
        client = Client(TWILIO_SID, TWILIO_TOKEN)
        safe_msg = urllib.parse.quote(task_message)
        render_url = "https://call-me-buddy.onrender.com"
        webhook_url = f"{render_url}/twiml?msg={safe_msg}"

        call = client.calls.create(url=webhook_url, to=MY_PHONE, from_=TWILIO_PHONE)
        logger.info(f"[Twilio] Call placed! SID: {call.sid}")
    except Exception as e:
        logger.error(f"[Twilio] Call dispatch failed: {e}")

async def send_telegram_reply(chat_id: int, text: str):
    """Sends a confirmation reply back to your Telegram chat."""
    async with httpx.AsyncClient() as client:
        await client.post(TELEGRAM_API_URL, json={"chat_id": chat_id, "text": text})

def parse_natural_language(user_text: str) -> dict:
    """Extracts task and scheduled IST timestamp using Groq."""
    now_ist = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
    system_prompt = (
        f"Current Indian Standard Time (IST): {now_ist}. "
        "Extract the task and the future datetime for the requested reminder. "
        "Return strictly a raw JSON object with keys 'task' (string) and 'run_at' (YYYY-MM-DD HH:MM:SS in 24-hr format). "
        "Do not include markdown fences, backticks, or any additional text."
    )
    
    response = groq_client.chat.completions.create(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        model="openai/gpt-oss-20b",
        temperature=0.1,
    )
    
    raw = response.choices[0].message.content.strip()
    clean_json = raw.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(clean_json)
    except Exception:
        return {}

async def process_telegram_message(chat_id: int, text: str):
    parsed = parse_natural_language(text)
    
    if "run_at" not in parsed or "task" not in parsed:
        await send_telegram_reply(
            chat_id, 
            "❌ Could not understand the time or task.\n\n*Examples:*\n• *Remind me in 15 minutes to review contracts*\n• *Call me tomorrow at 7:30 AM to prepare notes*"
        )
        return

    try:
        target_naive = datetime.strptime(parsed["run_at"], "%Y-%m-%d %H:%M:%S")
        target_time = target_naive.replace(tzinfo=IST)
        
        if target_time <= datetime.now(IST):
            await send_telegram_reply(chat_id, "❌ That time is in the past. Please give a future time.")
            return

        job_id = f"job_{int(target_time.timestamp())}"
        scheduler.add_job(
            trigger_phone_call, 
            "date", 
            run_date=target_time, 
            args=[parsed["task"]], 
            id=job_id,
            replace_existing=True
        )
        
        confirmation = (
            f"✅ **Call Scheduled!**\n\n"
            f"📞 **Time (IST):** `{parsed['run_at']}`\n"
            f"📝 **Task:** {parsed['task']}"
        )
        await send_telegram_reply(chat_id, confirmation)
        
    except Exception as e:
        await send_telegram_reply(chat_id, f"❌ Error queueing job: {str(e)}")

# ── ROUTES ────────────────────────────────────────────────────────────────────
@app.get("/")
def health_check():
    """Ping target for UptimeRobot to prevent Render sleeping."""
    return {"status": "alive", "server_time_ist": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")}

@app.get("/twiml")
def generate_twiml(msg: str = "your task"):
    """Serves dynamic TwiML XML voice instructions to Twilio."""
    twiml_script = (
        f"<?xml version='1.0' encoding='UTF-8'?>"
        f"<Response>"
        f"<Pause length='1'/>"
        f"<Say voice='Polly.Aditi' language='en-IN'>"
        f"Hello! This is your AI reminder. It is time to: {msg}. "
        f"Stay focused and have a great session. Goodbye!"
        f"</Say>"
        f"</Response>"
    )
    return Response(content=twiml_script, media_type="application/xml")

@app.post("/webhook")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    """Receives inbound Telegram webhook payloads."""
    data = await request.json()
    if "message" in data and "text" in data["message"]:
        chat_id = data["message"]["chat"]["id"]
        text = data["message"]["text"]
        background_tasks.add_task(process_telegram_message, chat_id, text)
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
