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

SECRET_KEY = "squad-v35-sync"
ALGORITHM = "HS256"
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")
DB_FILE = "squad_db_v18.json"

def load_db():
    if not os.path.exists(DB_FILE):
        default_db = {"users": [], "hangouts": [], "dms": []}
        with open(DB_FILE, 'w') as f: json.dump(default_db, f)
        return default_db
    try: with open(DB_FILE, 'r') as f: return json.load(f)
    except: return {"users": [], "hangouts": [], "dms": []}

def save_db(data):
    with open(DB_FILE, 'w') as f: json.dump(data, f)

app = FastAPI()
if not os.path.exists("static"): os.makedirs("static")
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- MULTI-DEVICE CONNECTION MANAGER ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[int, List[WebSocket]] = {} # Groups
        self.user_connections: Dict[str, List[WebSocket]] = {}   # Personal (Now a LIST)

    # GROUP LOGIC
    async def connect(self, websocket: WebSocket, hangout_id: int):
        await websocket.accept()
        if hangout_id not in self.active_connections: self.active_connections[hangout_id] = []
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

    # PERSONAL LOGIC (FIXED FOR MULTI-DEVICE)
    async def connect_user(self, websocket: WebSocket, username: str):
        await websocket.accept()
        if username not in self.user_connections: self.user_connections[username] = []
        self.user_connections[username].append(websocket)

    def disconnect_user(self, websocket: WebSocket, username: str):
        if username in self.user_connections:
            if websocket in self.user_connections[username]:
                self.user_connections[username].remove(websocket)

    async def send_to_user(self, username: str, message: dict):
        if username in self.user_connections:
            # Send to ALL active sockets for this user
            for connection in self.user_connections[username][:]:
                try: await connection.send_json(message)
                except: 
                    # If dead, remove it
                    if connection in self.user_connections[username]:
                        self.user_connections[username].remove(connection)

manager = ConnectionManager()

def get_hash(p): return hashlib.sha256(p.encode()).hexdigest()
def create_token(d): return jwt.encode(d, SECRET_KEY, algorithm=ALGORITHM)
async def get_current_user(token: str = Depends(oauth2_scheme)):
    try: 
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        db = load_db()
        user = next((u for u in db["users"] if u["username"] == username), None)
    except: raise HTTPException(status_code=401)
    if not user: raise HTTPException(status_code=401)
    return user

@app.get("/health")
def health(): return {"status": "ok", "version": "v35_sync"}

@app.post("/register")
def register(u: dict):
    db = load_db()
    if any(user["username"] == u['username'] for user in db["users"]): raise HTTPException(400, "Taken")
    new_user = { "username": u['username'], "hashed_password": get_hash(u['password']), "avatar_data": u.get('avatar_data'), "bio": "Just joined Squad!", "instagram": "", "is_admin": (u['username'].lower() == "qasim") }
    db["users"].append(new_user)
    save_db(db)
    return {"msg": "ok"}

@app.post("/token")
def login(f: OAuth2PasswordRequestForm = Depends()):
    db = load_db()
    user = next((u for u in db["users"] if u["username"] == f.username), None)
    if not user or user["hashed_password"] != get_hash(f.password): raise HTTPException(400, "Fail")
    return {"access_token": create_token({"sub": user["username"]}), "token_type": "bearer", "username": user["username"], "avatar": user["avatar_data"], "is_admin": user.get("is_admin", False)}

class ProfileSchema(BaseModel): bio: str; instagram: str
@app.post("/update_profile")
def update_profile(p: ProfileSchema, u: dict = Depends(get_current_user)):
    db = load_db()
    for user in db["users"]:
        if user["username"] == u["username"]:
            user["bio"] = p.bio; user["instagram"] = p.instagram
            save_db(db)
            return {"msg": "updated"}
    raise HTTPException(404)

@app.get("/get_user/{username}")
def get_user_profile(username: str):
    db = load_db()
    user = next((u for u in db["users"] if u["username"] == username), None)
    if not user: raise HTTPException(404)
    return {"username": user["username"], "avatar": user["avatar_data"], "bio": user.get("bio", ""), "instagram": user.get("instagram", ""), "is_admin": user.get("is_admin", False)}

class HangoutSchema(BaseModel): title: str; location: str; event_time: str; max_people: int; image_data: Optional[str] = None
@app.post("/create_hangout/")
def create_h(h: HangoutSchema, u: dict = Depends(get_current_user)):
    db = load_db()
    new_id = len(db["hangouts"]) + 1
    new_hangout = { "id": new_id, "title": h.title, "location": h.location, "event_time": h.event_time, "max_people": h.max_people, "host_username": u["username"], "image_data": h.image_data, "attendees": [{"username": u["username"], "avatar": u["avatar_data"], "is_admin": u.get("is_admin", False)}], "messages": [] }
    db["hangouts"].append(new_hangout)
    save_db(db)
    return {"msg": "ok"}

