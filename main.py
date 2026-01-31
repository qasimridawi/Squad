import uvicorn
import random
import os
import json
import jwt
import hashlib
from datetime import datetime
from typing import Optional, List, Dict
from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, Text, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session
from pydantic import BaseModel

# --- CONFIG ---
SECRET_KEY = "squad-v13-final-fix"
ALGORITHM = "HS256"
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

# --- DATABASE ---
database_url = "sqlite:///./squad_v13.db"
engine = create_engine(database_url, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- MODELS ---
class User(Base):
    __tablename__ = "users_v13"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    avatar_data = Column(Text, nullable=True)
    is_admin = Column(Boolean, default=False)

class Hangout(Base):
    __tablename__ = "hangouts_v13"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String)
    location = Column(String)
    host_username = Column(String)
    image_data = Column(Text, nullable=True)
    video_data = Column(Text, nullable=True)
    likes_data = Column(Text, default="[]") 
    participants = relationship("Participant", back_populates="hangout", cascade="all, delete")
    messages = relationship("Message", back_populates="hangout", cascade="all, delete")

class Participant(Base):
    __tablename__ = "participants_v13"
    id = Column(Integer, primary_key=True, index=True)
    hangout_id = Column(Integer, ForeignKey("hangouts_v13.id"))
    username = Column(String)
    user_avatar = Column(Text, nullable=True)
    hangout = relationship("Hangout", back_populates="participants")

class Message(Base):
    __tablename__ = "messages_v13"
    id = Column(Integer, primary_key=True, index=True)
    hangout_id = Column(Integer, ForeignKey("hangouts_v13.id"))
    username = Column(String)
    user_avatar = Column(Text, nullable=True)
    text = Column(String)
    hangout = relationship("Hangout", back_populates="messages")

class DirectMessage(Base):
    __tablename__ = "direct_messages_v13"
    id = Column(Integer, primary_key=True, index=True)
    sender = Column(String)
    receiver = Column(String)
    text = Column(String)
    timestamp = Column(String)

try: Base.metadata.create_all(bind=engine)
except: pass

# --- APP ---
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- WEBSOCKET MANAGER ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[int, List[WebSocket]] = {}
        self.online_users: set = set()

    async def connect(self, websocket: WebSocket, hangout_id: int, username: str):
        await websocket.accept()
        if hangout_id not in self.active_connections:
            self.active_connections[hangout_id] = []
        self.active_connections[hangout_id].append(websocket)
        self.online_users.add(username)
        await self.broadcast_status(hangout_id)

    def disconnect(self, websocket: WebSocket, hangout_id: int, username: str):
        if hangout_id in self.active_connections:
            if websocket in self.active_connections[hangout_id]:
                self.active_connections[hangout_id].remove(websocket)
        if username in self.online_users:
            self.online_users.remove(username)
        
    async def broadcast(self, message: dict, hangout_id: int):
        if hangout_id in self.active_connections:
            for connection in self.active_connections[hangout_id][:]:
                try: await connection.send_json(message)
                except: pass

    async def broadcast_status(self, hangout_id: int):
        msg = {"type": "status", "online_users": list(self.online_users)}
        await self.broadcast(msg, hangout_id)

manager = ConnectionManager()

# --- HELPERS (Standard Lib Only) ---
def get_db():
    db = SessionLocal(); try: yield db; finally: db.close()

def get_hash(p): return hashlib.sha256(p.encode()).hexdigest()
def verify_password(p, h): return get_hash(p) == h
def create_token(d): return jwt.encode(d, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    try: payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM]); user = db.query(User).filter(User.username == payload.get("sub")).first()
    except: raise HTTPException(status_code=401)
    if not user: raise HTTPException(status_code=401)
    return user

# --- ENDPOINTS ---
@app.get("/health")
def health(): return {"status": "ok", "version": "v13"}

@app.post("/register")
def register(u: dict, db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == u['username']).first(): raise HTTPException(400, "Taken")
    db.add(User(username=u['username'], hashed_password=get_hash(u['password']), avatar_data=u.get('avatar_data'), is_admin=(u['username'].lower()=="qasim")))
    db.commit()
    return {"msg": "ok"}

@app.post("/token")
def login(f: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == f.username).first()
    if not user or not verify_password(f.password, user.hashed_password): raise HTTPException(400, "Fail")
    return {"access_token": create_token({"sub": user.username}), "token_type": "bearer", "username": user.username, "avatar": user.avatar_data, "is_admin": user.is_admin}

