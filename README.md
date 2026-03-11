# AI Mesh

텔레그램 기반 멀티 에이전트 오케스트레이션. AI 에이전트들이 자율적으로 협업하여 작업을 수행하고, 사람은 텔레그램 그룹 채팅으로 방향을 지시합니다.

## 아키텍처

```
텔레그램 그룹 채팅
        │
   TelegramBot (자연어 + 명령어)
        │
   Orchestrator (Python)
        │
   ┌────┴────┐
   PM (tmux)  Workers (tmux)
   │          │
   Claude Code / Codex / Gemini
```

- **PM 에이전트**: Claude Code, Codex, Gemini 중 하나를 tmux 세션에서 실제 CLI 프로세스로 실행. 작업 분해, 워커 배정, 진행 추적을 담당.
- **Worker 에이전트**: 코드를 구현하는 Coder와 분석을 수행하는 Researcher.
- **Orchestrator**: PM 생명주기, 메시지 라우팅, 작업 상태를 관리하는 Python 프로세스.
- **3계층 메모리**: 영속 정체성(L3), 누적 지식(L2), 일일 에피소드(L1), 작업 컨텍스트(L0).
- **텔레그램 디스플레이**: 모든 에이전트 활동을 그룹 채팅에 표시.

## 사전 요구 사항

