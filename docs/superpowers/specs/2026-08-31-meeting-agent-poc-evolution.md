# 회의록 Agent POC 변화 과정과 결정 기록

**문서 성격:** 회고형 Evolution/Decision 기록  
**기준일:** 2026-08-31  
**대상:** 최초 설계 기반 구현부터 현재 As-built POC까지

> 이 문서는 당시 각 단계에서 미리 작성된 작업 일지가 아니다. Git 이력, 현재 코드·테스트, 기존 설계 문서와 POC 문서에서 확인 가능한 사실을 바탕으로 변화 과정을 재구성했다. 확인할 수 없는 세부 시점이나 의도를 사실처럼 만들지 않는다.

## 1. 이 문서가 필요한 이유

현행 코드만 보면 “무엇이 구현되어 있는가”는 알 수 있지만 “왜 현재 구조가 되었는가”는 알기 어렵다. 이 문서는 다음 질문에 답한다.

- 최초 설계의 어떤 의도를 검증하려 했는가?
- 실제 구현과 제품 대역을 왜 나눴는가?
- `zen-ai` 구조를 조사한 뒤에도 왜 독립 Python·LangGraph POC로 돌아왔는가?
- DeepSeek, Trace와 테스트가 왜 추가되었는가?
- `done`/`none`, Checkpoint와 반복 HITL 같은 혼동을 어떻게 정리했는가?

현재의 규범적 설계는 `2026-08-31-meeting-agent-poc-as-built-design.md`를 따른다. 이 문서는 변경 배경을 설명한다.

## 2. 변화 과정 요약

```text
기존 회의록 Agent 설계
→ Python·LangGraph 독립 1차 POC
→ 실제 DeepSeek Adapter와 통합 테스트
→ zen-ai 현행 Agent·녹화 구조 조사
→ 제품 구조에 가까운 형태로 조정 시도
→ POC의 본질을 재확인하고 독립 구조로 복귀
→ 브라우저 Trace와 발표 시나리오 추가
→ 설계 용어·Checkpoint·HITL 동작 교정
→ 현행 As-built 명세와 프로젝트 작업 규칙 작성
```

## 3. 단계별 배경과 결정

### 3.1 기존 설계에서 독립 POC로

#### 배경

회의록 Agent는 향후 `zen-ai` 풀스택 서비스에 들어갈 예정이지만, 제품에 바로 구현하면 Agent 설계 자체의 문제와 제품 통합 문제를 분리하기 어렵다.

#### 결정

먼저 별도 저장소에서 Python과 LangGraph만으로 설계가 기술적으로 성립하는지 검증한다.

#### 반영

- 최상위 Agent Graph와 Search·Question Subgraph 구현
- 녹화 Workflow 분리
- SQLite Repository와 Checkpointer 사용
- CLI를 실제 UI의 실행 대역으로 사용
- 핵심 Graph 흐름을 단위 테스트로 고정

#### 의미

POC의 성공 기준은 `zen-ai`와 폴더 구조가 같은지가 아니라, 설계의 책임과 상태 전이가 실제로 실행되는지다.

### 3.2 실제 DeepSeek 연결

#### 배경

규칙 기반 Model만으로는 Graph 제어는 검증할 수 있지만, 실제 LLM이 목표 판단과 원문 기반 답변 계약을 지키는지는 확인할 수 없다.

#### 결정

`MeetingModel` 계약 아래 두 구현을 둔다.

- `DeepSeekMeetingModel`: 실제 API 호출
- `RuleBasedMeetingModel`: 빠르고 재현 가능한 오프라인 테스트 대역

#### 반영

- DeepSeek 키 파일 로딩
- `deepseek-v4-pro` 지정 가능
- 구조화 목표 판단값 검증
- HTTP Adapter 단위 테스트
- 키가 있을 때만 실행하는 실제 API 통합 테스트

#### 보안 결정