class HangoutSchema(BaseModel):
    title: str; location: str; image_data: Optional[str] = None; video_data: Optional[str] = None

@app.post("/create_hangout/")
def create_h(h: HangoutSchema, u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    new = Hangout(title=h.title, location=h.location, host_username=u.username, image_data=h.image_data, video_data=h.video_data)
    db.add(new); db.commit()
    db.add(Participant(hangout_id=new.id, username=u.username, user_avatar=u.avatar_data)); db.commit()
    return {"msg": "ok"}

@app.post("/like_hangout/{hangout_id}")
def like_h(hangout_id: int, u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    h = db.query(Hangout).filter(Hangout.id == hangout_id).first()
    if h:
        likes = json.loads(h.likes_data)
        if u.username in likes: likes.remove(u.username)
        else: likes.append(u.username)
        h.likes_data = json.dumps(likes)
        db.commit()
    return {"msg": "ok"}

@app.post("/join_hangout/{id}")
def join_h(id: int, u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not db.query(Participant).filter_by(hangout_id=id, username=u.username).first():
        db.add(Participant(hangout_id=id, username=u.username, user_avatar=u.avatar_data)); db.commit()
    return {"msg": "ok"}

@app.delete("/delete_hangout/{id}")
def del_h(id: int, u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    h = db.query(Hangout).filter(Hangout.id == id).first()
    if h and (h.host_username == u.username or u.is_admin): db.delete(h); db.commit()
    return {"msg": "ok"}

class DMSchema(BaseModel):
    receiver: str; text: str

@app.post("/send_dm/")
def send_dm(dm: DMSchema, u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == dm.receiver).first():
        db.add(DirectMessage(sender=u.username, receiver=dm.receiver, text=dm.text, timestamp=datetime.now().strftime("%H:%M")))
        db.commit()
    return {"msg": "sent"}

@app.get("/get_dms/")
def get_dms(u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    msgs = db.query(DirectMessage).filter((DirectMessage.sender == u.username) | (DirectMessage.receiver == u.username)).all()
    return [{"sender": m.sender, "receiver": m.receiver, "text": m.text, "time": m.timestamp} for m in msgs]

@app.get("/hangouts/")
def feed(db: Session = Depends(get_db)):
    hangouts = db.query(Hangout).all()
    results = []
    for h in hangouts:
        attendees = [{"name": p.username, "avatar": p.user_avatar} for p in h.participants]
        results.append({
            "id": h.id, "title": h.title, "location": h.location, "host": h.host_username,
            "image_data": h.image_data, "video_data": h.video_data,
            "attendees": attendees, "count": len(attendees),
            "likes": len(json.loads(h.likes_data)), "liked_by_me": False
        })
    return {"feed": results}

@app.get("/chat_history/{hangout_id}")
def chat_hist(hangout_id: int, u: User = Depends(get_current_user), db: Session = Depends(get_db)):
    h = db.query(Hangout).filter(Hangout.id == hangout_id).first()
    if not h: return []
    return [{"user": m.username, "avatar": m.user_avatar, "text": m.text} for m in h.messages]

@app.websocket("/ws/{hangout_id}")
async def ws_endpoint(websocket: WebSocket, hangout_id: int, token: str = Query(...)):
    db = SessionLocal() 
    try:
        try: payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM]); username = payload.get("sub")
        except: await websocket.close(code=1008); return
            
        user = db.query(User).filter(User.username == username).first()
        if not user: await websocket.close(code=1008); return

        avatar = user.avatar_data
        await manager.connect(websocket, hangout_id, username)
        
        while True:
            data = await websocket.receive_text()
            db.add(Message(hangout_id=hangout_id, username=username, user_avatar=avatar, text=data))
            db.commit()
            await manager.broadcast({"type": "msg", "user": username, "avatar": avatar, "text": data}, hangout_id)
            if "@squadbot" in data.lower():
                reply = random.choice(["Truth or Dare?", "Who's buying?", "Drop a pin!", "Music?"])
                db.add(Message(hangout_id=hangout_id, username="SquadBot 🤖", text=reply)); db.commit()
                await manager.broadcast({"type": "msg", "user": "SquadBot 🤖", "text": reply}, hangout_id)

    except WebSocketDisconnect:
        manager.disconnect(websocket, hangout_id, username)
        await manager.broadcast_status(hangout_id)
    except Exception as e:
        try: await websocket.close()
        except: pass
    finally: db.close()

@app.get("/")
def root(): return FileResponse("static/index.html")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
