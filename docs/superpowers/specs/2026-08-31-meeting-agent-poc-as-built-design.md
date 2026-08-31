# 회의록 Agent POC 현행 설계 명세

**문서 성격:** 회고형 As-built 명세  
**기준일:** 2026-08-31  
**대상:** `zen-ai-meeting-agent-test`

> 이 문서는 최초 구현 전에 확정된 사전 명세가 아니다. 기존 회의록 Agent 설계 문서, POC 문서, 현재 코드와 테스트를 대조하여 현재 구현 상태를 재구성한 현행 명세다. 코드와 문서가 충돌할 경우 현재 동작은 코드와 테스트로 확인하고, 설계 의도와의 차이는 결함 또는 변경 후보로 기록한다.

## 1. 목적과 범위

POC가 답하려는 핵심 질문은 다음과 같다.

> **회의록 Agent 설계가 기술적으로 성립하는가?**

이를 확인하기 위해 Python과 LangGraph만으로 다음 설계 요소를 실행 가능하게 만든다.

- 최상위 Agent Graph의 목표 판단과 반복 실행
- Search·Question Subgraph의 책임 분리
- 결정적 녹화 Workflow
- 영속 State와 비영속 Runtime Context의 분리
- Checkpoint와 HITL 중단·재개
- 권한 검증, Tool 재시도와 구조화 오류
- 회의록 원문 및 API 키의 비영속성
- 실제 DeepSeek API와 결정적 테스트 대역의 교체 가능성
- Graph·Subgraph·Node·Edge·State를 확인하는 발표용 Trace

실제 `zen-ai` 프론트엔드, 인증 서버, 업무 DB와 녹화 Backend를 완성하는 것은 범위 밖이다.

## 2. 근거 자료와 우선순위

현행 상태를 판단할 때 다음 자료를 함께 사용한다.

1. 현재 `src/meeting_agent` 코드와 `tests`의 실행 결과
2. Obsidian의 `회의록 에이전트 설계` 원본 설계 문서
3. Obsidian의 `회의록 에이전트 POC` 1~6번 문서
4. 이 저장소의 Git 이력과 명시적으로 기록된 결정

이 문서는 위 자료를 찾아가기 위한 기준점이며, 실행 가능한 코드 자체를 대체하지 않는다.

## 3. 전체 아키텍처

```text
사용자/발표 경계
├─ cli.py                    CLI·대화형 실행
└─ web_demo.py               로컬 Trace UI와 HTTP 서버
           │
           ▼
실행 하네스
└─ runtime.py                Graph 조립, Checkpointer, Thread Lock, HITL 재개
           │
           ├───────────────────────────┐
           ▼                           ▼
agent_graph.py                    recording_graph.py
최상위 Agent Graph                 결정적 녹화 Workflow
├─ Search Subgraph                └─ 녹화 명령 → 고정 상태 전이
└─ Question Subgraph
           │                           │
           ├───────────────┬───────────┘
           ▼               ▼
model.py                repository.py
DeepSeek/테스트 Model    권한·회의록·녹화 Backend 대역

공통 계약
├─ state.py               영속 State, Subgraph State, Runtime Context
└─ tracing.py             LangGraph debug event의 안전한 Trace 변환
```

## 4. 파일별 책임

| 파일 | 현행 책임 |
|---|---|
| `state.py` | `AgentState`, `AgentContext`, Subgraph State, 녹화 Workflow State와 ScratchPad Reducer 정의 |
| `agent_graph.py` | 최상위 Agent Graph, Search·Question Subgraph, 목표 Loop, 업무 Checkpoint, Compact 구현 |
| `recording_graph.py` | LLM을 거치지 않는 녹화 명령의 결정적 상태 전이 구현 |
| `runtime.py` | Repository·Model·Graph·SQLite Checkpointer 조립, 동일 Thread Lock, 실행과 HITL 재개 |
| `model.py` | `MeetingModel` 계약, 실제 `DeepSeekMeetingModel`, 오프라인 `RuleBasedMeetingModel` |
| `repository.py` | 회의 검색·선택 재검증·원문 조회·녹화 상태를 제공하는 SQLite 제품 경계 대역 |
| `tracing.py` | 실제 LangGraph debug stream을 Graph/Node/Edge/State 이벤트로 변환하고 민감정보 제거 |
| `web_demo.py` | 발표용 로컬 서버, 명령 입력, HITL 응답과 Trace 화면 제공 |
| `cli.py` | Provider 선택, 단일 질의, 대화, 녹화, State 확인과 서버 실행 진입점 |
| `__main__.py` | `python -m meeting_agent` 실행 진입점 |

## 5. State와 Context 계약

### 5.1 영속 Agent State

최상위 Agent State의 핵심 필드는 다음 세 가지다.

