# Agent Platform 모델 기본값

이 문서는 `eks-demo` 운영자가 모델을 배정·변경할 때 사용합니다. 배포 값의 원본은
[`env/eks-demo.yaml`](../env/eks-demo.yaml)입니다.

## 호출 경계

모든 소비 서비스는 **Agent Models의 선택 ID**를 사용합니다. 공급자 연결·자격 증명·wire ID·
활성 선택·요청 승인·호출별 사용량은 Models가 소유합니다. 소비 서비스에 공급자 API 키나
OpenRouter 직접 URL을 넣지 않습니다. 공급자는 **OpenRouter를 우선**합니다.
`selfhosted/qwen3.8-flash-next`는 외부 모델을 사용하지 않는 Agent의 예외이며 이 호출도 Models를 통과합니다.

Models에서 모델을 수집한 것만으로 실행할 수 없습니다. 먼저 선택·활성화하고 역할에 필요한
작업, 도구 호출, 구조화 출력, 이미지 입력, 추론과 가격을 확인합니다. 권한 오류·요금 한도·
결과 불명 요청을 다른 공급자로 자동 재전송하지 않습니다.

## 역할과 기본 모델

`openrouter/`는 Models의 공급자 연결 ID입니다. 아래 ID는 공급자에 보낼 wire ID와 다릅니다.

| 서비스·역할 | Models 선택 ID | 선정 이유 |
| --- | --- | --- |
| Studio 기본·일반, Helper·보고서 | `openrouter/deepseek-v4.1-flash` | 긴 문맥, 도구 호출과 일반 분석을 낮은 입력 비용으로 처리 |
| Studio 빠른 응답, Memory Dreaming, Knowledge 지식 추출 | `openrouter/gpt-6-luna` | 반복 호출·분류·구조화 추출에 적합한 저비용 모델 |
| Studio 코딩·이미지 이해, Forge 코딩 프로필 | `openrouter/deepseek-v4.1-flash` | 코드 수정과 다단계 작업, 이미지 이해 지원 |
| Studio 복잡한 추론 | `openrouter/glm-5.3` | 복잡한 추론을 별도 tier에 배정; 단순 요청에는 사용하지 않음 |
| Studio 코드 리뷰 | `openrouter/gpt-6.1-sol` | 검토 품질을 위한 고정 모델. 일반 호출의 기본값으로 사용하지 않음 |
| Forge 리뷰 프로필 | `openrouter/deepseek-v4.1-flash` | 코드 분석·도구 호출 지원, 반복 검토 비용 절감 |
| Knowledge 원문 검증·회의록 | `openrouter/deepseek-v4.1-flash` | 원문 근거 확인과 긴 전사 요약. 지식 추출 모델과 분리 |
| Studio·Memory 결정 | `openrouter/jev-1.14` | 자유문 대신 구조화된 선택·관련성 판단 |
| Studio·Memory·Knowledge embedding | `openrouter/text-embedding-3-small` | 이미 저렴하며 기존 벡터와 차원 계약 유지 |
| Studio·Memory·Knowledge rerank | `openrouter/rerank-v3.5` | 검색 결과 관련성 재정렬 전용 |
| Knowledge 기본 전사 | `openrouter/mai-transcribe-2` | 다국어 전사, 낮은 오디오 분당 비용 |
| Studio 이미지 생성·편집 | `openrouter/gpt-image-2.5-flare` | 이미지 생성과 기존 이미지 편집을 같은 모델로 제공 |
| 자체 호스팅 전용·간호 인수인계 Agent | `selfhosted/qwen3.8-flash-next` | 외부 모델 미사용 정책 유지 |

GPT-6 Sol·Astra와 Opus·Sonnet의 기존 중앙 선택 및 Forge 채널은 참조 중인 Agent·Workspace를
끊지 않도록 유지합니다. 활성 목록에 남는 것과 기본 모델로 배정하는 것은 다릅니다.

## 가격과 공개 범위

아래 값은 2026-11-03에 확인한 Models/OpenRouter 카탈로그의 **입력 / 출력 100만 토큰당 USD**입니다.
실제 청구는 공급자, 캐시, 추론 토큰, 입력 길이, 할인 시간과 이미지·오디오 단위에 따라 달라집니다.
단가는 운영 당시 Models에서 다시 확인합니다. 같은 입력의 속도·정확도 측정 없이 성능 개선을 주장하지 않습니다.

