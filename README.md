# ZEN AI Meeting Agent LangGraph POC

회의록 Agent 설계가 기술적으로 성립하는지를 독립적으로 검증하기 위해 Python과 LangGraph로 작성한 POC입니다. 향후 `zen-ai`에 이식할 때 참고하지만, 현재 POC의 구조를 실제 서비스 구조에 억지로 맞추는 것이 목적은 아닙니다.

## POC의 핵심 질문

> **회의록 Agent 설계가 기술적으로 성립하는가?**

이를 위해 다음을 실행 가능한 코드와 테스트로 확인합니다.

- LangGraph `StateGraph` 기반 최상위 Agent Graph
- Search·Question 내부 Subgraph
- SQLite `SqliteSaver` 기반 Agent Checkpoint
- 복수 후보와 기존 ID 추가·대체를 위한 `interrupt()` HITL
- `ScratchPad`, `authorized_meeting_ids`, `recording_modal_status`
- Search 완료 후 Question으로 이어지는 제어 Loop
- LLM을 사용하지 않는 독립된 결정적 녹화 Workflow
- 회의록 원문을 Agent State·Checkpoint에 저장하지 않는 Context 조립
- 사용자·스레드·요청을 비영속 Runtime Context로 전달
- 같은 Thread 요청의 프로세스 내 순차 처리
- Tool 실패 2회 재시도와 구조화 오류 반환
- Context 예산 50%에서 `ScratchPad` Compact
- 실제 `deepseek-v4-pro` 모델 호출

CLI의 기본 모델은 실제 DeepSeek API를 호출하는 `DeepSeekMeetingModel`입니다. `RuleBasedMeetingModel`은 빠르고 재현 가능한 오프라인 테스트 대역으로 유지합니다. API 키는 코드나 State에 저장하지 않고 실행 시 키 파일에서만 읽습니다.

## 설계 대응

```text
회의 시작·일시정지·재개·종료
→ recording_graph.py
→ 결정적 StateGraph
→ SQLite recording_sessions가 recording_state 소유
→ recording_modal_status만 Agent Checkpoint에 동기화

회의록 검색·질문
→ agent_graph.py의 최상위 StateGraph
→ Search·Question Subgraph
→ graph_runtime_checkpoint
→ llm_goal_condition
   ├─ 후속 작업 있음: 다음 Subgraph
   └─ 후속 작업 없음: END
```

`search`와 `question`은 별도 영속 변수로 저장하지 않습니다. 실행 중인 Subgraph Node와 Edge가 개념적 Graph State를 나타냅니다.

`user_id`, `thread_id`, 현재 `request`는 `AgentContext`로 전달되며 Checkpoint에 저장되지 않습니다. 회의록 원문도 Question Node의 지역 변수에만 존재하며 State에는 답변과 문서 메타데이터만 남습니다.

## 파일별 역할

| 파일 | 역할 |
|---|---|
| `src/meeting_agent/state.py` | Agent State, Subgraph State, Runtime Context |
| `src/meeting_agent/agent_graph.py` | 최상위 Agent Graph와 Search·Question Subgraph |
| `src/meeting_agent/recording_graph.py` | LLM 없는 결정적 녹화 Workflow |
| `src/meeting_agent/runtime.py` | Graph 조립, Checkpoint, Lock, HITL 재개 |
| `src/meeting_agent/model.py` | 실제 DeepSeek Adapter와 테스트 Model 계약 |
| `src/meeting_agent/repository.py` | 권한·검색·원문·녹화 백엔드의 SQLite 대역 |
| `src/meeting_agent/cli.py` | 실제 UI 대신 사용하는 로컬 실행 진입점 |

## 설치

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

## 실행

실제 DeepSeek으로 Search → Question 복합 요청을 실행합니다.

```powershell
.\.venv\Scripts\python.exe -m meeting_agent `
  --deepseek-key-file "C:\secure\DeepSeek API Key.txt" `
  ask "meeting-001 회의록을 가져오고 결정 사항을 설명해줘"
```

외부 API 없이 규칙 기반 대역으로 실행하려면:

```powershell
.\.venv\Scripts\python.exe -m meeting_agent `
  --model-provider rule-based `
  ask "meeting-001 회의록을 가져오고 결정 사항을 설명해줘"
```

기타 명령:

```powershell
.\.venv\Scripts\meeting-agent.exe seed
.\.venv\Scripts\meeting-agent.exe chat
.\.venv\Scripts\meeting-agent.exe record start
.\.venv\Scripts\meeting-agent.exe record pause
.\.venv\Scripts\meeting-agent.exe record resume
.\.venv\Scripts\meeting-agent.exe record stop
.\.venv\Scripts\meeting-agent.exe state
```

## 테스트

오프라인 테스트:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

실제 DeepSeek 통합 테스트는 API 사용량이 발생하므로 키 파일을 지정할 때만 실행합니다.

```powershell
$env:DEEPSEEK_API_KEY_FILE="C:\secure\DeepSeek API Key.txt"
$env:DEEPSEEK_MODEL="deepseek-v4-pro"
.\.venv\Scripts\python.exe -m unittest tests.test_deepseek_integration -v
```

## 발표용 로컬 Trace 서버

브라우저에서 실제 LangGraph 실행의 Graph, Subgraph, Node, Edge와 State Snapshot을 순서대로 확인할 수 있습니다.

```powershell
.\.venv\Scripts\python.exe -m meeting_agent `
  --deepseek-key-file "C:\secure\DeepSeek API Key.txt" `
  serve
```

기본 주소는 `http://127.0.0.1:8765`이며 브라우저가 자동으로 열립니다. 서버 종료는 터미널에서 `Ctrl+C`입니다.

발표 입력 예시:

```text
/meeting-start
meeting-001 회의록을 가져오고 결정 사항을 설명해줘
/meeting-pause
/meeting-resume
/meeting-stop
```

복수 후보 HITL은 `회의록 검색해줘`로 발생시키고, 화면에 나온 후보를 `/select meeting-001`처럼 입력해 재개합니다. 기존 허용 ID가 있을 때 추가·대체 질문이 나오면 `/merge add` 또는 `/merge replace`를 입력합니다.

Trace는 LangGraph의 실제 debug stream에서 생성합니다. 회의록 원문과 API 키는 화면 로그에 노출하지 않습니다.

## 실제 구현과 대역

### 실제 검증 코드

- Agent Graph·Search/Question Subgraph
- State·Checkpoint·HITL
- 실제 DeepSeek 호출과 구조화 판단값 검증
- Tool 경계, 권한 재검증, 원문 비영속성
- 결정적 녹화 Workflow와 Graph 제어 Loop

### 제품 경계의 대역

- CLI: 실제 ZEN AI 채팅 UI 대역
- SQLite Repository: 인증·권한·회의록 Backend 대역
- `recording_sessions`: 실제 녹화 모달·Backend 대역
- `RuleBasedMeetingModel`: 오프라인 테스트용 LLM 대역
- 프로세스 내부 Lock/HITL Context: 운영 Queue 대역

## POC 한계

- 실제 프론트엔드·인증·업무 DB·녹화 Backend는 연결하지 않습니다.
- SQLite Checkpointer와 Repository는 로컬 POC 용도입니다.
- HITL Runtime Context는 프로세스 재시작 후 복구되지 않습니다.
- 운영 환경에는 분산 Queue, 멱등성, 비밀 관리, 관측성과 실제 토큰 계산이 추가로 필요합니다.
- 실제 `zen-ai` 이식 방식은 별도 문서에서 다루며 이 POC의 기술 검증 구조와 분리합니다.
