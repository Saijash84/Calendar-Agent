# main.py
from agent import handle_user_message
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from agent import handle_user_message
from database import init_db

app = FastAPI()
init_db()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatRequest(BaseModel):
    message: str

class ChatResponse(BaseModel):
    response: dict

@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(chat_request: ChatRequest):
    try:
        result = handle_user_message(chat_request.message)
        return ChatResponse(response=result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return ChatResponse(response={"error": str(e)})
