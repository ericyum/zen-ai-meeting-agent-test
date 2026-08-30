# ZEN AI Meeting Agent LangGraph POC

`1-1. 회의록 에이전트 업무 개요`와 `1-2. 회의록 에이전트 워크플로우 Graph 상세`를 실행 가능한 코드로 옮긴 로컬 POC입니다.

## 구현 범위

- LangGraph `StateGraph` 기반 최상위 Agent Graph
- Search·Question 내부 Subgraph
- SQLite `SqliteSaver` 기반 Agent Checkpoint
- SQLite 더미 사용자·회의록·권한·녹화 모달 데이터
- 복수 검색 후보와 기존 ID 추가·대체를 위한 `interrupt()` HITL
- `ScratchPad`, `authorized_meeting_ids`, `recording_modal_status`
- Checkpoint 뒤 후속 작업 판단을 통한 Search → Question 제어 루프
- LLM을 사용하지 않는 독립된 결정적 녹화 Workflow
- 회의록 원문을 Agent State·Checkpoint에 저장하지 않는 컨텍스트 조립
- 영속 Agent State를 `ScratchPad`, `authorized_meeting_ids`, `recording_modal_status`로 제한
- 사용자·스레드·요청을 비영속 LangGraph Runtime Context로 전달
- 같은 스레드 요청의 프로세스 내 순차 처리
- Tool 실패 2회 재시도와 구조화 오류 반환
- 컨텍스트 예산의 50%에서 `ScratchPad` Compact

기본 모델은 API 키 없이 실행되는 `RuleBasedMeetingModel`입니다. LLM이 담당하는 의도·후속 작업 판단과 답변 생성을 재현하기 위한 더미 구현이며, Graph와 Tool 경계는 실제 모델 어댑터로 교체할 수 있게 분리했습니다.

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

`search`와 `question`은 별도 지속 코드 변수로 저장하지 않습니다. 실행 중인 Subgraph Node와 Edge가 해당 개념적 Graph State를 나타냅니다.

`user_id`, `thread_id`, 현재 `request`는 `AgentContext`로 전달되며 Agent Checkpoint에 저장되지 않습니다. 별도 `run_id`나 `request_id`도 사용하지 않습니다.

회의록 원문은 `Q2B_Q2C_tool_2_and_answer` Node의 지역 변수에만 존재합니다. State로 반환하는 값은 답변과 문서 ID·제목 메타데이터뿐이므로 SQLite Checkpoint에는 원문이 들어가지 않습니다.

## 설치

Windows PowerShell 기준:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

## 실행

더미 데이터 생성:

```powershell
.\.venv\Scripts\meeting-agent.exe seed
```

Search → Question 복합 요청:

```powershell
.\.venv\Scripts\meeting-agent.exe ask "meeting-001 회의록을 가져오고 결정 사항을 설명해줘"
```

복수 후보 HITL을 확인하려면:

```powershell
.\.venv\Scripts\meeting-agent.exe ask "회의록 검색해줘"
```

대화형 실행:

```powershell
.\.venv\Scripts\meeting-agent.exe chat
```

녹화 Workflow:

```powershell
.\.venv\Scripts\meeting-agent.exe record start
.\.venv\Scripts\meeting-agent.exe record pause
.\.venv\Scripts\meeting-agent.exe record resume
.\.venv\Scripts\meeting-agent.exe record stop
```

현재 Agent State 확인:

```powershell
.\.venv\Scripts\meeting-agent.exe state
```

## 더미 데이터

기본 사용자는 `user-eric`, 기본 스레드는 `thread-eric`입니다.

| ID | 제목 | `user-eric` 접근 |
|---|---|---|
| `meeting-001` | ZEN AI 제품 로드맵 회의 | 생성자 |
| `meeting-002` | 3분기 마케팅 전략 회의 | 공유 |
| `meeting-003` | 보안 검토 회의 | 공유 |
| `meeting-004` | 재무 비공개 회의 | 접근 불가 |

## 테스트

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

테스트는 다음을 확인합니다.

- 하나의 목표에서 Search 이후 Question이 이어지는지
- 검증된 ID만 `authorized_meeting_ids`에 반영되는지
- 회의록 원문이 Agent State·Checkpoint에 남지 않는지
- Agent Checkpoint State가 설계의 세 필드만 가지는지
- 복수 후보에서 HITL interrupt가 발생하는지
- 후보 수 해석과 질문 Model Context가 모델 경계를 통과하는지
- Tool 오류가 재시도 후 구조화되어 모델에 전달되는지
- `ScratchPad`가 컨텍스트 예산 50%에서 Compact되는지
- 녹화 State가 `none → recording ↔ pause → stop → none` 규칙을 따르는지
- 정상적인 State 거부가 `recording_modal_status=error`로 처리되지 않는지
- 모달 실행 오류가 Agent의 `recording_modal_status=error`에 동기화되는지

## POC 한계

- 규칙 기반 더미 모델이므로 자연어 해석 범위가 제한적입니다.
- SQLite Checkpointer는 로컬 데모와 소규모 POC 용도입니다.
- 실제 ZEN AI 프론트엔드 모달·백엔드 API 대신 SQLite 서비스로 동작을 재현합니다.
- HITL 실행 Context는 현재 프로세스 메모리에만 있으므로 프로세스를 재시작한 뒤의 실행 복구는 지원하지 않습니다.
- 실제 배포에서는 인증 세션, 비밀 관리, 분산 큐·멱등성, 관측성과 운영 DB가 추가로 필요합니다.
