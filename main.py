from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import asyncio, aiohttp, os, uuid, subprocess, tempfile, shutil, logging
from typing import Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
app = FastAPI(title="DocuFlare API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
jobs = {}

class VideoRequest(BaseModel):
 topic: str
 duration: int
 language: str
 voice_style: str
 music: str
 quality: str
 ratio: str
 mode: str
 custom_script: Optional[str] = None

def update_job(job_id, status, progress, step, video_url=None, error=None):
 jobs[job_id] = {"job_id": job_id, "status": status, "progress": progress, "step": step, "video_url": video_url, "error": error}

@app.get("/")
async def root():
 return {"message": "DocuFlare API v1.0", "status": "running"}

@app.get("/health")
async def health():
 return {"status": "healthy"}

@app.get("/status/{job_id}")
async def get_status(job_id: str):
 if job_id not in jobs:
  return {"error": "Job not found"}
 return jobs[job_id]

@app.post("/generate")
async def generate_video(req: VideoRequest, background_tasks: BackgroundTasks):
 job_id = str(uuid.uuid4())[:8]
 jobs[job_id] = {"job_id": job_id, "status": "pending", "progress": 0, "step": "Starting...", "video_url": None, "error": None}
 background_tasks.add_task(process_job, job_id, req)
 return {"job_id": job_id}

async def process_job(job_id, req):
 tmp_dir = tempfile.mkdtemp()
 try:
  update_job(job_id, "processing", 10, "Writing script...")
  script = await generate_script(req.topic, req.duration, req.language)
  update_job(job_id, "processing", 30, "Fetching footage...")
  clips = await fetch_footage(req.topic, req.duration, tmp_dir)
  update_job(job_id, "processing", 55, "Generating voiceover...")
  audio = await generate_voice(script, tmp_dir)
  update_job(job_id, "processing", 70, "Rendering video...")
  output = os.path.join(tmp_dir, f"{job_id}.mp4")
  render_video(clips, audio, output, req.duration)
  update_job(job_id, "processing", 90, "Uploading...")
  video_url = await upload_video(output, job_id)
  update_job(job_id, "done", 100, "Ready!", video_url=video_url)
 except Exception as e:
  update_job(job_id, "failed", 0, str(e), error=str(e))
 finally:
  shutil.rmtree(tmp_dir, ignore_errors=True)

async def generate_script(topic, duration, language):
 async with aiohttp.ClientSession() as s:
  r = await s.post("https://openrouter.ai/api/v1/chat/completions", headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}, json={"model": "mistralai/mistral-7b-instruct:free", "messages":[{"role":"user","content":f"Write a {duration} minute documentary script about {topic} in {language}. Pure narration only."}]})
  d = await r.json()
 return d["choices"][0]["message"]["content"] if "choices" in d else "Script generation failed"

async def fetch_footage(topic, duration, tmp_dir):
 clips = []
 async with aiohttp.ClientSession() as s:
  r = await s.get(f"https://api.pexels.com/videos/search?query={topic}&per_page={duration*2}", headers={"Authorization": PEXELS_API_KEY})
  d = await r.json()
  for i, v in enumerate(d.get("videos", [])[:duration*2]):
   url = v["video_files"][0]["link"]
   path = os.path.join(tmp_dir, f"clip_{i}.mp4")
   async with s.get(url) as resp:
    with open(path, "wb") as f:
     f.write(await resp.read())
   clips.append(path)
 return clips

async def generate_voice(script, tmp_dir):
 voice_id = "21m00Tcm4TlvDq8ikWAM"
 async with aiohttp.ClientSession() as s:
  r = await s.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}", headers={"xi-api-key": ELEVENLABS_API_KEY}, json={"text": script[:2500], "model_id": "eleven_monolingual_v1"})
  path = os.path.join(tmp_dir, "voice.mp3")
  with open(path, "wb") as f:
   f.write(await r.read())
 return path

def render_video(clips, audio, output, duration):
 if not clips:
  raise Exception("No footage found")
 list_file = output.replace(".mp4", "_list.txt")
 with open(list_file, "w") as f:
  for c in clips:
   f.write(f"file '{c}'\n")
 concat = output.replace(".mp4", "_concat.mp4")
 subprocess.run(["ffmpeg","-y","-f","concat","-safe","0","-i",list_file,"-t",str(duration*60),"-c","copy",concat], check=True, capture_output=True)
 subprocess.run(["ffmpeg","-y","-i",concat,"-i",audio,"-c:v","libx264","-c:a","aac","-shortest",output], check=True, capture_output=True)
async def upload_video(path, job_id):
 async with aiohttp.ClientSession() as s:
  with open(path, "rb") as f:
   data = aiohttp.FormData()
   data.add_field("file", f, filename=f"{job_id}.mp4", content_type="video/mp4")
   await s.post(f"{SUPABASE_URL}/storage/v1/object/videos/{job_id}.mp4", headers={"Authorization": f"Bearer {SUPABASE_KEY}", "x-upsert": "true"}, data=data)
 return f"{SUPABASE_URL}/storage/v1/object/public/videos/{job_id}.mp4"
