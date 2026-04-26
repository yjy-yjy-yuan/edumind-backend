from fastapi import FastAPI
from pydantic import BaseModel
from typing import Any, Dict, List
import uvicorn

app = FastAPI(title='Vinci Mock', version='1.0.0')

class InferReq(BaseModel):
    question: str = ''
    history: List[Any] = []
    session_id: str = ''
    timestamp: float = 0
    silent: bool = False
    frames: List[Any] = []
    base64_frames: List[str] = []

@app.get('/health')
def health() -> Dict[str, Any]:
    return {'status': 'ok', 'service': 'vinci-mock', 'reachable': True}

@app.post('/api/v1/inference/internvl')
def internvl(req: InferReq) -> Dict[str, Any]:
    text = '已收到画面请求（VINCI mock）'
    if req.question:
        text = f"{text}：{req.question[:120]}"
    return {
        'answer': text,
        'history': req.history,
        'session_id': req.session_id or 'health_check',
        'trace_id': 'vinci-mock',
        'degraded': False,
    }

@app.post('/api/v1/chat')
def chat(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        'answer': 'vinci mock chat ok',
        'history': payload.get('history', []),
        'session_id': payload.get('session_id', ''),
        'trace_id': 'vinci-mock',
        'degraded': False,
    }

if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=8010)
