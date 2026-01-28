import uvicorn 
from fastapi import FastAPI 
from sqlalchemy import create_engine, Column, Integer, String 
from sqlalchemy.orm import declarative_base, sessionmaker 
 
# DATABASE SETUP 
SQLALCHEMY_DATABASE_URL = "sqlite:///./squad.db" 
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}) 
SessionLocal = sessionmaker(bind=engine) 
Base = declarative_base() 
 
# MODELS 
class User(Base): 
    tablename = "users" 
    id = Column(Integer, primary_key=True, index=True) 
    username = Column(String, unique=True, index=True) 
 
Base.metadata.create_all(bind=engine) 
 
# APP 
app = FastAPI() 
@app.get("/") 
def read_root(): 
    return {"message": "IT FINALLY WORKS! SQUAD IS LIVE."} 
 
if name == "main": 
    uvicorn.run(app, host="127.0.0.1", port=8000) 
