import uvicorn
import random
import os
import json
import jwt
import hashlib
from datetime import datetime
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel

# --- CONFIG ---
SECRET_KEY = "squad-v18-moscow-mode"
ALGORITHM = "HS256"
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")
DB_FILE = "squad_db_v18.json"

# --- JSON DATABASE ENGINE ---
def load_db():
    if not os.path.exists(DB_FILE):
        default_db = {"users": [], "hangouts": [], "dms": []}
        with open(DB_FILE, 'w') as f: json.dump(default_db, f)
        return default_db
    try:
        with open(DB_FILE, 'r') as f: return json.load(f)
    except:
        return {"users": [], "hangouts": [], "dms": []}

def save_db(data):
    with open(DB_FILE, 'w') as f: json.dump(data, f)

# --- APP ---
app = FastAPI()

if not os.path.exists("static"): os.makedirs("static")
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- WEBSOCKET MANAGER ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[int, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, hangout_id: int):
        await websocket.accept()
        if hangout_id not in self.active_connections:
            self.active_connections[hangout_id] = []
        self.active_connections[hangout_id].append(websocket)

    def disconnect(self, websocket: WebSocket, hangout_id: int):
        if hangout_id in self.active_connections:
            if websocket in self.active_connections[hangout_id]:
                self.active_connections[hangout_id].remove(websocket)
        
    async def broadcast(self, message: dict, hangout_id: int):
        if hangout_id in self.active_connections:
            for connection in self.active_connections[hangout_id][:]:
                try: await connection.send_json(message)
                except: pass

manager = ConnectionManager()

