import uvicorn
import random
import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from pydantic import BaseModel

# --- DATABASE ---
SQLALCHEMY_DATABASE_URL = "sqlite:///./squad_v3.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# NOTE: No "User" class here!

class Hangout(Base):
    __tablename__ = "hangouts"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String)
    location = Column(String)
    host_username = Column(String)
    participants = relationship("Participant", back_populates="hangout", cascade="all, delete")
    messages = relationship("Message", back_populates="hangout", cascade="all, delete")

class Participant(Base):
    __tablename__ = "participants"
    id = Column(Integer, primary_key=True, index=True)
    hangout_id = Column(Integer, ForeignKey("hangouts.id"))
    username = Column(String)
    hangout = relationship("Hangout", back_populates="participants")

class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, index=True)
    hangout_id = Column(Integer, ForeignKey("hangouts.id"))
    username = Column(String)
    text = Column(String)
    hangout = relationship("Hangout", back_populates="messages")

Base.metadata.create_all(bind=engine)

# --- APP ---
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

class HangoutSchema(BaseModel):
    title: str
    location: str
    host_username: str

class JoinSchema(BaseModel):
    username: str
    hangout_id: int

class MessageSchema(BaseModel):
    username: str
    hangout_id: int
    text: str

# --- BOT BRAIN ---
BOT_IDEAS = [
    "How about Truth or Dare?",
    "Someone should bring snacks!",
    "Let's make a playlist.",
    "Who is paying for the taxi?",
    "We should take a group selfie later.",
    "Don't forget to charge your phones!",
    "Is everyone here yet?"
]

# --- ENDPOINTS ---

@app.get("/")
def read_root():
    return FileResponse("static/index.html")

@app.post("/create_hangout/")
def create_hangout(hangout: HangoutSchema):
    db = SessionLocal()
    new_h = Hangout(title=hangout.title, location=hangout.location, host_username=hangout.host_username)
    db.add(new_h)
    db.commit()
    db.add(Participant(hangout_id=new_h.id, username=hangout.host_username))
    db.commit()
    db.close()
    return {"message": "Created"}

@app.post("/join_hangout/")
def join_hangout(data: JoinSchema):
    db = SessionLocal()
    if not db.query(Participant).filter_by(hangout_id=data.hangout_id, username=data.username).first():
        db.add(Participant(hangout_id=data.hangout_id, username=data.username))
        db.commit()
    db.close()
    return {"message": "Joined"}

@app.delete("/delete_hangout/{hangout_id}")
def delete_hangout(hangout_id: int):
    db = SessionLocal()
    db.query(Hangout).filter(Hangout.id == hangout_id).delete()
    db.commit()
    db.close()
    return {"message": "Deleted"}

@app.post("/send_message/")
def send_message(msg: MessageSchema):
    db = SessionLocal()
    new_msg = Message(hangout_id=msg.hangout_id, username=msg.username, text=msg.text)
    db.add(new_msg)
    db.commit()

    if "@squadbot" in msg.text.lower():
        bot_reply = random.choice(BOT_IDEAS)
        bot_msg = Message(hangout_id=msg.hangout_id, username="SquadBot 🤖", text=bot_reply)
        db.add(bot_msg)
        db.commit()

    db.close()
    return {"message": "Sent"}

@app.get("/hangouts/")
def get_feed():
    db = SessionLocal()
    hangouts = db.query(Hangout).all()
    results = []
    for h in hangouts:
        names = [p.username for p in h.participants]
        msgs = [{"user": m.username, "text": m.text} for m in h.messages]
        results.append({
            "id": h.id,
            "title": h.title,
            "location": h.location,
            "host": h.host_username,
            "attendees": names,
            "count": len(names),
            "messages": msgs
        })
    db.close()
    return {"feed": results}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