API 키는 코드, Agent State, Checkpoint와 Trace에 저장하지 않는다. 외부 키 파일 또는 실행 환경에서만 읽는다.

### 3.3 `zen-ai` 현행 구조 조사와 방향 조정 시도

#### 배경

향후 이식 시행착오를 줄이기 위해 `zen-ai`의 Agent 실행 공간과 실제 녹화 모달 상태 소유 구조를 조사했다.

#### 확인한 차이

- `zen-ai`는 독립 Python LangGraph 서버보다 TypeScript 공통 Worker·Run·Queue·Message 구조를 중심으로 Agent를 실행한다.
- 실제 녹화 상태는 Agent가 아니라 Meeting Recording Provider와 BackendAdapter가 소유한다.
- POC의 `recording_modal_status`와 SQLite 녹화 상태는 실제 제품 모델을 그대로 표현하지 않는다.

#### 중간 방향

한때 POC 구조를 `zen-ai` Agent 방식에 더 가깝게 맞추는 방향을 검토하고 조정했다.

#### 문제 인식

제품 실행 골격을 POC에 미리 복제하면 다음 두 질문이 섞인다.

1. 회의록 Agent 설계가 기술적으로 성립하는가?
2. 이 설계를 `zen-ai` 제품 구조에 어떻게 통합하는가?

#### 최종 결정

POC는 Python·LangGraph 독립 검증 구조로 되돌린다. `zen-ai` 조사 결과는 코드를 지배하는 구조가 아니라 별도 이식 전략 문서로 유지한다.

#### 남긴 자산

제품에 이식할 때 유지할 것은 Python 코드 형태가 아니라 다음 설계 자산이다.

- Tool 입력·출력 계약
- 권한을 Backend가 강제한다는 원칙
- 원문 비영속성
- 복수 후보와 추가·대체 HITL 규칙
- Search → Question 복합 목표 시나리오
- Retry, Thread 격리와 녹화의 결정적 전이

### 3.4 발표용 Trace 추가

#### 배경

테스트 성공만으로는 발표 중 설계도에 따른 Node·Edge·State·Subgraph 흐름을 눈으로 설명하기 어렵다.

#### 결정

간이 로컬 서버와 브라우저 Trace 화면을 추가한다.

#### 반영

- `web_demo.py`: 로컬 HTTP 서버와 입력 화면
- `tracing.py`: LangGraph `stream_mode="debug"` 이벤트 변환
- Graph와 Subgraph namespace 구분
- Node 시작·완료, Edge와 안전한 State 표시
- DeepSeek 실행 중 진행 표시
- HITL 선택과 추가·대체 재개 입력
- 회의록 원문과 API 키 제거

#### 검증 원칙

화면에 보여주는 Node 목록을 별도로 꾸며내지 않고 실제 LangGraph debug stream에서 Trace를 만든다.

### 3.5 `done`을 제거하고 설계의 `none`으로 통일

#### 문제

구현 또는 Trace 설명에서 `done`이라는 종료 용어가 등장했지만 원래 설계의 목표 상태에는 `none`만 있었다.

#### 결정

`Action`은 `search`, `question`, `direct`, `none`만 사용한다.

#### 의미

`none`은 오류나 값 부재를 뜻하지 않는다. 현재 요청에 대해 수행할 후속 작업이 없어 입력 대기 또는 Graph 종료로 이동한다는 의미다. 실제 실행 종료는 다음처럼 표현한다.

```text
고정 follow_up Condition: false
→ END
```

별도 `done` 상태는 설계와 코드에 추가하지 않는다.

### 3.6 업무 Checkpoint와 기술 Snapshot 분리

#### 문제

브라우저 Trace에서 `State Checkpoint`가 Node마다 반복되어, 설계도 끝부분에 둔 Checkpoint가 임의로 많이 추가된 것처럼 보였다.

#### 원인

두 종류의 개념이 같은 화면에 섞여 있었다.