| 모델 | 입력 / 출력 | 공개 범위 |
| --- | --- | --- |
| DeepSeek V4.1 Flash | $0.045 / $0.50 | 코드·가중치 MIT |
| GPT-6 Luna | $0.10 / $0.50 | 비공개 상용 |
| MiniMax M3.1 Flash | $0.15 / $0.60 | 가중치 공개, MiniMax Community License 조건 적용 |
| GLM 5.3 | $0.08 / $3.60 | 가중치 공개, GLM-5.3 License 조건 적용 |
| GPT-6.1 Sol | $2 / $10 | 비공개 상용 |

Embedding은 입력 100만 토큰당 $0.02, rerank는 검색 단위당 $0.001,
Jev는 입력 100만 토큰당 $0.042, MAI-Transcribe 2는 오디오 분당 $0.001667입니다.
이미지 생성은 별도 이미지 토큰 과금이므로 텍스트 출력 단가와 비교하지 않습니다.
자체 호스팅의 등록 단가 0은 서버·GPU 운영비가 없다는 뜻이 아닙니다.

Dreaming 기본값은 Memory `v0.73.3`의 동일한 합성 평가 4개를 배포 환경에서 Models를 통해
각 1회 실행해 비교했습니다. 실제 기억은 읽거나 변경하지 않았습니다. 두 모델 모두 오래된 사실
제거·선호 보존·관련 기억 병합·주제 분리 기준을 통과했고, Models가 각 12개 호출의 성공과 비용을 기록했습니다.

| Dreaming 모델 | 평가 통과 | 기록된 합계 비용 | 4개 평가 경과 시간 합계 |
| --- | --- | --- | --- |
| Anthropic 직접 연결 Opus 5.5 | 4/4 | $0.156928 | 54.70초 |
| OpenRouter GPT-6 Luna | 4/4 | $0.0032272 | 66.99초 |

이 표본에서 비용은 97.94% 낮았고 시간은 22.5% 길었습니다. 반복 측정이나 전체 실제 기억에 대한
품질 보증은 아닙니다. 기본값은 속도 향상이 아니라, 해당 평가의 품질 기준을 만족하면서 반복 처리 비용을
낮추는 선택입니다. 평가 원본은 `agent-memory/tests/dream-quality.integration.test.ts`입니다.
평가한 Memory 소스는 `415d244f126be7399140f78ef720e389d22be52d`입니다. 재현할 때는
Memory 저장소에서 `MODELS_URL`과 Memory OIDC client를 안전하게 주입한 뒤 다음 명령을 실행합니다.
두 모델을 같은 fixture·소스·환경에서 비교하고, Models의 service 호출 사용량과 비용을 함께 확인합니다.
실제 공급자를 호출하므로 요금이 발생합니다.

```bash
DREAM_EVAL_LIVE=1 DREAM_MODEL=anthropic/claude-opus-5.5 \
  pnpm exec vitest run tests/dream-quality.integration.test.ts
DREAM_EVAL_LIVE=1 DREAM_MODEL=openrouter/gpt-6-luna \
  pnpm exec vitest run tests/dream-quality.integration.test.ts
```

코드·가중치 공개와 제한 없는 사용은 다릅니다. MiniMax·GLM·Qwen의 상업 이용·서비스 제공 조건은
해당 라이선스를 확인합니다. OpenRouter를 쓰는 공개 모델도 외부 API 호출 비용이 발생합니다.

