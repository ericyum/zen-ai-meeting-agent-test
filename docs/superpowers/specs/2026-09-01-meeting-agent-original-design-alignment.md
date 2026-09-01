# 회의록 Agent 원본 설계 정합화와 실시간 Trace 설계

**문서 성격:** 구현 전 규범 명세  
**기준일:** 2026-09-01  
**대상:** `zen-ai-meeting-agent-test`

## 1. 목표

이 변경의 목적은 현재 POC를 임의로 최적화하는 것이 아니라, Obsidian의 `회의록 에이전트 설계` 원본 문서에 정의된 LLM·하네스·Tool·Condition·Node 책임을 Python과 LangGraph로 그대로 검증하는 것이다.

브라우저 Trace는 실행이 끝난 뒤 이벤트를 재생하지 않는다. 실제 Graph 이벤트가 발생하는 즉시 UI에 전달하여 사용자가 Graph·Subgraph·Node·Edge·State와 업무 Checkpoint 진행을 관찰할 수 있게 한다.

## 2. 기준 문서와 우선순위

구현 판단 순서는 다음과 같다.

1. Obsidian `회의록 에이전트 설계`의 원본 설계 문서
2. 이 명세
3. 원본 설계 계약을 검증하는 테스트
4. 현재 코드와 회고형 As-built 문서

현재 코드나 As-built 문서가 원본 설계와 충돌하면 기존 동작을 유지하지 않고 원본 설계에 맞춘다. 기술적으로 구현할 수 없는 항목이 발견될 때만 사용자와 별도로 설계 변경을 합의한다.

## 3. 범위

### 포함

- LLM의 의도·Tool 판단 계약
- Tool 1 검색 조건과 후보 경로 해석
- Search 응답과 후속 작업 여부의 구조화 반환
- Question 응답과 후속 작업 여부의 구조화 반환
- 하네스의 고정 후속 작업 Condition
- Search·Question Subgraph의 실제 Node 책임
- Subgraph 경계의 ScratchPad delta 병합
- 업무 결과 반영과 Checkpoint 경계
- SSE 기반 Graph 이벤트 실시간 전송
- 원본 설계 순서와 호출 횟수를 검증하는 오프라인 테스트
- 선택적으로 실행되는 실제 DeepSeek 통합 테스트
- 관련 저장소 명세와 Obsidian POC 문서 갱신

### 제외

- 실제 `zen-ai` 제품 코드 이식
- 실제 인증·회의록 DB·녹화 모달 연결
- WebSocket 도입
- LLM 답변 토큰 단위 스트리밍
- 별도 프론트엔드 프레임워크 도입
- 원본 설계와 무관한 리팩터링

## 4. LLM과 하네스의 책임

### 4.1 LLM 책임

LLM은 다음을 수행한다.

1. 현재 사용자 목표와 ScratchPad를 바탕으로 `search`, `question` 또는 Tool 없는 직접 답변 경로를 판단한다.
2. Search가 필요하면 Tool 1에 전달할 구조화된 검색 조건을 생성한다.
3. Tool 1의 구조화된 후보 결과를 해석하여 `none`, `one`, `many` 경로를 반환한다.
4. Search의 검증·선택 결과를 받아 사용자 안내와 `follow_up`을 함께 생성한다.
5. Question의 원문 또는 선택 필요·거부·실패 컨텍스트를 받아 사용자 답변과 `follow_up`을 함께 생성한다.
6. Tool 없는 요청에는 직접 답변을 생성한다.

LLM은 권한, 후보 선택의 유효성, `authorized_meeting_ids` 갱신과 다음 Edge를 직접 확정하지 않는다. `authorized_meeting_ids`의 값과 필드 자체도 목표 판단 Model Context에 전달하지 않고 Tool 2의 결정적 하네스 경계에서만 사용한다.

### 4.2 하네스 책임

하네스는 다음을 수행한다.

1. LLM의 구조화 출력을 허용된 Schema로 검증한다.
2. Tool 인터페이스와 Repository 대역을 연결한다.
3. 후보 수 결과에 맞는 고정 Edge를 선택한다.
4. HITL 선택 ID를 후보 집합과 권한 경계에서 다시 검증한다.
5. `authorized_meeting_ids`를 설정·유지·추가·대체한다.
6. Tool 결과와 State 갱신 결과를 Model Context로 조립한다.
7. LLM이 반환한 `follow_up`을 고정 Condition으로 읽어 LLM 판단 복귀 또는 END Edge를 선택한다.
8. 응답과 Tool 결과를 ScratchPad에 한 번만 반영하고 업무 Checkpoint를 저장한다.
9. HITL 입력이 후보·추가대체 Schema에 맞지 않으면 같은 질문을 다시 표시하고 pending 실행을 보존한다.