- Graph Runtime Checkpoint: 설계상의 업무 완료 경계이며 독립 업무 Node가 아님
- LangGraph checkpoint event: 매 Super-step 뒤 복구를 위해 생성되는 기술 State Snapshot

#### 결정

- 업무 경계 Checkpoint는 항상 명확하게 표시한다.
- 기술 Snapshot은 기본적으로 숨기고 옵션을 켰을 때만 표시한다.
- 문서와 발표에서는 두 개념을 다른 이름으로 설명한다.

### 3.7 동일 ID 반복 검색의 불필요한 HITL 제거

#### 문제

이미 `meeting-001`이 허용된 상태에서 같은 ID를 다시 검색해도 추가·대체 HITL이 발생했다. 사용자가 `추가`를 선택한 뒤에도 기대와 다른 “관련 회의록 없음” 응답이 이어질 수 있었다.

#### 결정

새로 선택한 ID 집합이 기존 허용 ID 안에 모두 포함되면 `unchanged` 경로로 보낸다.

```text
이미 허용된 동일 ID
→ S2-B 또는 S2-C 내부에서 merge_mode: unchanged
→ HITL 없이 검색 응답
```

HITL 입력에서는 화면 사용성을 위해 영문 명령과 한국어 표현을 적절히 정규화한다.

### 3.8 DeepSeek 실행과 rule-based 실행의 혼동 정리

#### 문제

서버가 너무 빨리 켜지거나 답변이 즉시 나오는 현상 때문에 실제 DeepSeek 사용 여부가 혼동되었다.

#### 정리

- Provider 옵션을 생략하거나 `--model-provider deepseek`을 쓰면 실제 DeepSeek 모드다.
- `--model-provider rule-based`는 실제 API를 호출하지 않는 대역이다.
- 서버 시작 단계에는 Model 호출이 없으므로 DeepSeek 모드도 즉시 기동될 수 있다.
- 실제 차이는 브라우저에서 질문을 전송한 뒤 나타난다.

실행 방법은 Obsidian의 `3. POC 실행 및 테스트 방법.md`, 발표 흐름은 `6. 발표용 로컬 Trace 시연 가이드.md`에서 관리한다.

### 3.9 원본 설계의 LLM·Condition 책임과 실시간 Trace 복원

#### 문제

회고형 As-built 구현이 Search·Question 결과 뒤마다 `decide_next_action()`을 다시 호출해 최종 `none`까지 LLM이 판단하도록 바뀌어 있었다. 후보 경로는 반대로 코드가 계산했고, Question의 Q1·Q2·Q3는 실제 책임이 분리되지 않았다. 브라우저 Trace도 Graph 완료 후 수집된 이벤트를 110ms 간격으로 재생했다.

#### 결정

- 원본 설계를 구현의 최우선 기준으로 되돌린다.
- Tool 1 후보 결과의 `none/one/many` 해석은 LLM이 구조화해 반환한다.
- Search·Question LLM은 `response + follow_up`을 함께 반환한다.
- 하네스의 고정 Condition이 `follow_up`을 읽어 LLM 판단 복귀 또는 END를 선택한다.
- 최종 종료 확인만을 위한 별도 LLM `none` 호출을 제거한다.
- Question은 Q1 원문 조회, Q2 결과별 Context 조립, Q3 답변 생성으로 실제 책임을 나눈다.
- Graph Runtime 공통 처리를 별도 업무 Node나 `business_checkpoint` 이벤트로 만들지 않는다.
- LangGraph debug event는 SSE로 즉시 브라우저에 전달하고 인위적 재생 지연을 제거한다.

## 4. 현재 설계에 남은 핵심 결정

