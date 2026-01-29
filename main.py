import uvicorn
import random
import os
import json
from datetime import datetime, timedelta
from typing import Optional, List
from fastapi import FastAPI, Depends, HTTPException, status, WebSocket, WebSocketDisconnect, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, Text, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session
from pydantic import BaseModel
from passlib.context import CryptContext
from jose import JWTError, jwt

# --- CONFIG ---
SECRET_KEY = "squad-god-mode-key"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 43200 # 30 days

# SECURITY: Use pbkdf2_sha256 to prevent crashes
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

# --- DATABASE ---
database_url = os.environ.get("DATABASE_URL")
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
if not database_url:
    database_url = "sqlite:///./squad_v4.db"

engine = create_engine(database_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- MODELS V4 ---
class User(Base):
    __tablename__ = "users_v4"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    avatar_data = Column(Text, nullable=True) # New: Profile Pic
    is_admin = Column(Boolean, default=False) # New: God Mode

class Hangout(Base):
    __tablename__ = "hangouts_v4"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String)
    location = Column(String)
    host_username = Column(String)
    image_data = Column(Text)
    participants = relationship("Participant", back_populates="hangout", cascade="all, delete")
    messages = relationship("Message", back_populates="hangout", cascade="all, delete")

class Participant(Base):
    __tablename__ = "participants_v4"
    id = Column(Integer, primary_key=True, index=True)
    hangout_id = Column(Integer, ForeignKey("hangouts_v4.id"))
    username = Column(String)
    user_avatar = Column(Text, nullable=True) # Cache avatar for fast loading
    hangout = relationship("Hangout", back_populates="participants")

class Message(Base):
    __tablename__ = "messages_v4"
    id = Column(Integer, primary_key=True, index=True)
    hangout_id = Column(Integer, ForeignKey("hangouts_v4.id"))
    username = Column(String)
    user_avatar = Column(Text, nullable=True)
    text = Column(String)
    hangout = relationship("Hangout", back_populates="messages")

Base.metadata.create_all(bind=engine)

# --- APP ---
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- WEBSOCKET MANAGER ---
class ConnectionManager:
    def __init__(self):
        # Dictionary to hold list of connections per hangout_id
        self.active_connections: dict[int, List[WebSocket]] = {}

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
            # Clean dead connections
            for connection in self.active_connections[hangout_id][:]:
                try:
                    await connection.send_json(message)
                except:
                    self.disconnect(connection, hangout_id)

manager = ConnectionManager()

# --- SCHEMAS ---
class RegisterSchema(BaseModel):
    username: str
    password: str
    avatar_data: Optional[str] = None

class HangoutSchema(BaseModel):
    title: str
    location: str
    image_data: Optional[str] = None

# --- HELPERS ---
def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

def verify_password(plain, hashed): return pwd_context.verify(plain, hashed)
def get_hash(password): return pwd_context.hash(password)

def create_token(data: dict):
    to_encode = data.copy()
    to_encode.update({"exp": datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if not username: raise HTTPException(status_code=401)
    except JWTError: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.username == username).first()
    if not user: raise HTTPException(status_code=401)
    return user

# --- ENDPOINTS ---

@app.post("/register")
def register(user_data: RegisterSchema, db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == user_data.username).first():
        raise HTTPException(status_code=400, detail="Username taken")
    
    # GOD MODE CHECK: If name is Qasim, make Admin
    is_admin = (user_data.username.lower() == "qasim")
    
    new_user = User(
        username=user_data.username, 
        hashed_password=get_hash(user_data.password),
        avatar_data=user_data.avatar_data,
        is_admin=is_admin
    )
    db.add(new_user)
    db.commit()
    return {"msg": "User created", "is_admin": is_admin}

@app.post("/token")
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Incorrect")
    
    token = create_token({"sub": user.username})
    return {"access_token": token, "token_type": "bearer", "username": user.username, "avatar": user.avatar_data, "is_admin": user.is_admin}

@app.post("/create_hangout/")
def create_hangout(hangout: HangoutSchema, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    new_h = Hangout(title=hangout.title, location=hangout.location, host_username=user.username, image_data=hangout.image_data)
    db.add(new_h)
    db.commit()
    # Host auto-joins
    db.add(Participant(hangout_id=new_h.id, username=user.username, user_avatar=user.avatar_data))
    db.commit()
    return {"msg": "Created"}

@app.post("/join_hangout/{hangout_id}")
def join_hangout(hangout_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not db.query(Participant).filter_by(hangout_id=hangout_id, username=user.username).first():
        db.add(Participant(hangout_id=hangout_id, username=user.username, user_avatar=user.avatar_data))
        db.commit()
    return {"msg": "Joined"}

@app.delete("/delete_hangout/{hangout_id}")
def delete_hangout(hangout_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    hangout = db.query(Hangout).filter(Hangout.id == hangout_id).first()
    if not hangout: return {"msg": "Not found"}
    
    # PERMISSION CHECK: Host OR Admin can delete
    if hangout.host_username == user.username or user.is_admin:
        db.delete(hangout)
        db.commit()
        return {"msg": "Deleted"}
    raise HTTPException(status_code=403, detail="Not authorized")

@app.get("/hangouts/")
def get_feed(db: Session = Depends(get_db)):
    hangouts = db.query(Hangout).all()
    results = []
    for h in hangouts:
        # Get participants with avatars
        attendees = [{"name": p.username, "avatar": p.user_avatar} for p in h.participants]
        # Get messages with avatars
        msgs = [{"user": m.username, "avatar": m.user_avatar, "text": m.text} for m in h.messages]
        results.append({
            "id": h.id, "title": h.title, "location": h.location, "host": h.host_username,
            "image_data": h.image_data, "attendees": attendees, "count": len(attendees), "messages": msgs
        })
    return {"feed": results}

# --- REAL-TIME WEBSOCKET ---
@app.websocket("/ws/{hangout_id}")
async def websocket_endpoint(websocket: WebSocket, hangout_id: int, token: str = Query(...), db: Session = Depends(get_db)):
    # Authenticate via Token in Query Param
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username: return
    except:
        await websocket.close()
        return

    # Find User for Avatar
    user = db.query(User).filter(User.username == username).first()
    avatar = user.avatar_data if user else None

    await manager.connect(websocket, hangout_id)
    try:
        while True:
            data = await websocket.receive_text()
            # Save to DB
            msg_entry = Message(hangout_id=hangout_id, username=username, user_avatar=avatar, text=data)
            db.add(msg_entry)
            db.commit()
            
            # Broadcast to everyone live
            await manager.broadcast({
                "user": username,
                "avatar": avatar,
                "text": data
            }, hangout_id)
            
            # Bot Check
            if "@squadbot" in data.lower():
                reply = random.choice(["Truth or Dare?", "Who's buying drinks?", "Selfie time!", "Drop a pin!", "Music?"])
                bot_msg = Message(hangout_id=hangout_id, username="SquadBot 🤖", text=reply)
                db.add(bot_msg)
                db.commit()
                await manager.broadcast({"user": "SquadBot 🤖", "text": reply}, hangout_id)

    except WebSocketDisconnect:
        manager.disconnect(websocket, hangout_id)

@app.get("/")
def read_root(): return FileResponse("static/index.html")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