| 필드 | 의미 |
|---|---|
| `ScratchPad` | 사용자 요청, 판단, 응답, 업무 Checkpoint와 Compact 결과의 이벤트 기록 |
| `authorized_meeting_ids` | Backend 경계에서 접근 권한이 검증된 회의 ID 목록 |
| `recording_modal_status` | Agent가 녹화 기능을 호출할 수 있는지 나타내는 축약 상태 |

`search`와 `question`은 별도 영속 필드가 아니다. 현재 실행 중인 Node와 Edge가 개념적 Graph State를 표현한다.

### 5.2 비영속 Runtime Context

`AgentContext`의 `user_id`, `thread_id`, `request`는 호출마다 전달되며 Agent Checkpoint에 저장하지 않는다.

회의록 원문도 Question Node의 지역 변수와 Model Context에서만 사용한다. State에는 답변, 상태와 문서 ID·제목 같은 메타데이터만 남긴다.

### 5.3 목표 상태 용어

목표 판단 결과는 `search`, `question`, `direct`, `none`이다.

- `search`: 회의 후보를 찾고 사용 가능한 ID를 확정해야 한다.
- `question`: 권한이 검증된 회의록을 근거로 답해야 한다.
- `direct`: Tool 없이 직접 응답할 수 있다.
- `none`: 현재 목표의 후속 작업이 없으며 입력 대기 또는 실행 종료로 이동한다.

설계에 없는 `done` 상태는 사용하지 않는다. 실행 종료는 `none` 판단 뒤 `request_finished`와 LangGraph `END`로 표현한다.

## 6. 최상위 Agent Graph

```text
START
→ receive_user_request
→ compact_scratchpad
→ llm_goal_condition
   ├─ search   → Search Subgraph
   ├─ question → Question Subgraph
   ├─ direct   → direct_response
   └─ none     → request_finished → END

Search/Question/direct 결과
→ graph_runtime_checkpoint
→ compact_scratchpad
→ llm_goal_condition
```

핵심은 한 번의 사용자 요청에서 하나의 Tool만 실행하고 끝내지 않는다는 것이다. 예를 들어 “회의록을 가져오고 결정 사항을 설명해줘”는 Search 완료 후 목표를 다시 판단하여 Question까지 이어진다.

`graph_runtime_checkpoint`는 하나의 업무 단위가 끝났음을 ScratchPad에 기록하는 설계상의 Node다.

## 7. Search Subgraph

```text
START
→ S1_tool_1_search
→ 후보/오류 분기
   ├─ 오류   → S2_tool_failed
   ├─ 0개    → S2A_no_candidates
   ├─ 1개    → S2B_auto_select
   └─ 여러 개 → S2C_hitl_select
→ 기존 허용 ID와 비교
   ├─ 선택 없음       → 응답
   ├─ 최초 선택       → S2_set_new_ids
   ├─ 이미 허용됨     → S2_keep_existing_ids
   └─ 새로운 ID 포함  → S2_hitl_add_or_replace
→ S3_search_response_ready
→ END
```

설계상 중요한 보장은 다음과 같다.

- 검색 결과는 사용자 접근 권한 범위 안에서만 반환한다.
- HITL에서 받은 ID도 Repository에서 다시 검증한다.
- 여러 후보는 모델이 임의 확정하지 않고 사용자 선택을 받는다.
- 기존 ID와 새 ID가 충돌하면 `add` 또는 `replace`를 사용자가 결정한다.
- 이미 허용된 동일 ID의 반복 선택은 `unchanged`로 처리해 불필요한 HITL을 만들지 않는다.
- Tool 예외는 최대 2회 시도 후 구조화 오류로 변환한다.

## 8. Question Subgraph

```text
START
→ Q1_tool_2_context_and_answer
→ 결과 분기
   ├─ selection_required
   ├─ source_ready
   └─ source_denied_or_failed
→ Q3_question_response_ready
→ END
```

Question Subgraph는 `authorized_meeting_ids`가 있을 때만 원문을 조회한다. Repository는 현재 사용자 권한을 다시 검사한다. 실제 문서 본문은 Model 호출에만 전달하고 ScratchPad에는 답변과 문서 메타데이터만 기록한다.

## 9. 녹화 Workflow

녹화 시작·일시정지·재개·종료는 자연어 추론 대상이 아니라 코드로 정해진 Workflow다.

```text
START
→ recording_modal_and_backend
→ END
```

Repository 대역이 실제 녹화 상태를 소유하고, 성공 여부를 `recording_modal_status`로 Agent Checkpoint에 동기화한다. 이 상태 모델은 실제 제품 모달을 복제한 것이 아니다. 향후 이식 시에는 `zen-ai`의 Recording Provider와 BackendAdapter 계약을 사용해야 한다.

## 10. Model 경계

### 실제 코드: `DeepSeekMeetingModel`