## 5. 구조화 Model 계약

개념적 반환 계약은 다음과 같다.

```python
class SearchQuery(TypedDict):
    meeting_ids: list[str]
    keywords: list[str]
    meeting_date: str | None

class ActionDecision(TypedDict):
    action: Literal["search", "question", "direct"]
    search_query: SearchQuery | None

class CandidateDecision(TypedDict):
    route: Literal["none", "one", "many"]

class AgentResponse(TypedDict):
    response: str
    follow_up: bool
```

`none`은 별도의 최종 LLM 행동으로 요청하지 않는다. Search·Question 응답의 `follow_up=False`를 하네스 Condition이 읽어 현재 상위 Graph State를 `none`으로 정리하고 END로 이동한다.

Tool 없는 직접 답변은 개념적 State `none`을 유지한다. 구현상의 routing 값 `direct`는 지속 State가 아니며 직접 답변 완료 후 END로 이동한다.

## 6. 목표 Graph 흐름

```text
START
→ receive_user_request
→ compact_scratchpad
→ llm_goal_condition
   ├─ search   → Search Subgraph
   ├─ question → Question Subgraph
   └─ direct   → Direct Response

Search/Question 결과
→ Graph Runtime 공통 처리
   ├─ State delta와 응답 반영
   ├─ 업무 Checkpoint 저장
   └─ fixed_follow_up_condition
       ├─ true  → compact_scratchpad → llm_goal_condition
       └─ false → END

Direct Response
→ Graph Runtime 공통 처리
→ END
```

Graph Runtime 공통 처리는 독립적인 Agent 업무가 아니다. Trace에서는 `runtime` 또는 `checkpoint` 이벤트로 표현하되, 설계상의 LLM·Tool Node인 것처럼 표시하지 않는다.

## 7. Search Subgraph

```text
START
→ S1_candidate_search_and_judgment
→ fixed_candidate_condition
   ├─ none → S2A_no_candidates
   ├─ one  → S2B_one_candidate
   └─ many → S2C_multiple_candidates
→ 선택 ID 재검증과 기존 목록 처리
→ S3_search_response
   └─ LLM: response + follow_up
→ SEARCH_RESPONSE_READY
→ END
```

- Tool 1은 LLM이 만든 구조화 검색 조건을 받는다.
- Repository는 현재 사용자의 접근 가능한 범위만 검색한다.
- 후보 경로는 LLM이 구조화해 반환하고, 실제 Edge는 고정 Condition이 선택한다.
- 복수 후보와 새 ID의 추가·대체는 기존 HITL 규칙을 유지한다.
- S3는 검색 안내와 후속 작업 여부를 함께 생성한다.

## 8. Question Subgraph

```text
START
→ Q1_tool_2_source_lookup
→ fixed_source_result_condition
   ├─ selection_required       → Q2A_build_selection_context
   ├─ source_ready             → Q2B_build_source_context
   └─ source_denied_or_failed  → Q2C_build_failure_context
→ Q3_question_response
   └─ LLM: response + follow_up
→ QUESTION_RESPONSE_READY
→ END
```

- Q1은 허용 ID 확인, Tool 2 호출, 권한 재검증과 원문 조회까지만 담당한다.
- Q2-A/B/C는 결과별 Model Context를 조립한다.
- Q3만 최종 질문 답변 LLM을 호출한다.
- 원문은 Q2-B에서 Q3로 전달되는 `UntrackedValue` 실행 중 데이터이며 Agent State, Checkpoint와 Trace에 저장하지 않는다.
- Checkpoint·Lock·HITL Context는 `(user_id, thread_id)`로 격리한다.

## 9. ScratchPad와 Checkpoint

- 부모 Graph가 Subgraph에 넘긴 기존 ScratchPad를 Subgraph 결과로 다시 append하지 않는다.
- 각 Subgraph는 이번 실행에서 새로 생성한 이벤트 delta만 부모 Graph에 반환한다.
- 사용자 요청, LLM 판단, Search/Question 응답과 구조화된 Tool 메타데이터는 각각 한 번만 남긴다.
- 업무 결과가 Agent State에 반영된 뒤 SQLite Checkpointer가 복구 Snapshot을 저장한다.
- 발표용 Trace의 업무 Checkpoint와 LangGraph 기술 Snapshot을 구분한다.
- 원문, API 키와 Authorization Header는 State, Checkpoint와 Trace에 포함하지 않는다.

## 10. 실시간 Trace 전송

### 10.1 전송 방식

표준 라이브러리 기반 SSE(Server-Sent Events)를 사용한다. WebSocket이나 외부 웹 프레임워크는 추가하지 않는다.

