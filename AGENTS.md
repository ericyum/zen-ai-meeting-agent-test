# 회의록 Agent POC 작업 지침

## 프로젝트 목적

이 저장소는 기존 회의록 Agent 설계가 **Python과 LangGraph로 기술적으로 성립하는지** 검증하는 독립 POC다.

- 실제 `zen-ai` 제품 구조를 이 저장소에 복제하는 것이 목적이 아니다.
- POC에서 검증한 책임, 상태 전이, 보안 경계와 테스트 시나리오를 향후 제품 구조에 맞게 이식한다.
- 구현 판단 시 기존 회의록 Agent 설계 문서, 현재 코드와 테스트, `docs/superpowers/specs`의 현행 명세 순으로 함께 확인한다.

## 작업 전 확인

1. `docs/superpowers/specs/2026-08-31-meeting-agent-poc-as-built-design.md`를 읽는다.
2. 변경 이유가 필요하면 `docs/superpowers/specs/2026-08-31-meeting-agent-poc-evolution.md`를 읽는다.
3. 관련 코드와 테스트를 직접 확인하며 문서만으로 현재 동작을 단정하지 않는다.
4. 설계 또는 동작을 바꾸는 작업에는 Superpowers의 브레인스토밍 절차를 적용해 사용자와 방향을 합의한다.

## 구현 경계

- POC는 Python 3.11 이상과 LangGraph 기반의 독립 프로젝트로 유지한다.
- 최상위 Agent Graph와 Search·Question Subgraph는 `agent_graph.py`에 둔다.
- 녹화는 LLM 자유 판단이 아니라 `recording_graph.py`의 결정적 Workflow로 유지한다.
- 실제 제품 UI, 인증·권한 Backend, 회의록 DB와 녹화 모달은 이 저장소에서 대역 경계다.
- `RuleBasedMeetingModel`은 오프라인 테스트 대역이고 `DeepSeekMeetingModel`은 실제 API Adapter다.
- `zen-ai` 이식 조사 결과는 참고하되, 제품 코드를 이 POC에 그대로 복제하지 않는다.

## 설계 불변 조건

- 영속 Agent State의 핵심 필드는 `ScratchPad`, `authorized_meeting_ids`, `recording_modal_status`다.
- `user_id`, `thread_id`, 현재 `request`는 비영속 Runtime Context로 전달한다.
- API 키와 회의록 원문은 Agent State, Checkpoint, Trace에 저장하지 않는다.
- 회의록 권한은 LLM이 아니라 Repository/Backend 경계에서 검증하고 원문 조회 시 다시 검증한다.
- 여러 후보의 선택과 기존 허용 ID의 추가·대체는 HITL 경계로 처리한다.
- 같은 허용 ID를 다시 선택한 경우 불필요한 추가·대체 HITL을 만들지 않는다.
- 목표 판단의 종료 상태는 설계 용어인 `none`을 사용한다. 별도의 `done` 상태를 도입하지 않는다.
- `graph_runtime_checkpoint`는 설계상의 업무 경계다. LangGraph가 매 Super-step 뒤 만드는 기술 State Snapshot과 혼동하지 않는다.
- 기술 Snapshot은 Trace 화면에서 기본적으로 숨기고 필요할 때만 표시한다.

## 보안과 설정

- DeepSeek API 키를 소스, 테스트 fixture, 문서 예시의 실제 값 또는 Git 추적 파일에 기록하지 않는다.
- 키는 실행 시 지정한 외부 키 파일 또는 환경변수를 통해서만 읽는다.
- 로그, 오류 메시지와 Trace에 API 키 및 회의록 원문이 노출되지 않는지 확인한다.
- 실제 API 테스트는 비용과 네트워크를 사용한다는 사실을 명시하고, 키가 없으면 Skip 가능한 상태를 유지한다.

## 테스트와 문서화

- 기본 검증 명령은 `./.venv/Scripts/python.exe -m unittest discover -s tests -v`다.
- 실제 DeepSeek 통합 테스트와 오프라인 테스트를 구분한다.
- 기능 변경 시 관련 단위 테스트를 먼저 추가하거나 수정하고 전체 오프라인 테스트를 실행한다.
- 설계 계약이 바뀌면 현행 As-built 명세와 관련 Obsidian 문서를 함께 갱신한다.
- 실제 구현과 제품 경계의 대역을 문서에서 명확히 구분한다.
- 완료를 보고하기 전에 실행한 검증 명령과 결과를 확인한다.

## Git 작업 방침

- 현재 작업 디렉터리에서 작업한다.
- 별도 브랜치나 Git worktree를 생성하지 않는다.
- 사용자의 명시적인 요청 없이 커밋하지 않는다.
- Pull Request와 Draft Pull Request를 생성하지 않는다.
- 사용자의 기존 변경을 되돌리거나 덮어쓰지 않는다.
- 파괴적 Git 명령과 자료 삭제는 사용자의 명시적인 승인 없이 수행하지 않는다.

위 방침과 사용자의 현재 요청이 충돌하면 사용자의 명시적인 요청을 우선하되, 위험하거나 파괴적인 작업은 실행 전에 범위와 영향을 확인한다.
