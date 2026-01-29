import uvicorn
import random
import os
from datetime import datetime, timedelta
from typing import Optional
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, Text
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session
from pydantic import BaseModel
from passlib.context import CryptContext
from jose import JWTError, jwt

# --- SECURITY CONFIG ---
SECRET_KEY = "squad-secret-key-change-me"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 10080 

# FIXED: Using pbkdf2_sha256 which is built-in and won't crash
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

# --- DATABASE ---
database_url = os.environ.get("DATABASE_URL")
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
if not database_url:
    database_url = "sqlite:///./squad_v3.db"

engine = create_engine(database_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- MODELS ---
class User(Base):
    __tablename__ = "users_v3"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String)

class Hangout(Base):
    __tablename__ = "hangouts_v3"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String)
    location = Column(String)
    host_username = Column(String)
    image_data = Column(Text)
    participants = relationship("Participant", back_populates="hangout", cascade="all, delete")
    messages = relationship("Message", back_populates="hangout", cascade="all, delete")

class Participant(Base):
    __tablename__ = "participants_v3"
    id = Column(Integer, primary_key=True, index=True)
    hangout_id = Column(Integer, ForeignKey("hangouts_v3.id"))
    username = Column(String)
    hangout = relationship("Hangout", back_populates="participants")

class Message(Base):
    __tablename__ = "messages_v3"
    id = Column(Integer, primary_key=True, index=True)
    hangout_id = Column(Integer, ForeignKey("hangouts_v3.id"))
    username = Column(String)
    text = Column(String)
    hangout = relationship("Hangout", back_populates="messages")

Base.metadata.create_all(bind=engine)

# --- APP ---
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- SCHEMAS ---
class UserSchema(BaseModel):
    username: str
    password: str

class HangoutSchema(BaseModel):
    title: str
    location: str
    image_data: Optional[str] = None

class MessageSchema(BaseModel):
    hangout_id: int
    text: str

# --- HELPERS ---
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None: raise HTTPException(status_code=401)
    except JWTError:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.username == username).first()
    if user is None: raise HTTPException(status_code=401)
    return user

# --- ENDPOINTS ---

@app.post("/register")
def register(user: UserSchema, db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == user.username).first():
        raise HTTPException(status_code=400, detail="Username taken")
    new_user = User(username=user.username, hashed_password=get_password_hash(user.password))
    db.add(new_user)
    db.commit()
    return {"msg": "User created"}

@app.post("/token")
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Incorrect username or password")
    access_token = create_access_token(data={"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer"}

@app.post("/create_hangout/")
def create_hangout(hangout: HangoutSchema, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    new_h = Hangout(title=hangout.title, location=hangout.location, host_username=user.username, image_data=hangout.image_data)
    db.add(new_h)
    db.commit()
    db.add(Participant(hangout_id=new_h.id, username=user.username))
    db.commit()
    return {"msg": "Created"}

@app.post("/join_hangout/{hangout_id}")
def join_hangout(hangout_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not db.query(Participant).filter_by(hangout_id=hangout_id, username=user.username).first():
        db.add(Participant(hangout_id=hangout_id, username=user.username))
        db.commit()
    return {"msg": "Joined"}

@app.delete("/delete_hangout/{hangout_id}")
def delete_hangout(hangout_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    hangout = db.query(Hangout).filter(Hangout.id == hangout_id).first()
    if hangout and hangout.host_username == user.username:
        db.delete(hangout)
        db.commit()
    return {"msg": "Deleted"}

@app.post("/send_message/")
def send_message(msg: MessageSchema, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.add(Message(hangout_id=msg.hangout_id, username=user.username, text=msg.text))
    db.commit()
    if "@squadbot" in msg.text.lower():
        bot_reply = random.choice(["Truth or Dare?", "Snacks?", "Selfie time!", "ETA?", "Music?"])
        db.add(Message(hangout_id=msg.hangout_id, username="SquadBot 🤖", text=bot_reply))
        db.commit()
    return {"msg": "Sent"}

@app.get("/hangouts/")
def get_feed(db: Session = Depends(get_db)):
    hangouts = db.query(Hangout).all()
    results = []
    for h in hangouts:
        names = [p.username for p in h.participants]
        msgs = [{"user": m.username, "text": m.text} for m in h.messages]
        results.append({
            "id": h.id, "title": h.title, "location": h.location, "host": h.host_username,
            "image_data": h.image_data, "attendees": names, "count": len(names), "messages": msgs
        })
    return {"feed": results}

@app.get("/")
def read_root(): return FileResponse("static/index.html")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