# --- HELPERS ---
def get_hash(p): return hashlib.sha256(p.encode()).hexdigest()
def create_token(d): return jwt.encode(d, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(token: str = Depends(oauth2_scheme)):
    try: 
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        db = load_db()
        user = next((u for u in db["users"] if u["username"] == username), None)
    except: 
        raise HTTPException(status_code=401)
    if not user: raise HTTPException(status_code=401)
    return user

# --- ENDPOINTS ---
@app.get("/health")
def health(): return {"status": "ok", "version": "v18_event"}

@app.post("/register")
def register(u: dict):
    db = load_db()
    if any(user["username"] == u['username'] for user in db["users"]):
        raise HTTPException(400, "Taken")
    
    new_user = {
        "username": u['username'],
        "hashed_password": get_hash(u['password']),
        "avatar_data": u.get('avatar_data'),
        "is_admin": (u['username'].lower() == "qasim")
    }
    db["users"].append(new_user)
    save_db(db)
    return {"msg": "ok"}

@app.post("/token")
def login(f: OAuth2PasswordRequestForm = Depends()):
    db = load_db()
    user = next((u for u in db["users"] if u["username"] == f.username), None)
    if not user or user["hashed_password"] != get_hash(f.password):
        raise HTTPException(400, "Fail")
    return {
        "access_token": create_token({"sub": user["username"]}), 
        "token_type": "bearer", 
        "username": user["username"], 
        "avatar": user["avatar_data"], 
        "is_admin": user["is_admin"]
    }

# NEW SCHEMA: No Video, Added Time/Max People
class HangoutSchema(BaseModel):
    title: str
    location: str
    event_time: str
    max_people: int
    image_data: Optional[str] = None

@app.post("/create_hangout/")
def create_h(h: HangoutSchema, u: dict = Depends(get_current_user)):
    db = load_db()
    new_id = len(db["hangouts"]) + 1
    new_hangout = {
        "id": new_id,
        "title": h.title,
        "location": h.location,
        "event_time": h.event_time,
        "max_people": h.max_people,
        "host_username": u["username"],
        "image_data": h.image_data,
        "likes": [],
        "attendees": [{"username": u["username"], "avatar": u["avatar_data"]}],
        "messages": []
    }
    db["hangouts"].append(new_hangout)
    save_db(db)
    return {"msg": "ok"}

@app.post("/like_hangout/{hangout_id}")
def like_h(hangout_id: int, u: dict = Depends(get_current_user)):
    db = load_db()
    for h in db["hangouts"]:
        if h["id"] == hangout_id:
            if u["username"] in h["likes"]: h["likes"].remove(u["username"])
            else: h["likes"].append(u["username"])
            save_db(db)
            break
    return {"msg": "ok"}

@app.post("/join_hangout/{id}")
def join_h(id: int, u: dict = Depends(get_current_user)):
    db = load_db()
    for h in db["hangouts"]:
        if h["id"] == id:
            # Check if full
            if len(h["attendees"]) >= h["max_people"]:
                raise HTTPException(400, "Full")
                
            if not any(a["username"] == u["username"] for a in h["attendees"]):
                h["attendees"].append({"username": u["username"], "avatar": u["avatar_data"]})
                save_db(db)
            break
    return {"msg": "ok"}

@app.delete("/delete_hangout/{id}")
def del_h(id: int, u: dict = Depends(get_current_user)):
    db = load_db()
    original_len = len(db["hangouts"])
    db["hangouts"] = [h for h in db["hangouts"] if not (h["id"] == id and (h["host_username"] == u["username"] or u["is_admin"]))]
    if len(db["hangouts"]) < original_len:
        save_db(db)
    return {"msg": "ok"}

class DMSchema(BaseModel):
    receiver: str; text: str

@app.post("/send_dm/")
def send_dm(dm: DMSchema, u: dict = Depends(get_current_user)):
    db = load_db()
    if any(user["username"] == dm.receiver for user in db["users"]):
        new_dm = {
            "sender": u["username"],
            "receiver": dm.receiver,
            "text": dm.text,
            "timestamp": datetime.now().strftime("%H:%M")
        }
        db["dms"].append(new_dm)
        save_db(db)
    return {"msg": "sent"}

@app.get("/get_dms/")
def get_dms(u: dict = Depends(get_current_user)):
    db = load_db()
    my_dms = [m for m in db["dms"] if m["sender"] == u["username"] or m["receiver"] == u["username"]]
    return my_dms

@app.get("/hangouts/")
def feed(u: dict = Depends(get_current_user)):
    db = load_db()
    results = []
    for h in db["hangouts"]:
        results.append({
            "id": h["id"], "title": h["title"], "location": h["location"],
            "event_time": h.get("event_time", "Now"), 
            "max_people": h.get("max_people", 5),
            "host": h["host_username"],
            "image_data": h.get("image_data"),
            "attendees": h["attendees"], 
            "count": len(h["attendees"]),
            "is_full": len(h["attendees"]) >= h.get("max_people", 5),
            "likes": len(h["likes"]), 
            "liked_by_me": (u["username"] in h["likes"])
        })
    return {"feed": results}

@app.get("/chat_history/{hangout_id}")
def chat_hist(hangout_id: int, u: dict = Depends(get_current_user)):
    db = load_db()
    h = next((h for h in db["hangouts"] if h["id"] == hangout_id), None)
    if not h: return []
    return h["messages"]

@app.websocket("/ws/{hangout_id}")
async def ws_endpoint(websocket: WebSocket, hangout_id: int, token: str = Query(...)):
    try:
        try: payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM]); username = payload.get("sub")
        except: await websocket.close(code=1008); return
            
        db = load_db()
        user = next((u for u in db["users"] if u["username"] == username), None)
        if not user: await websocket.close(code=1008); return

        avatar = user["avatar_data"]
        await manager.connect(websocket, hangout_id)
        
        while True:
            data = await websocket.receive_text()
            db = load_db()
            for h in db["hangouts"]:
                if h["id"] == hangout_id:
                    msg_obj = {"user": username, "avatar": avatar, "text": data}
                    h["messages"].append(msg_obj)
                    save_db(db)
                    break
            
            await manager.broadcast({"type": "msg", "user": username, "avatar": avatar, "text": data}, hangout_id)
            
            if "@squadbot" in data.lower():
                reply = random.choice(["I'm down!", "What time?", "Send location!", "Anyone else coming?"])
                for h in db["hangouts"]:
                    if h["id"] == hangout_id:
                        h["messages"].append({"user": "SquadBot 🤖", "text": reply})
                        save_db(db)
                        break
                await manager.broadcast({"type": "msg", "user": "SquadBot 🤖", "text": reply}, hangout_id)

    except WebSocketDisconnect:
        manager.disconnect(websocket, hangout_id)
    except Exception as e:
        try: await websocket.close()
        except: pass

@app.get("/")
def root(): return FileResponse("static/index.html")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