@app.post("/join_hangout/{id}")
def join_h(id: int, u: dict = Depends(get_current_user)):
    db = load_db()
    for h in db["hangouts"]:
        if h["id"] == id:
            if len(h["attendees"]) >= h["max_people"]: raise HTTPException(400, "Full")
            if not any(a["username"] == u["username"] for a in h["attendees"]):
                h["attendees"].append({"username": u["username"], "avatar": u["avatar_data"], "is_admin": u.get("is_admin", False)})
                save_db(db)
            break
    return {"msg": "ok"}

@app.delete("/delete_hangout/{id}")
def del_h(id: int, u: dict = Depends(get_current_user)):
    db = load_db()
    db["hangouts"] = [h for h in db["hangouts"] if not (h["id"] == id and (h["host_username"] == u["username"] or u.get("is_admin", False)))]
    save_db(db)
    return {"msg": "ok"}

@app.get("/hangouts/")
def feed(u: dict = Depends(get_current_user)):
    db = load_db()
    results = []
    for h in db["hangouts"]:
        results.append({ "id": h["id"], "title": h["title"], "location": h["location"], "event_time": h.get("event_time", "Now"), "max_people": h.get("max_people", 5), "host": h["host_username"], "image_data": h.get("image_data"), "attendees": h["attendees"], "count": len(h["attendees"]), "is_full": len(h["attendees"]) >= h.get("max_people", 5) })
    return {"feed": results}

@app.get("/chat_history/{hangout_id}")
def chat_hist(hangout_id: int):
    db = load_db()
    h = next((h for h in db["hangouts"] if h["id"] == hangout_id), None)
    return h["messages"] if h else []

# --- PRIVATE DM LOGIC (REAL-TIME) ---
class DMSchema(BaseModel): receiver: str; text: str
@app.post("/send_dm")
async def send_dm(dm: DMSchema, u: dict = Depends(get_current_user)):
    db = load_db()
    msg_obj = { "sender": u["username"], "receiver": dm.receiver, "text": dm.text, "timestamp": datetime.now().strftime("%H:%M") }
    db["dms"].append(msg_obj)
    save_db(db)
    await manager.send_to_user(dm.receiver, {"type": "dm", "sender": u["username"], "text": dm.text})
    return {"msg": "sent"}

@app.get("/my_dms")
def get_my_dms(u: dict = Depends(get_current_user)):
    db = load_db()
    my_msgs = [m for m in db["dms"] if m["sender"] == u["username"] or m["receiver"] == u["username"]]
    partners = {}
    for m in my_msgs:
        p = m["receiver"] if m["sender"] == u["username"] else m["sender"]
        partners[p] = m 
    result = []
    for p_name, last_msg in partners.items():
        p_data = next((user for user in db["users"] if user["username"] == p_name), None)
        avatar = p_data["avatar_data"] if p_data else None
        result.append({"partner": p_name, "avatar": avatar, "last_msg": last_msg["text"]})
    return result

@app.get("/dm_history/{partner}")
def dm_history(partner: str, u: dict = Depends(get_current_user)):
    db = load_db()
    msgs = [m for m in db["dms"] if (m["sender"] == u["username"] and m["receiver"] == partner) or (m["sender"] == partner and m["receiver"] == u["username"])]
    return msgs

# --- PERSONAL WEBSOCKET ---
@app.websocket("/ws/me")
async def ws_personal(websocket: WebSocket, token: str = Query(...)):
    try:
        try: payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM]); username = payload.get("sub")
        except: await websocket.close(); return
        
        await manager.connect_user(websocket, username)
        while True:
            await websocket.receive_text()
    except: manager.disconnect_user(websocket, username)

# --- GROUP WEBSOCKET ---
@app.websocket("/ws/{hangout_id}")
async def ws_endpoint(websocket: WebSocket, hangout_id: int, token: str = Query(...)):
    try:
        try: payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM]); username = payload.get("sub")
        except: await websocket.close(); return
        db = load_db()
        user = next((u for u in db["users"] if u["username"] == username), None)
        if not user: await websocket.close(); return
        
        await manager.connect(websocket, hangout_id)
        while True:
            data = await websocket.receive_text()
            is_admin = user.get("is_admin", False)
            db = load_db()
            for h in db["hangouts"]:
                if h["id"] == hangout_id:
                    h["messages"].append({"user": username, "avatar": user["avatar_data"], "text": data, "is_admin": is_admin})
                    save_db(db)
                    break
            await manager.broadcast({"type": "msg", "user": username, "avatar": user["avatar_data"], "text": data, "is_admin": is_admin}, hangout_id)
    except: manager.disconnect(websocket, hangout_id)

@app.get("/")
def root(): return FileResponse("static/index.html")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