공식 근거:
[DeepSeek](https://openrouter.ai/deepseek/deepseek-v4.1-flash),
[MiniMax](https://openrouter.ai/minimax/minimax-m3.1-flash),
[GLM](https://openrouter.ai/z-ai/glm-5.3),
[GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna),
[MAI-Transcribe 2](https://microsoft.ai/models/mai-transcribe-2/),
[DeepSeek 라이선스](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash),
[MiniMax 라이선스](https://huggingface.co/MiniMaxAI/MiniMax-M3.1-Flash/blob/main/LICENSE),
[GLM 라이선스](https://huggingface.co/zai-org/GLM-5.3/blob/main/LICENSE),
[Qwen 라이선스](https://huggingface.co/Qwen/Qwen3.8-Flash-Next/blob/main/LICENSE).

## 설정 원천과 우선순위

| 대상 | Git 원본 | 적용 방식 |
| --- | --- | --- |
| Studio 공통 역할·라우팅 | `agent_studio.model_usage` | `MODEL_USAGE_DEFAULTS` JSON. 저장된 override → 배포 기본값 → 미설정 |
| Memory 역할 | `agent_memory.models` | `EMBEDDING_MODEL`, `RERANKER_MODEL`, `DECISION_MODEL`, `DREAM_MODEL`. 저장된 override가 우선 |
| Knowledge 검색·추출·검증 | `agent_knowledge.models` | `EMBEDDING_MODEL`, `RERANKER_MODEL`, `EXTRACTION_MODEL`, `VERIFICATION_MODEL`. 저장된 override가 우선 |
| Knowledge 오디오 처리 옵션 | `agent_knowledge.models.transcription`, `summary` | `AUDIO_PROCESSING_OPTIONS`. 모델별 처리 옵션이며 실제 작업 모델 선택과 별개 |
| Forge 런타임 채널 | `agent_forge.model_roles` | ConfigMap의 `FORGE_MODEL_CHANNELS`. 모든 env가 선언해야 하며 역할이 없으면 `[]` |
| Agent별 모델·이미지 모델·자동 선택 | Studio Agent 설정 | 소유자가 저장. 배포 기본값으로 기존 Agent를 자동 덮어쓰지 않음 |
| Forge 프로필·Knowledge 오디오 프로필 | 각 서비스의 프로필 | 소유자가 모델 선택을 저장. 접수한 작업과 기존 Workspace snapshot은 유지 |

Studio의 배포 기본값은 `MODEL_USAGE_DEFAULTS`를 지원하는 `v0.221.0` 이상을 먼저 배포한 뒤 설정합니다.
`modelRouting`은 전체 정책 객체이며 tier별로 저장값과 병합하지 않습니다. 모든 ID는 Models의
현재 승인 목록에서 검사합니다. 기본값을 선언해도 Models 등록이나 사용자 권한이 생기지 않습니다.

## 적용 절차

1. Models에서 표의 모델이 활성 상태이고 필요한 작업·기능·가격을 지원하는지 확인합니다.
2. `env/eks-demo.yaml`을 수정하고 `bash scripts/build.sh`,
   `python3 scripts/validate.py --all-charts`, `python3 -m pytest`를 실행합니다.
   플랫폼별 생성 values는 직접 수정하지 않습니다.
3. 검증한 PR을 병합하고 Argo CD revision·Sync·Health를 확인합니다. Memory와 Knowledge web·worker,
   Forge의 새 Pod가 새 ConfigMap을 읽는지 확인합니다. 기존 Secret·DB·벡터·파일은 보존합니다.
4. Studio `/settings/model-usage`에서 **배포 기본값 사용**을 실행합니다. 이 동작은 배포에 선언된
   항목의 override만 해제합니다. 다른 Embedding으로 바뀌는 경우 먼저 검색 모델 변경·재색인을 합니다.
5. Memory·Knowledge의 해당 모델 override는 **환경 변수 값 사용**으로 해제합니다.
   override 해제는 저장 후 서비스 재시작이 필요하며 Knowledge는 worker도 함께 적용합니다.
6. 기존 운영 Agent 중 Helper·보고서는 DeepSeek, Code Agent는 MiniMax, Audio Inbox는 Luna를
   기본 모델로 저장합니다. 자동 라우팅 사용 여부와 자체 호스팅 전용 정책은 유지합니다.
   Studio Code Review는 GPT-6.1 Sol 고정을 유지합니다.
7. Forge의 코딩 프로필은 MiniMax, 리뷰 프로필은 DeepSeek로 새 revision을 저장합니다.
   Knowledge `default` 오디오 프로필은 MAI-Transcribe 2와 DeepSeek로 저장합니다.
   이미 접수한 작업의 모델을 바꾸지 않으며, 기존 Workspace에 새 프로필을 쓰려면 서비스의
   명시적인 프로필 선택 절차를 따릅니다.
8. 설정을 새로 조회하고 Models 사용량에서 서비스·모델·요청·종료 상태·비용을 확인합니다.
   텍스트·도구 호출·구조화 추출·전사·회의록을 역할에 맞게 검증합니다. 실패 시 같은 유료 작업을
   반복 실행하지 말고 요청 ID의 오류·권한·공급자 상태를 먼저 확인합니다.

모델 품질이나 연결에 문제가 있으면 해당 역할만 검증된 이전 선택으로 되돌립니다.
환경 기본값 rollback은 Git 변경을, 저장된 Agent·프로필 rollback은 각 서비스의 설정을 사용합니다.
OpenRouter 요금 한도나 인증 문제가 있으면 해당 공급자 상태를 먼저 복구하고, 확인되지 않은
다른 공급자로 자동 전환하지 않습니다.
