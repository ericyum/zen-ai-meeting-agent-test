from __future__ import annotations

import argparse
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .runtime import MeetingAgentRuntime, interrupt_payload


def encode_sse(event: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n".encode("utf-8")


def public_error_event(_error: Exception) -> dict[str, str]:
    return {"type": "error", "message": "요청을 처리하지 못했습니다."}


HTML = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>ZEN AI 회의록 Agent POC Trace</title>
<style>
:root{color-scheme:dark;--bg:#0b1020;--panel:#121a2f;--line:#293553;--accent:#77e0b5;--blue:#7fb3ff;--warn:#ffc86b;--text:#edf3ff;--muted:#91a0bb}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(135deg,#0b1020,#11182b);color:var(--text);font-family:system-ui,"Malgun Gothic",sans-serif}
header{padding:20px 28px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center}h1{font-size:20px;margin:0}.badge{color:var(--accent);font-size:13px}
main{display:grid;grid-template-columns:minmax(340px,42%) 1fr;gap:18px;padding:18px;height:calc(100vh - 70px)}.panel{background:rgba(18,26,47,.92);border:1px solid var(--line);border-radius:14px;overflow:hidden}.left{display:flex;flex-direction:column}.messages{flex:1;overflow:auto;padding:18px}.msg{padding:12px 14px;margin:8px 0;border-radius:12px;white-space:pre-wrap;line-height:1.5}.user{background:#253454;margin-left:12%}.agent{background:#16352f;margin-right:8%}.system{background:#352d1d;color:#ffe1a6;font-size:13px}
.composer{padding:14px;border-top:1px solid var(--line);display:flex;gap:8px}input{flex:1;background:#0b1224;color:white;border:1px solid #405074;border-radius:9px;padding:12px;font-size:15px}button{border:0;border-radius:9px;background:var(--accent);color:#062218;font-weight:700;padding:0 16px;cursor:pointer}button:disabled{opacity:.45}.hint{padding:0 16px 12px;color:var(--muted);font-size:12px}
.traceHead{padding:14px 18px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;gap:12px;align-items:center}.traceTools{display:flex;gap:14px;align-items:center;font-size:12px;color:var(--muted)}.traceTools label{display:flex;gap:6px;align-items:center}.traceTools input{width:auto}.trace{height:calc(100% - 52px);overflow:auto;padding:14px}.event{border-left:3px solid var(--blue);background:#0d1529;margin:8px 0;padding:10px 12px;border-radius:0 9px 9px 0;animation:in .25s ease}.event.sub{margin-left:24px;border-color:var(--accent)}.event.state{border-color:var(--warn)}.event.business{border-color:#ff79c6;background:#21152b}.title{font-weight:700}.meta{color:var(--muted);font-size:12px;margin-top:3px}.edge{color:var(--warn)}pre{font-size:11px;color:#bdc9df;white-space:pre-wrap;max-height:170px;overflow:auto;margin:8px 0 0}@keyframes in{from{opacity:0;transform:translateY(6px)}}.spinner{display:none;color:var(--accent)}@media(max-width:850px){main{grid-template-columns:1fr;height:auto}.panel{min-height:500px}}
</style></head><body>
<header><h1>회의록 Agent · LangGraph 실행 관찰</h1><span class="badge">● Python + LangGraph POC</span></header>
<main><section class="panel left"><div id="messages" class="messages"><div class="msg system">명령 예시
/meeting-start · /meeting-pause · /meeting-resume · /meeting-stop
meeting-001 회의록을 가져오고 결정 사항을 설명해줘
회의록 검색해줘 (복수 후보 HITL)
/select meeting-001 · /merge add</div></div><div class="hint">실제 DeepSeek은 응답까지 10~40초 정도 걸릴 수 있습니다.</div><form id="form" class="composer"><input id="input" autocomplete="off" placeholder="명령 또는 회의록 질문"><button id="send">실행</button></form></section>
<section class="panel"><div class="traceHead"><strong>실제 실행 Trace</strong><div class="traceTools"><label><input id="showSnapshots" type="checkbox"> 기술 State Snapshot 표시</label><span id="spinner" class="spinner">DeepSeek / Graph 실행 중…</span></div></div><div id="trace" class="trace"></div></section></main>
<script>
const form=document.querySelector('#form'), input=document.querySelector('#input'), send=document.querySelector('#send'), messages=document.querySelector('#messages'), trace=document.querySelector('#trace'), spinner=document.querySelector('#spinner'), showSnapshots=document.querySelector('#showSnapshots');
const addMsg=(text,kind)=>{const d=document.createElement('div');d.className='msg '+kind;d.textContent=text;messages.appendChild(d);messages.scrollTop=messages.scrollHeight};
const esc=s=>String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
function appendTrace(e){if(e.type==='state'&&!showSnapshots.checked)return;const business=e.type==='business_checkpoint';const d=document.createElement('div');d.className='event '+(e.graph!=='Agent Graph'?'sub ':'')+(e.type==='state'?'state ':' )+(business?'business ':' );let detail='';if(e.type==='state'||business)detail=`<span class="edge">${esc(e.edge)}</span><pre>${esc(JSON.stringify(e.state,null,2))}</pre>`;else{detail=`${e.phase==='start'?'NODE 시작':'NODE 완료'}${e.decision?` · <strong class="edge">Agent State 판단: ${esc(e.decision)}</strong>`:''}${e.edge?` · <span class="edge">${esc(e.edge)}</span>`:''}${e.output&&Object.keys(e.output).length?`<pre>${esc(JSON.stringify(e.output,null,2))}</pre>`:''}`}const label=e.type==='state'?'기술 State Snapshot (LangGraph 복구용)':business?'업무 Checkpoint 갱신':(e.node??'Node');d.innerHTML=`<div class="title">${esc(e.graph)} · ${esc(label)}</div><div class="meta">${e.step!==undefined?`step ${esc(e.step)} · `:''}${detail}</div>`;trace.appendChild(d);trace.scrollTop=trace.scrollHeight}
form.addEventListener('submit',async ev=>{ev.preventDefault();const command=input.value.trim();if(!command)return;addMsg(command,'user');input.value='';trace.innerHTML='';send.disabled=true;spinner.style.display='inline';try{const res=await fetch('/api/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({command})});if(!res.ok)throw new Error(await res.text()||'실행 실패');const reader=res.body.getReader(),decoder=new TextDecoder();let buffer='';while(true){const {value,done}=await reader.read();buffer+=decoder.decode(value||new Uint8Array(),{stream:!done});const frames=buffer.split('\n\n');buffer=frames.pop();for(const frame of frames){const line=frame.split('\n').find(item=>item.startsWith('data: '));if(!line)continue;const data=JSON.parse(line.slice(6));if(data.type==='final'){addMsg(data.response||'(응답 없음)','agent');if(data.interrupt)addMsg(`HITL: ${data.interrupt.message}\n${JSON.stringify(data.interrupt,null,2)}`,'system')}else if(data.type==='error'){throw new Error(data.message)}else appendTrace(data)}if(done)break}}catch(e){addMsg('오류: '+e.message,'system')}finally{send.disabled=false;spinner.style.display='none';input.focus()}});input.focus();
</script></body></html>"""


def serve(runtime: MeetingAgentRuntime, host: str, port: int, open_browser: bool = True) -> None:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            print(f"[WEB] {self.address_string()} - {format % args}")

        def _json(self, status: int, body: dict[str, Any]) -> None:
            data = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

        def do_GET(self) -> None:
            if self.path != "/": self.send_error(404); return
            data = HTML.encode("utf-8"); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

        def do_POST(self) -> None:
            if self.path != "/api/command": self.send_error(404); return
            try:
                length = int(self.headers.get("Content-Length", "0")); body = json.loads(self.rfile.read(length) or b"{}")
                command = str(body.get("command", "")).strip(); thread_id = "presentation-thread"; user_id = "user-eric"
                aliases = {"/meeting-start": "start", "/meeting-pause": "pause", "/meeting-resume": "resume", "/meeting-stop": "stop"}
                if command in aliases:
                    events = runtime.iter_recording_traced(
                        user_id, thread_id, aliases[command]
                    )
                elif command.startswith("/select "):
                    ids = [item.strip() for item in command[8:].split(",") if item.strip()]
                    events = runtime.iter_resume_agent_traced(user_id, thread_id, {"meeting_ids": ids})
                elif command.startswith("/merge ") or command in {"추가", "대체", "add", "replace"}:
                    mode = command[7:].strip() if command.startswith("/merge ") else {"추가": "add", "대체": "replace"}.get(command, command)
                    events = runtime.iter_resume_agent_traced(user_id, thread_id, {"mode": mode})
                else:
                    events = runtime.iter_agent_traced(user_id, thread_id, command)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                for event in events:
                    if event["type"] == "final":
                        result = event["result"]
                        event = {
                            "type": "final",
                            "response": result.get("response", ""),
                            "state": runtime.get_agent_state(user_id, thread_id),
                            "interrupt": interrupt_payload(result),
                        }
                    self.wfile.write(encode_sse(event))
                    self.wfile.flush()
            except Exception as exc:
                try:
                    self.wfile.write(encode_sse(public_error_event(exc)))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

    server = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}"
    print(f"발표용 Trace 서버: {url}")
    print("종료: Ctrl+C")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try: server.serve_forever()
    finally: server.server_close()