브라우저는 명령을 `POST`로 제출하고 Fetch API의 `ReadableStream`으로 SSE 형식 응답을 순서대로 읽는다. 브라우저 기본 `EventSource`는 `GET`만 지원하므로 사용하지 않는다.

```text
POST command
→ SSE response 시작
→ node_start
→ node_end
→ edge
→ runtime_checkpoint
→ interrupt 또는 final
→ stream 종료
```

각 SSE data payload는 JSON이며 최소한 다음 필드를 사용한다.

```json
{
  "type": "node|edge|checkpoint|interrupt|final|error",
  "graph": "Agent Graph|Search Subgraph|Question Subgraph",
  "node": "node_name",
  "step": 1,
  "payload": {}
}
```

### 10.2 UI 동작

- 이벤트가 도착하는 즉시 Trace panel에 추가한다.
- 이벤트당 인위적인 `setTimeout`을 사용하지 않는다.
- Node 시작과 LLM 호출 시작이 도착하면 DeepSeek 응답을 기다리는 동안에도 화면에 남는다.
- `final` 이벤트가 도착하면 최종 답변을 채팅 영역에 표시한다.
- `interrupt` 이벤트가 도착하면 HITL 메시지를 표시하고 스트림을 정상 종료한다.
- 기술 State Snapshot은 기존처럼 기본 숨김을 유지한다.

## 11. 오류 처리

- DeepSeek 구조화 응답이 Schema를 위반하면 명확한 Runtime 오류로 변환한다.
- SSE 전송 전에 오류가 발생하면 일반 JSON 오류를 반환할 수 있다.
- SSE 시작 후 오류가 발생하면 API 키·원문을 제거한 `error` 이벤트를 보내고 스트림을 닫는다.
- 클라이언트 연결이 끊겨도 서버와 Checkpoint DB 연결이 손상되지 않게 요청 단위 정리를 수행한다.
- Tool Retry와 구조화 오류 정책은 기존 보안 경계를 유지한다.

## 12. 테스트 계약

오프라인 테스트는 실제 API 없이 다음을 검증한다.

1. 복합 요청의 LLM 역할 순서가 `goal → candidate → search_response → goal → question_response`인지 확인한다.
2. 최종 Question 응답 후 `none` 확인용 LLM 호출이 발생하지 않는지 확인한다.
3. Search·Question 응답이 `response`와 `follow_up`을 함께 반환하는지 확인한다.
4. `follow_up` 고정 Condition이 복귀와 END Edge를 선택하는지 확인한다.
5. Q1·Q2·Q3 Trace가 실제 책임과 같은 순서로 발생하는지 확인한다.
6. ScratchPad 이벤트가 Subgraph 경계에서 중복되지 않는지 확인한다.
7. 원문과 API 키가 State·Checkpoint·SSE Trace에 포함되지 않는지 확인한다.
8. SSE 소비자가 Graph 실행 완료 전에 첫 Node 이벤트를 받을 수 있는지 확인한다.
9. HITL 중단·재개에서도 이벤트가 실시간 순서로 전달되는지 확인한다.
10. 녹화 Workflow는 LLM을 호출하지 않는지 확인한다.

실제 DeepSeek 통합 테스트는 키 파일이 명시된 경우에만 실행하며 비용과 네트워크를 사용한다. 실제 테스트에서도 최종 `none` 재호출이 없고 구조화 응답 Schema가 유지되는지 확인한다.

## 13. 완료 조건

- 원본 설계의 LLM·하네스·Tool·Condition 책임이 코드와 테스트에 일치한다.
- Search·Question의 실제 Node 책임과 Trace 표시가 일치한다.
- 최종 응답 이후 종료 확인만을 위한 DeepSeek 호출이 없다.
- Trace가 Graph 실행 완료 후 재생되는 것이 아니라 이벤트 발생 즉시 UI에 표시된다.
- ScratchPad에 동일 이벤트가 Subgraph 병합으로 중복되지 않는다.
- 기존 보안·권한·HITL·녹화 Workflow 불변 조건이 유지된다.
- 전체 오프라인 테스트가 통과하고 실제 API 테스트는 명시적 실행 또는 안전한 Skip 상태를 유지한다.

## 14. 최소 구현 원칙

- Python 표준 라이브러리와 현재 LangGraph API를 우선 사용한다.
- SSE를 위해 새 웹 프레임워크나 비동기 Queue 추상화를 추가하지 않는다.
- Trace 이벤트와 Model 구조화 반환에는 필요한 필드만 둔다.
- 원본 설계 검증과 관계없는 성능 최적화·UI 확장·제품 통합은 포함하지 않는다.