- OpenAI 호환 DeepSeek Chat Completions API를 호출한다.
- 기본 실행 Provider이며 모델 이름으로 `deepseek-v4-pro`를 지정할 수 있다.
- 목표 판단 결과를 허용된 enum으로 검증한다.
- API 키는 코드에 포함하지 않고 실행 시 외부 키 파일 또는 환경 설정에서 읽는다.

### 테스트 대역: `RuleBasedMeetingModel`

- 네트워크와 API 비용 없이 Graph 분기를 결정적으로 재현한다.
- 오프라인 단위 테스트와 발표 화면 점검에 사용한다.
- 실행 명령에 `--model-provider rule-based`가 있으면 실제 DeepSeek을 호출하지 않는다.

서버 기동 자체는 Model을 호출하지 않으므로 DeepSeek 모드에서도 빠르다. 실제 API 호출은 사용자가 질의를 보낼 때 시작된다.

## 11. Checkpoint와 Trace

### 11.1 업무 경계 Checkpoint

`graph_runtime_checkpoint`는 Search·Question·direct 같은 업무 결과 뒤에 실행되는 명시적 Graph Node다. 목표를 다시 판단하기 전에 업무 완료 경계를 남긴다.

### 11.2 기술 State Snapshot

LangGraph의 SQLite Checkpointer는 복구를 위해 매 Super-step 뒤 기술 Snapshot을 만든다. 이것은 설계도에 표시된 업무 Checkpoint가 아니다.

Trace UI는 혼동을 막기 위해 기술 Snapshot을 기본적으로 숨긴다. 사용자가 옵션을 켰을 때만 표시하며, 업무 경계 Node는 항상 별도로 보인다.

### 11.3 Trace 보안

Trace는 실제 `stream_mode="debug"` 이벤트를 변환한다. Graph와 Subgraph namespace, Node 시작·완료, Edge와 안전한 State를 보여주되 회의록 `transcript`와 API 키는 제거한다.

## 12. 실제 구현과 대역의 경계

### POC가 실제로 검증하는 코드

- LangGraph Node·Edge·Subgraph와 반복 제어
- State Reducer, SQLite Checkpoint와 HITL 중단·재개
- 실제 DeepSeek 호출과 판단값 검증
- 권한 재검증, 원문 비영속성, Tool Retry와 오류 구조화
- ScratchPad Compact와 동일 Thread 순차 실행
- 결정적 녹화 Workflow
- 실제 Graph debug stream 기반 Trace

### 제품 경계의 대역

- CLI 및 로컬 웹 화면: 실제 채팅 UI 대역
- SQLite Repository: 인증·권한·회의록 Backend와 업무 DB 대역
- Repository의 녹화 세션: 실제 모달·녹화 Backend 대역
- `RuleBasedMeetingModel`: 실제 LLM 대역
- 프로세스 내부 Lock과 HITL Context: 운영 Queue·분산 실행 대역

## 13. 검증 전략과 합격 조건

오프라인 전체 테스트:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

실제 DeepSeek 통합 테스트는 키 파일을 지정한 경우에만 수행한다.

검증 범위는 다음을 포함한다.

- Search → Question 복합 목표 Loop
- 영속 State 핵심 필드 제한
- 권한 없는 회의록 차단과 원문 비저장
- 복수 후보 및 추가·대체 HITL 중단·재개
- 동일 ID 반복 검색 시 불필요한 HITL 방지
- Tool 재시도와 구조화 오류
- ScratchPad Compact
- DeepSeek HTTP Adapter 계약과 선택적 실제 API 통합
- 결정적 녹화 전이
- Trace의 상위 Graph·Subgraph 구분과 민감정보 제거

## 14. 현재 한계와 이식 경계

- SQLite Repository와 Checkpointer는 로컬 POC 용도다.
- 프로세스 재시작 이후의 HITL Runtime Context 복구와 분산 FIFO Queue는 없다.
- Context 예산은 실제 Tokenizer가 아닌 문자 수 기준이다.
- 실제 프론트엔드·인증·업무 DB·녹화 Backend는 연결하지 않았다.
- 실제 `zen-ai`에서는 Python Graph를 줄 단위로 옮기지 않는다.
- 이식 시 Tool 계약, 권한 불변 조건, 원문 비영속성, HITL 규칙과 테스트 시나리오를 기존 TypeScript Worker·Run·Message·Recording 구조에 맞게 번역한다.

## 15. 변경 관리

설계 계약을 바꾸기 전에는 다음을 수행한다.

1. 변경하려는 의도와 영향 범위를 사용자와 합의한다.
2. 관련 테스트로 기존 동작을 확인한다.
3. 코드와 테스트를 함께 변경한다.
4. 이 As-built 명세와 관련 Obsidian 문서를 갱신한다.
5. 실제 구현과 대역의 경계가 흐려지지 않았는지 검토한다.

과거 결정과 변경 이유는 `2026-08-31-meeting-agent-poc-evolution.md`에 기록한다.