- Python 3.12+
- tmux
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) (`claude` 명령어) 및/또는 [Codex CLI](https://github.com/openai/codex) (`codex` 명령어)
- 텔레그램 봇 토큰 ([@BotFather](https://t.me/BotFather)에서 발급)
- Anthropic API 키 (레거시 `anthropic` 엔진 모드에서만 필요)

## 시작하기

### 1단계: 설치

```bash
git clone <repo-url>
cd aimesh

python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

pip install -e ".[dev]"
```

### 2단계: 텔레그램 봇 생성

1. 텔레그램에서 [@BotFather](https://t.me/BotFather)에게 메시지 전송
2. `/newbot` 전송 후 안내에 따라 봇 생성
3. **봇 토큰** 복사 (예: `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`)
4. 텔레그램 **그룹 채팅**을 만들고 봇을 추가
5. **그룹 채팅 ID** 확인 ([@RawDataBot](https://t.me/RawDataBot) 사용 또는 그룹 메시지 전달)

### 3단계: 셋업 위자드 실행

```bash
python -m aimesh.main
```

첫 실행 시 `.env` 파일이 없으면 CLI 셋업 위자드가 자동으로 시작됩니다:

```
============================================================
  AI Mesh Setup Wizard
  First-run configuration
============================================================

Running pre-flight checks...
  ✓ Python 3.12+
  ✓ tmux installed

Step 1: PM Engine Selection
  Detected CLI tools:
    Claude Code     (claude)     ... FOUND
    Codex           (codex)      ... not found
    Gemini CLI      (gemini-cli) ... not found

  Choose engine (1-4, default: 1): 1

Step 2: Team Configuration
  How many teams? (1-5, default: 1): 1

  Team Setup
  Team name: my-project
  Team purpose: SaaS 제품 개발
  Bot token: ********
  Bot validated: @my_aimesh_bot
  Group chat ID: -1001234567890
  Group chat validated!

============================================================
  Setup Complete!
  Engine: Claude Code
  Team: my-project
    Config: orgs/my-project/config.yaml
    Soul:   orgs/my-project/soul.md
  .env file generated.
============================================================
```

위자드가 생성하는 파일들:
- `.env` — 봇 토큰, 그룹 채팅 ID, 조직 ID
- `orgs/{org_id}/config.yaml` — PM 엔진, 에이전트 구성, 워크스페이스 경로
- `orgs/{org_id}/soul.md` — PM 성격 / 팀 방향성
- `orgs/{org_id}/souls/` — 에이전트별 성격 파일

위자드 완료 후 봇이 자동으로 시작되어 텔레그램 그룹에서 수신 대기합니다.

### 4단계: 사용하기

텔레그램 그룹에서 메시지를 보냅니다:

```
/task JWT 인증이 포함된 로그인 API 만들어줘
```

PM 에이전트가 작업을 분해하고, coder/researcher 워커에 배정한 뒤, 진행 상황을 그룹에 보고합니다. 자연어로 대화해도 됩니다 — NL 핸들러가 메시지를 PM에 자동 라우팅합니다.

## 조직 추가하기

봇이 실행 중인 상태에서 두 가지 방법으로 조직(팀)을 추가할 수 있습니다:

### 빠른 추가: `/addteam` (그룹 채팅에서)

봇이 있는 그룹 채팅에서 전송:

```
/addteam marketing codex
```

현재 그룹의 채팅 ID를 사용하여 `orgs/marketing/config.yaml`을 생성합니다. 봇을 재시작하면 활성화됩니다.

| 인자 | 필수 | 설명 |
|------|------|------|
| `name` | 예 | 팀 이름 (ASCII, org_id가 됨) |
| `engine` | 아니오 | `claude_code` (기본값), `codex`, `gemini`, `anthropic` |

조직마다 다른 엔진을 지정할 수 있습니다:

```
/addteam backend claude_code    → Claude Code 엔진
/addteam frontend codex         → Codex 엔진
/addteam research gemini        → Gemini 엔진
```

### 전체 위자드: `/setup` (봇 DM에서)

봇에게 직접 메시지(개인 채팅)를 보내고 `/setup`을 전송합니다. 대화형 위자드가 안내합니다:

1. 사전 검사
2. 조직 이름
3. 그룹 채팅 ID
4. 워크스페이스 경로
5. 에이전트 유형 (coder, researcher, 또는 둘 다)
6. 확인

### 새 조직 활성화

조직을 추가한 후:

1. `.env`에 조직 ID 추가:

```bash
# 단일 조직
AIMESH_ORG_ID=my-project

# 복수 조직
AIMESH_TEAM_IDS=my-project,marketing,design
```

2. 봇 재시작:

```bash
python -m aimesh.main
```

각 조직은 독립된 PM 에이전트, 워커 에이전트, 메모리를 갖습니다. 멀티 조직 모드에서는 @멘션, 도메인 키워드, 작업 컨텍스트에 기반하여 메시지가 적절한 PM에 라우팅됩니다.

## 수동 설정

위자드를 건너뛰려면:

```bash
cp .env.example .env
# .env 파일을 편집하여 토큰 입력
```

### 환경 변수 (.env)

| 변수 | 필수 | 설명 |
|------|------|------|
| `TELEGRAM_BOT_TOKEN` | 예 | @BotFather에서 발급받은 봇 토큰 |
| `TELEGRAM_GROUP_CHAT_ID` | 예 | 에이전트 표시용 그룹 채팅 ID |
| `ADMIN_USER_IDS` | 아니오 | 관리자 텔레그램 사용자 ID (쉼표 구분) |
| `AIMESH_ORG_ID` | 아니오 | 조직 ID (기본값: `_default`) |
| `AIMESH_TEAM_IDS` | 아니오 | 멀티 팀 모드용 조직 ID (쉼표 구분) |
| `ANTHROPIC_API_KEY` | `anthropic` 엔진만 | 레거시 API 모드용 API 키 |
| `MAX_BUDGET_PER_TASK_USD` | 아니오 | 작업당 최대 비용 (기본값: $5) |

### 조직 설정 (orgs/{org_id}/config.yaml)

```yaml
org_id: my-project
org_name: "My Project"

telegram:
  group_chat_id: -100XXXXXXXXXX
  admin_user_ids: [123456789]

pm:
  engine: "claude_code"  # claude_code | codex | gemini | anthropic (레거시 API)
  model: "claude-sonnet-4-20250514"

engine_config:
  claude_code: "claude --dangerously-skip-permissions"
  codex: "codex --full-auto"
  gemini: "gemini-cli"

agents:
  - id: "coder-1"
    type: "coder"
    soul_file: "souls/coder.md"
    capabilities: ["code", "implement", "fix", "refactor"]
  - id: "researcher-1"
    type: "researcher"
    soul_file: "souls/researcher.md"
    capabilities: ["research", "analyze", "compare"]

workspace_path: "./workspace"
```

### 엔진 선택

| 엔진 | 필요한 CLI | 설명 |
|------|-----------|------|
| `claude_code` | `claude` | Claude Code — 전체 도구 접근, 파일 편집, bash |
| `codex` | `codex` | OpenAI Codex CLI — full-auto 모드 |
| `gemini` | `gemini-cli` | Google Gemini CLI |
| `anthropic` | 없음 (API) | 레거시 API 전용 모드 (tmux 없음, 상태 비저장) |

조직 설정의 `pm.engine`으로 지정합니다. PM은 tmux 세션에서 실제 CLI 프로세스로 실행되며, 세션 간 메모리가 유지됩니다. 워커도 동일한 엔진을 사용합니다.

## 텔레그램 명령어

| 명령어 | 설명 |
|--------|------|
| `/task <설명>` | 새 작업 제출 |
| `/status` | 활성 작업 현황 보기 |
| `/agents` | 등록된 에이전트 보기 |
| `/approve <task_id>` | 완료된 작업 승인 |
| `/reject <task_id> [피드백]` | 거절 후 재작업 요청 |
| `/cancel <task_id>` | 작업 취소 |
| `/addteam <이름> [엔진]` | 새 팀 추가 (관리자 전용) |
| `/setup` | 셋업 위자드 시작 (DM 전용) |

자연어로 대화해도 됩니다 — NL 핸들러가 메시지를 PM에 자동 라우팅합니다.

## 전체 워크플로우

```
1. 셋업        python -m aimesh.main → 위자드 → 봇 시작
                       │
2. 작업 제출    /task "로그인 API 만들어줘" (또는 자연어)
                       │
3. PM 분해     PM이 작업을 하위 작업으로 분해
                       │
4. 워커 실행    coder-1이 구현, researcher-1이 분석
                       │
5. 리뷰        PM이 결과 제시 → /approve 또는 /reject
                       │
6. 반복        거절? → 재작업. 승인? → 완료.
```

나중에 팀을 추가하려면:

```
/addteam design claude_code   → .env 편집 → 봇 재시작
```

## 개발

```bash
# 테스트 실행
pytest tests/ -v

# 린트
ruff check src/
```

## 프로젝트 구조

```
src/aimesh/
  main.py              # 진입점, 시작/종료
  config.py            # Pydantic 설정 + 조직 설정
  core/
    bus.py             # AbstractMessageBus + AsyncioMessageBus
    message.py         # MeshMessage 프로토콜
    task.py            # 작업 상태 머신
    context.py         # 공유 컨텍스트 저장소
    registry.py        # 에이전트 레지스트리
    logging.py         # 구조화 로깅 (structlog)
  agents/
    base.py            # BaseAgent ABC
    pm.py              # PM 에이전트 (레거시 API 모드)
    coder.py           # Coder 에이전트
    researcher.py      # Researcher 에이전트
    executor.py        # LLM 실행 래퍼
  tmux/
    session.py         # 비동기 tmux CLI 래퍼
    protocol.py        # 파일 아웃박스 프로토콜 (atomic JSON)
    bridge.py          # 하이브리드 IPC (send-keys + 파일 아웃박스)
    orchestrator.py    # TmuxPMOrchestrator (v3 PM)
    lifecycle.py       # 세션 상태 머신
  memory/
    manager.py         # 3계층 메모리 (L0-L3)
    templates.py       # 기본 정체성 템플릿
  routing/
    router.py          # 멀티 PM 라우팅 (@멘션, 작업 컨텍스트, 최소 부하)
  tasks/
    decomposer.py      # 작업 분해
    tracker.py         # 진행 추적
    consensus.py       # 검증 + 완료
  telegram/
    bot.py             # 텔레그램 봇 설정
    handlers.py        # 명령어 핸들러
    nl_handler.py      # 자연어 메시지 핸들러
    formatter.py       # 페르소나 접두사 포맷팅
    display.py         # 속도 제한 디스플레이 레이어
    setup_wizard.py    # 텔레그램 DM 셋업 위자드
  nl/
    classifier.py      # 의도 분류
    context.py         # 대화 컨텍스트
  cli/
    wizard.py          # 첫 실행 CLI 셋업 위자드
orgs/
  _default/
    config.yaml        # 기본 조직 설정
    soul.md            # 기본 PM 성격
    souls/             # 에이전트별 성격 파일
```