| 주제 | 채택한 결정 | 채택하지 않은 방향 |
|---|---|---|
| POC 구조 | 독립 Python·LangGraph | `zen-ai` 제품 골격의 사전 복제 |
| 목표 종료 | `response.follow_up=false → 고정 Condition → none/END` | 최종 `none` 확인용 LLM 호출, 별도 `done` 상태 |
| 녹화 | LLM 없는 결정적 Workflow | LLM의 자유로운 녹화 상태 조작 |
| Model | 실제 DeepSeek + rule-based 대역 | 실제 API만으로 모든 테스트 수행 |
| 원문 | Node 지역 Context에서만 사용 | Agent State·Checkpoint 저장 |
| 권한 | Repository/Backend에서 검색·조회 시 검증 | LLM 판단에 권한 위임 |
| HITL | 여러 후보와 새 ID 추가·대체에 사용 | 동일 허용 ID에도 반복 질문 |
| Checkpoint 표시 | 결과 반영 Snapshot을 업무 경계로 해석하고 기술 Snapshot은 기본 숨김 | 별도 `graph_runtime_checkpoint` 업무 Node |
| Trace 전송 | SSE Graph 이벤트 실시간 표시 | 실행 완료 후 110ms 사후 재생 |
| 제품 이식 | 책임·계약·테스트를 번역 | Python 코드를 TypeScript로 줄 단위 복사 |
| Git 작업 | 현재 작업공간에서 사용자 요청 범위만 수정 | 자동 branch/worktree/commit/PR |

## 5. 현재 문서 체계

### 저장소 안

- `AGENTS.md`: 이후 Codex 작업에 적용할 프로젝트 지침
- `2026-08-31-meeting-agent-poc-as-built-design.md`: 현재 구현의 규범적 As-built 명세
- 이 문서: 변화 과정과 결정 이유
- `README.md`: 빠른 프로젝트 소개와 실행 진입점

### Obsidian

- `1. POC 설계 기준.md`: 추진 배경과 검증 기준
- `2. POC 구조와 설계 대응.md`: 설계와 Graph 구조 대응
- `3. POC 실행 및 테스트 방법.md`: 환경, CLI와 테스트
- `4. POC 전체 구조와 코드 해설.md`: 파일·코드 관점 설명
- `5. zen-ai 현행 구조와 향후 이식 전략.md`: 제품 이식 경계
- `6. 발표용 로컬 Trace 시연 가이드.md`: 시연 순서와 Trace 설명

저장소 명세는 다음 세션의 작업 기준이고, Obsidian 문서는 설계 설명·발표·팀 공유 자료다. 설계 계약이 바뀌면 양쪽의 관련 문서를 함께 갱신한다.

## 6. Git 이력으로 확인되는 기준점

현재 저장소 이력에는 다음 기준점이 남아 있다.

| Commit | 기록된 의미 |
|---|---|
| `b9c839e` | Obsidian 설계 문서에 맞춘 수정 |
| `c32e5f1` | 1차 POC 및 DeepSeek v4 pro 테스트 |
| `ab0d23b` | 2차 POC 및 DeepSeek v4 pro 테스트 |

기준일 현재 이 Commit 이후에도 Agent 상태 용어, Trace와 HITL 관련 작업 파일에 커밋되지 않은 사용자 변경이 존재한다. 이 문서는 해당 현재 작업 트리까지 읽어 재구성했으며, 문서 작성을 이유로 기존 변경을 되돌리거나 별도 Commit을 만들지 않는다.

## 7. 다음 작업에서 사용하는 방법

새 Codex 세션에서는 다음 순서로 맥락을 복원한다.

1. 저장소 루트의 `AGENTS.md`를 따른다.
2. 현행 As-built 명세를 읽는다.
3. 결정 배경이 필요한 경우 이 변화 과정 문서를 읽는다.
4. 관련 코드와 테스트로 현재 상태를 확인한다.
5. 변경이 설계 계약에 영향을 주면 브레인스토밍을 거쳐 문서와 테스트를 함께 갱신한다.

Superpowers는 작업 절차를 제공하지만 과거 대화를 자동으로 기억하지 않는다. 이 문서 체계가 새 세션에 필요한 지속 가능한 맥락을 제공한다.
