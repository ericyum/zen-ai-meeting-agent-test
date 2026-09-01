# Meeting Agent Original Design Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 원본 회의록 Agent 설계의 LLM·Tool·Condition·Node 책임을 복원하고 실제 LangGraph 이벤트를 브라우저에 즉시 스트리밍한다.

**Architecture:** Model은 구조화된 action, candidate route, `response + follow_up`을 반환한다. Search·Question Subgraph는 원본 Node 책임대로 분리하고, 부모 Graph의 고정 Condition이 `follow_up`을 읽어 재판단 또는 END를 선택한다. 로컬 서버는 LangGraph debug stream을 Fetch `ReadableStream`으로 소비할 수 있는 SSE 형식으로 즉시 전달한다.

**Tech Stack:** Python 3.11+, LangGraph, SQLite, Python 표준 라이브러리 HTTP/SSE, unittest, 브라우저 Fetch API

**Spec:** `docs/superpowers/specs/2026-09-01-meeting-agent-original-design-alignment.md`

## Global Constraints

- 원본 Obsidian 회의록 Agent 설계를 구현 기준으로 사용한다.
- Agent 영속 State 핵심 필드는 `ScratchPad`, `authorized_meeting_ids`, `recording_modal_status`만 유지한다.
- API 키와 회의록 원문을 State, Checkpoint와 Trace에 저장하지 않는다.
- 별도 브랜치, worktree, 커밋과 PR을 만들지 않는다.
- 새 웹 프레임워크와 WebSocket 의존성을 추가하지 않는다.
- 테스트를 먼저 작성하고 실패를 확인한 뒤 최소 구현을 추가한다.

---

### Task 1: 구조화 Model 계약과 원본 LLM 순서

**Files:**
- Modify: `src/meeting_agent/model.py`
- Modify: `src/meeting_agent/repository.py`
- Test: `tests/test_agent.py`
- Test: `tests/test_deepseek_model.py`

**Interfaces:**
- Produces: `ActionDecision`, `CandidateDecision`, `AgentResponse`, `SearchQuery`
- Produces: Model methods returning structured dictionaries
- Consumes: Existing DeepSeek Chat Completions adapter and Repository permission boundary

- [ ] **Step 1: Write failing tests**

Add tests proving that a compound request invokes model roles in the literal order `goal, candidate, search_response, goal, question_response`, that no final goal/`none` call occurs, and that DeepSeek structured responses validate every field.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `./.venv/Scripts/python.exe -m unittest tests.test_agent tests.test_deepseek_model -v`

Expected: failures because existing methods return strings and the graph performs a final `none` decision.

- [ ] **Step 3: Implement minimal structured contracts**

Implement literal TypedDict contracts, rule-based deterministic stand-ins, DeepSeek JSON parsing and Repository search using `meeting_ids`, `keywords`, and `meeting_date`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the same focused command and require zero failures.

### Task 2: Search·Question Node 책임과 fixed follow-up Condition

**Files:**
- Modify: `src/meeting_agent/state.py`
- Modify: `src/meeting_agent/agent_graph.py`
- Modify: `src/meeting_agent/runtime.py`
- Test: `tests/test_agent.py`
- Test: `tests/test_tracing.py`

**Interfaces:**
- Consumes: Task 1 structured Model results
- Produces: Search S1 Tool → candidate LLM → S2 → S3 response flow
- Produces: Question Q1 source lookup → Q2 context → Q3 response flow
- Produces: `follow_up` fixed routing without final LLM completion call

- [ ] **Step 1: Write failing graph responsibility tests**

Add tests for exact Node order, fixed follow-up routing, absence of `business_checkpoint` ScratchPad events, and unique ScratchPad events across Subgraph merges.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `./.venv/Scripts/python.exe -m unittest tests.test_agent tests.test_tracing -v`

- [ ] **Step 3: Implement minimal graph changes**

Split Q1/Q2/Q3 responsibilities, add S1 candidate interpretation as an actual LLM responsibility, route from Search/Question using the response event's `follow_up`, remove final `none` decision and remove the independent business checkpoint Node. Make the ScratchPad reducer recognize a Subgraph's full-prefix return so existing events are not appended twice.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the same focused command and require zero failures.

### Task 3: SSE 실시간 Trace

**Files:**
- Modify: `src/meeting_agent/tracing.py`
- Modify: `src/meeting_agent/runtime.py`
- Modify: `src/meeting_agent/web_demo.py`
- Test: `tests/test_tracing.py`
- Create: `tests/test_web_demo.py`

**Interfaces:**
- Produces: iterator yielding sanitized trace events followed by final/interrupt result
- Produces: `POST /api/command` SSE response consumed with Fetch `ReadableStream`
- Preserves: existing `run_agent_traced` collection API for CLI/tests

- [ ] **Step 1: Write failing streaming tests**

Add tests proving the first Node event is yielded before final completion, SSE frames are valid JSON `data:` records, snapshots remain optional, and transcript/API key fields never appear.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `./.venv/Scripts/python.exe -m unittest tests.test_tracing tests.test_web_demo -v`

- [ ] **Step 3: Implement minimal streaming path**

Convert one debug chunk at a time, expose runtime iterators, stream frames from the standard-library handler with flush, consume them incrementally in JavaScript, and remove the 110ms replay delay.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the same focused command and require zero failures.

### Task 4: 통합 검증과 문서 정합화

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-08-31-meeting-agent-poc-as-built-design.md`
- Modify: relevant Markdown files under `C:/Users/염정운/Obsidian/ZENAI/에이전트 설계/회의록 에이전트 POC`
- Test: all files under `tests/`

**Interfaces:**
- Consumes: Tasks 1–3 final behavior
- Produces: current execution and presentation documentation matching verified code

- [ ] **Step 1: Run full offline verification**

Run: `./.venv/Scripts/python.exe -m unittest discover -s tests -v`

- [ ] **Step 2: Run explicit real DeepSeek integration when the configured key file is available**

Use the external key file without printing or persisting its contents. If unavailable, retain and report the test's explicit Skip.

- [ ] **Step 3: Verify the local server behavior**

Start the server with rule-based provider, submit the compound request, and confirm trace events precede the final response without artificial replay delay.

- [ ] **Step 4: Update documentation**

Document the verified LLM order, fixed Condition, actual Node responsibilities, SSE trace, API/rule-based commands, and real-code versus stand-in boundaries.

- [ ] **Step 5: Run final verification and inspect the diff**

Run the full offline suite again, inspect `git diff --check`, and perform a Ponytail review for removable complexity without changing the original design contract.
