# Agent Platform 배포

운영 클러스터는 **EKS `eks-demo`**(Auto Mode, phase `prod`)입니다. 같은 클러스터의 Argo CD가 `apps-eks.yaml`로
`apps/eks/`의 ApplicationSet을 동기화하며, values는 `values.yaml` → `values-prod.yaml` → `eks/values-eks-demo.yaml`
순서로 적용됩니다. `eks-demo-a`·`eks-demo-b`는 향후 서비스 클러스터 선언입니다.
렌더 성공은 실제 배포·격리 검증을 뜻하지 않습니다.

| 저장소 | 책임 |
| --- | --- |
| `terraform-env-demo` | AWS 리소스, IAM, ECR, EKS Pod Identity, S3 |
| `argocd-env-addons` | Argo CD, External Secrets, Istio Gateway, external-dns, CNI 정책, 관측성 |
| `argocd-env-apps` | 여섯 앱, worker, 실행 namespace/RBAC/NetworkPolicy, PostgreSQL, Runway 풀·tenant admission 정책 |

| 앱 | 공개 호스트 | 처리 책임 |
| --- | --- | --- |
| Studio | `studio.agrt.dev` | Agent·Chat, 승인, MCP로 Memory·Knowledge·Forge 호출 |
| Memory | `memory.agrt.dev` | 사용자·팀·조직의 단기/장기 메모리, recall/remember/handoff |
| Knowledge | `knowledge.agrt.dev` | 문서·검색·지식 그래프, 원본 녹음·전사·회의록, durable queue worker |
| Forge | `forge.agrt.dev` | Workspace·Sandbox·Run·Session·Git, native 모델 Gateway |
| Models | `models.agrt.dev` | 공급자 연결·자격 증명, 모델 선택, 공급자 호출, 사용량 원장 |
| Runway | `runway.agrt.dev` | 앱 리소스(PostgreSQL database·S3 prefix)·Variable 저장과 전달. [Agent Runway](#agent-runway) |
| AgentOps | `agentops.agrt.dev` | 모든 서비스 화면의 통합 콘솔. 서비스 데이터를 저장하지 않고, 로그인 세션을 Identity에서 60초 토큰으로 바꿔 각 서비스 API를 부른다 |
| AgentOps Identity | 없음. 콘솔이 `agentops.agrt.dev`의 `/identity/*`와 `/api/auth/callback`을 중계한다 | 플랫폼 로그인, 사람·팀·Git 연결, 모든 서비스가 받는 토큰 발급. [로그인](#로그인과-모델-연결) |

공개 요청은 public ALB `demo`(`terraform-env-demo`의 `3-alb`, Route53 `*.agrt.dev`) → `istio-ingressgateway`(`istio-system`)
→ 각 앱의 HTTPRoute 순서로 전달됩니다.

모든 Agent 앱은 `.agrt.dev` 호스트 하나만 사용합니다. `AUTH_BASE_URL`(Studio는 `PUBLIC_BASE_URL`)과
HTTPRoute는 같은 호스트를 가리킵니다. Memory는 `AUTH_ALLOWED_HOSTS`도 같은 호스트로 렌더합니다.
프록시는 원래 `Host`를 전달해야 합니다.
`trusted_proxy_cidrs`는 실제 프록시 hop에 맞춰 EKS VPC CIDR(`10.10.0.0/16`)을 사용합니다.

## 로그인과 모델 연결

플랫폼 로그인은 AgentOps Identity가 담당합니다. Identity는 `charts/agentops`의 `identity` workload입니다
(image `agentops`, `AGENTOPS_ROLE=identity`, Service `agentops-identity:3000`). 클러스터 내부에서만 열리며,
콘솔이 공개 host의 `/identity/*`와 `/api/auth/callback`을 Identity로 중계합니다.
Agent 서비스는 외부 로그인 제공자와 연결하지 않고 Identity가 발급한 토큰만 받습니다.

Identity는 OIDC 제공자 하나(플랫폼 Dex)로만 로그인합니다. Google 로그인과 비밀번호 로그인은 없습니다.
설정은 `env/<cluster>.yaml`의 `agentops.identity`입니다.

| 설정 | 값 |
| --- | --- |
| `OIDC_ISSUER` | `agentops.identity.oidc_issuer`. eks-demo는 `https://sso.demo.opsp.dev`(LLDAP 앞의 Dex) |
| `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` | Dex client `agentops-identity`. `/k8s/<cluster>/agentops-identity/oidc-client-id`, `oidc-client-secret` |
| callback | `https://<agentops.hostname>/api/auth/callback`. Dex client의 redirect URI와 같습니다 |
| `ADMIN_EMAILS` | `/k8s/<cluster>/agentops-identity/admin-emails`. 관리자 이메일의 쉼표 구분 목록 |
| `IDENTITY_ENROLLMENT_DOMAINS` | `agentops.identity.enrollment_domains`. 목록이 비어 있으면 렌더하지 않습니다. eks-demo는 `[]` |

Identity는 첫 로그인에서 이메일이 `ADMIN_EMAILS`에 있거나 이메일 도메인이 `enrollment_domains`에 있을 때만
사람을 만듭니다. 둘 다 아니면 그 계정은 등록되지 않습니다.

`gen_values.py`는 렌더 전에 다음을 검사합니다.

- `validate_agentops_identity`: `oidc_issuer`는 `https://` URL, `import`는 `off`·`count`·`apply` 중 하나,
  `enrollment_domains`는 도메인 문자열 목록이어야 합니다.
- `validate_agent_oidc`: `agent_studio`, `agent_memory`, `agent_knowledge`, `agent_forge`, `agent_models`,
  `agent_runway`의 `oidc`, `google_sign_in`, `password_sign_in`, `sign_in_domains`, `git_hosts`를 거부합니다.
  로그인과 Git 호스트는 `agentops.identity`와 `agentops.git_hosts`에서만 선언합니다.

템플릿은 각 서비스에 다음 값을 고정으로 렌더합니다. 서비스에는 `OIDC_*` 설정이 없습니다.

| 설정 | Studio·Memory·Knowledge·Forge·Models·Runway | AgentOps 콘솔 |
| --- | --- | --- |
| `IDENTITY_ISSUER` | `https://<agentops.hostname>/identity` | - |
| `IDENTITY_URL` | `http://agentops-identity.agentops.svc.cluster.local:3000/identity` | `http://agentops-identity:3000/identity` |
| client secret | `IDENTITY_CLIENT_SECRET` ← `/k8s/<cluster>/agentops-identity/client-secret-<service>`. Models는 읽지 않습니다 | `IDENTITY_CLIENT_SECRET_CONSOLE` ← `client-secret-console` |
| `CONSOLE_BASE_URL` | `https://<agentops.hostname>` | - |

`<service>`는 `studio`, `memory`, `knowledge`, `forge`, `runway`입니다. Identity는 client 일곱 개(`console`, `studio`,
`memory`, `knowledge`, `forge`, `runway`, `models`)의 같은 secret을 `IDENTITY_CLIENT_SECRET_<CLIENT>`로 읽습니다.
NetworkPolicy `agentops`는 콘솔과 Identity의 3000 포트를 같은 namespace, `istio-system`, 여섯 서비스 namespace에만 엽니다.

Studio·Memory·Knowledge·Forge에는 `MODELS_URL=http://agent-models.agent-models.svc.cluster.local:3000`을 설정합니다.
Studio에는 브라우저용 `MODELS_PUBLIC_URL=https://<agent_models.hostname>`도 설정합니다.

공급자 주소·API 키·wire ID·가격은 Models만 소유합니다. 소비 서비스에는 공급자 설정을 주입하지 않습니다.
역할별 기본 모델, 선정 근거와 기존 설정 전환은 [모델 기본값](model-defaults.md)을 따릅니다.
Studio의 `agent_studio.model_usage`는 `MODEL_USAGE_DEFAULTS`로 렌더합니다. 저장된 override가
우선하며, Studio의 **배포 기본값 사용**으로 선언된 역할과 라우팅 정책을 배포 기본값에 연결합니다.
Memory·Knowledge의 모델 역할은 `env/<cluster>.yaml`의 `agent_memory.models`, `agent_knowledge.models`에
Models 선택 ID(`<provider>/<model>`)로 선언합니다. 선언하지 않은 역할은 렌더하지 않습니다.
Models에서 해당 선택을 먼저 활성화합니다. 각 서비스 **설정**의 override가 env보다 우선하므로
GitOps 값을 적용하려면 override를 reset합니다. Embedding 모델이나 차원을 바꾸면 기존 embedding을 다시 생성합니다.

| 키 | Memory env | Knowledge env |
| --- | --- | --- |
| `embedding` | `EMBEDDING_MODEL` | `EMBEDDING_MODEL` |
| `reranker` | `RERANKER_MODEL` | `RERANKER_MODEL` |
| `decision` | `DECISION_MODEL` | - |
| `dream` | `DREAM_MODEL`(Dreaming·세션 기억 추출) | - |
| `extraction` | - | `EXTRACTION_MODEL`(검증 모델을 따로 두지 않으면 검증도 사용) |
| `verification` | - | `VERIFICATION_MODEL`(원문과 추출 결과를 별도 모델로 검증) |
| `transcription`, `summary` | - | `AUDIO_PROCESSING_OPTIONS`의 기본 처리 옵션 항목 |

Knowledge의 전사·회의록 모델은 작업을 요청하는 쪽(Studio 등)이 작업마다 모델 ID로 고릅니다.
`AUDIO_PROCESSING_OPTIONS`는 그 모델의 처리 옵션만 정하며 모델을 선택하지 않습니다.

Forge의 `agent_forge.model_roles`는 ConfigMap의 `FORGE_MODEL_CHANNELS`로 렌더합니다.
선택 ID와 실행 한도는 비밀값이 아니므로 Git에서 관리합니다. Forge를 배포하는 env는 이 블록을 반드시 선언합니다.
선언이 없으면 렌더가 실패합니다.
각 항목은 `id`, `runtime`, `model`(Models 선택 ID), `protocol`과 선택 한도만 가집니다. 공급자 연결 정보가
있는 이전 `model-channels` 형식은 Forge가 시작 단계에서 거부합니다. 역할이 없으면 `[]`을 사용합니다.
저장된 Workspace 프로필과 접수한 작업의 모델은 채널 기본값과 별개입니다. 기존 채널 ID는
이를 참조하는 Workspace가 남아 있으면 유지합니다.

### Git 호스트

`agentops.git_hosts`는 사람이 콘솔 **프로필 → Git 계정**에서 연결할 Git 호스트를 선언합니다.
Identity가 이 목록을 `GIT_HOSTS`로 읽고 Git 연결을 저장합니다. Studio·Forge·Runway는 Identity에서 사용자의 Git token을
받습니다. 세 서비스에는 Git 호스트 설정이나 OAuth credential이 없습니다.
계약(필드 규칙, provider별 scope)은 agent-platform `docs/GIT_CONNECTIONS.md`를 따릅니다.

```yaml
agentops:
  git_hosts:
    - name: github              # GIT_HOST_SECRETS의 key와 같습니다.
      provider: github          # github, gitlab 또는 gitea
      origin: https://github.com
      clientId: <OAuth application의 client ID>
```

1. Git 호스트마다 OAuth application을 등록합니다. redirect URI는 `https://<agentops.hostname>/api/git/callback`입니다.
   콘솔이 이 경로를 Identity로 중계합니다.
   GitLab application은 scope `api`, `read_repository`, `write_repository`를 허용합니다.
   Gitea application은 scope를 따로 정하지 않습니다. **비공개 클라이언트**를 끄면 client secret 없이 PKCE로 동작합니다.
2. client secret을 `/k8s/<cluster>/agentops-identity/git-host-secrets` Secrets Manager 시크릿에 JSON 객체 하나로 저장합니다.
   key는 호스트 `name`, 값은 그 application의 client secret입니다. key가 없는 GitLab·Gitea 호스트는 public client입니다.
   `github` 호스트에는 secret이 있어야 합니다. Identity ExternalSecret은 이 경로를 항상 읽으므로 Git 호스트가 없어도
   시크릿을 만듭니다(빈 객체 `{}`).
3. `agentops.git_hosts`를 바꿔 배포합니다. Identity가 새 `GIT_HOSTS`로 다시 시작합니다.

기존 DB는 새 image의 데이터 보존 전환 도구로 현재 schema로 바꿉니다. 도구는 정해진 시작 schema만 받습니다.

| 서비스 | 현재 schema | 시작 schema와 도구 |
| --- | --- | --- |
| Studio | version 17 | version 14–16: 시작할 때 자동으로 단계별 변환. 16→17은 사람 데이터를 지우므로 [Studio 데이터 가져오기](#studio-데이터-가져오기)를 먼저 끝냅니다 |
| Memory | 현재 fingerprint | 기업 프로필 이전: `upgrade-enterprise-profile.mjs` 후 `upgrade-model-work.mjs`. 그 다음 기준선: `upgrade-model-work.mjs` |
| Knowledge | 현재 fingerprint | v0.18.1, v0.19.0–v0.20.1: `node scripts/upgrade-platform-auth.mjs --apply` |
| Forge | version 11 | v4–v10을 시작할 때 자동으로 단계별 확장 |

Studio의 현재 schema는 `SELECT version FROM agent_studio_schema`로 확인합니다. 도구가 있는 서비스는 해당 image에서
도구를 명시적으로 실행한 직후 새 버전을 시작합니다. 운영 DB reset은 데이터 유실이 허용될 때만 사용합니다.

## DB와 원본 보존

여섯 서비스와 AgentOps 콘솔·Identity의 `DATABASE_URL`은 `/k8s/<cluster>/agent-platform/database-url` 하나입니다.
PostgreSQL의 새 빈 `agent_platform` DB를 사용하고, 업무 테이블은 각각
`agent_studio_`, `agent_memory_`, `agent_knowledge_`, `agent_forge_` 접두어로 구분합니다.
Knowledge pg-boss도 같은 DB에 전용 queue namespace를 사용합니다.

`postgres` StatefulSet, `data-postgres-0` PVC, 기존 `agent_studio` DB 사용자와 비밀번호는 유지합니다.
`postgres-bootstrap`은 새 DB가 없을 때만 만들고 `vector` extension을 활성화합니다.
이미 있는 `agent_studio`·`agent_memory` DB를 삭제하거나 복사하지 않습니다.
앱 이미지는 자신의 namespace만 초기화하며 운영 DB reset은 배포 절차에 없습니다.
PostgreSQL readiness는 기존 DB와 새 DB 어느 쪽에도 의존하지 않는 `postgres` DB로 확인합니다.
`max_connections` 변경은 기존 StatefulSet의 재시작을 수반합니다.

Studio 파일은 S3 `agent-studio-static`, Knowledge 원본·오디오는 S3 `agent-knowledge-static`에 둡니다.
Studio Artifact와 Knowledge 원본은 각 앱의 인증된 API로 접근합니다.
Memory와 Forge에는 S3 자격 증명을 주입하지 않습니다.
Knowledge 지식 그래프는 PostgreSQL을 사용하며 Neo4j 환경변수를 주입하지 않습니다.
Neo4j 배포·Secret bootstrap은 제공하지 않습니다. 이전 리소스의 삭제 절차는
[폐기된 실행·그래프 자원 정리](retired-agent-resources.md)를 따릅니다.

EKS의 S3 SDK는 Pod Identity를 사용합니다. S3 endpoint와 정적 S3 키는 주입하지 않습니다.

| ServiceAccount | IAM role | 버킷 |
| --- | --- | --- |
| `agent-studio/agent-studio` | `pod-role--agent-studio` | `agent-studio-static` |
| `agent-knowledge/agent-knowledge` | `pod-role--agent-knowledge` | `agent-knowledge-static` |

## 인증과 Secret 준비

External Secrets는 `/k8s/<cluster>/` 경로를 읽습니다. 앱 namespace와 Forge 실행 namespace에는
각각 ECR generator와 `ecr-registry` Secret이 있습니다. EKS External Secrets의 IAM 연결을 사용하며
앱에 ECR 장기 키를 전달하지 않습니다. 운영 클러스터의 경로는 `/k8s/eks-demo/`입니다.
경로 이름은 `/k8s/<cluster>/<service>/<env 이름에서 서비스 접두어를 뺀 kebab-case>`입니다
(예: Forge `FORGE_CHECKPOINT_SECRET` → `agent-forge/checkpoint-secret`). Agent 서비스는 `/k8s/common/`을 사용하지 않습니다.

bootstrap으로 누락된 parameter를 준비합니다.

```bash
python3 scripts/bootstrap_agent_platform.py --cluster eks-demo
python3 scripts/bootstrap_agent_platform.py --cluster eks-demo --apply
```

첫 명령은 누락 경로만 출력합니다. `--apply`는 누락된 시크릿만 만들고 기존 값을 덮어쓰지 않습니다.

기존 클러스터의 데이터베이스를 새 클러스터로 옮길 때는 `--copy-from <원본 cluster>`를 함께 사용합니다.
기존 데이터가 의존하는 값(`CONTINUITY`)을 원본에서 그대로 복사하고, 나머지 값만 대상 클러스터에서 만듭니다.
`CONTINUITY`는 다음 값입니다.

- Studio 암호화 키(`agent-studio/aes-encryption-key`)
- Memory·Knowledge 인증 secret
- Forge 체크포인트 secret·모델 secret·Git 위임 secret
- Models 인증 secret·암호화 키
- Identity 인증 secret·암호화 키·조직 ID(`agentops-identity/auth-secret`, `encryption-key`, `organization-id`)
- Identity client secret 일곱 개(`agentops-identity/client-secret-<client>`)

원본에 값이 하나라도 없거나 대상에 다른 값이 이미 있으면 쓰기 전에 중단합니다. 값은 출력하지 않습니다.

```bash
python3 scripts/bootstrap_agent_platform.py --cluster eks-demo --copy-from <원본 cluster>
python3 scripts/bootstrap_agent_platform.py --cluster eks-demo --copy-from <원본 cluster> --apply
```

bootstrap은 기존 PostgreSQL 비밀번호에서 공통 DB URL을 만듭니다. metrics token, schedule scan token,
Runway 풀 비밀번호(`agent_runway`가 있는 클러스터)와 다음 값은 서로 독립적으로 생성합니다.

- Memory·Knowledge·Models·Identity의 인증 secret
- Studio·Models·Identity의 base64 32바이트 암호화 키
- Forge 체크포인트 secret·모델 secret·Git 위임 secret(`agent-forge/git-delegation-secret`)
- Identity client secret 일곱 개. client는 `console`, `studio`, `memory`, `knowledge`, `forge`, `runway`, `models`입니다.
- Identity 조직 ID(`agentops-identity/organization-id`): UUID입니다. 모든 Identity 토큰이 이 조직을 가리키고 Forge 행과
  체크포인트가 이 값을 가집니다. 첫 로그인 뒤에는 바꾸지 않습니다.

DB URL이 기존 비밀번호와 모순되면 쓰기 전에 중단합니다. 다음 값은 생성하지 않고 준비할 경로만 출력합니다.

- `agent-studio/plugins-repo-token`
- `agentops-identity/admin-emails`
- `agentops-identity/git-host-secrets`

Dex client 값(`agentops-identity/oidc-client-id`, `oidc-client-secret`)은 argocd-env-addons `install/eks/secrets.sh`가 만듭니다.
Secret 환경변수를 변경한 뒤에는 소비자 Deployment를 새 설정으로 rollout해야 합니다.

| Secrets Manager 경로 (`/k8s/<cluster>/` 아래) | 소비자 | 준비 |
| --- | --- | --- |
| `agent-platform/database-url` | Agent 서비스 여섯 개, AgentOps 콘솔·Identity, Identity 가져오기 Job | bootstrap |
| `agent-<service>/metrics-bearer-token` | Studio·Memory·Knowledge·Forge와 수집기 | bootstrap |
| `agent-studio/aes-encryption-key` | Studio, Identity 가져오기 Job(`STUDIO_AES_ENCRYPTION_KEY`) | bootstrap |
| `agent-studio/schedule-scan-token` | Studio web·ticker | bootstrap |
| `agent-studio/plugins-repo-token` | Studio | `PLUGINS_REPO` 저장소에 접근할 GitHub token |
| `agent-memory/auth-secret` | Memory | bootstrap |
| `agent-models/auth-secret`, `aes-encryption-key` | Models | bootstrap |
| `agent-knowledge/auth-secret` | Knowledge web·worker | bootstrap |
| `agent-forge/checkpoint-secret`, `model-secret` | Forge | bootstrap |
| `agent-forge/git-delegation-secret` | Forge(`FORGE_GIT_DELEGATION_SECRET`). Run에 넘기는 Git 위임을 봉인한다 | bootstrap. `checkpoint-secret`과 다른 값이어야 한다 |
| `agentops-identity/auth-secret`, `encryption-key` | Identity | bootstrap |
| `agentops-identity/organization-id` | Identity(`IDENTITY_ORGANIZATION_ID`) | bootstrap. UUID이며 첫 로그인 뒤 바꾸지 않는다 |
| `agentops-identity/client-secret-<client>` | Identity, 해당 client(`console`은 콘솔). `client-secret-models`는 Identity만 읽는다 | bootstrap |
| `agentops-identity/oidc-client-id`, `oidc-client-secret` | Identity. Dex는 `oidc-client-secret`을 읽는다 | argocd-env-addons `install/eks/secrets.sh` |
| `agentops-identity/admin-emails` | Identity(`ADMIN_EMAILS`) | 관리자 이메일의 쉼표 구분 목록 |
| `agentops-identity/git-host-secrets` | Identity(`GIT_HOST_SECRETS`) | 각 Git 호스트에 OAuth application 등록 후. 호스트가 없으면 `{}` |
| `agent-runway/metrics-bearer-token` | Runway와 수집기 | bootstrap(`agent_runway`가 있는 클러스터) |
| `agent-runway/pool-admin-password`, `pool-database-url` | Runway, 풀 bootstrap Job | bootstrap. URL은 비밀번호에서 만든다 |
| `delivery-pool/postgres-password` | 풀 PostgreSQL superuser(bootstrap Job 전용) | bootstrap |

Memory·Knowledge 조직 ID는 설정하지 않습니다. Studio가 각 서비스의 공개 MCP `installation_get`으로 확인합니다.
요청 header와 확인 주기는 [Studio 서비스 연결 계약](https://github.com/opspresso/agent-studio/blob/main/docs/design/agent-services.md#연결-설정)을 따릅니다.
Forge는 조직 ID를 설정으로 읽지 않습니다. Forge는 Identity 토큰의 조직(`agentops-identity/organization-id`)으로 행을 찾습니다.
ExternalSecret은 참조한 경로가 하나라도 없으면 Secret을 갱신하지 않습니다. 새 경로를 먼저 준비합니다.
Identity의 ExternalSecret은 `agentops.git_hosts`·`agentops.identity.import` 값과 관계없이 위 `agentops-identity/` 경로를
모두 읽습니다. 배포 전에 모두 만듭니다.
ClusterSecretStore `secrets-manager`는 허용한 namespace(`argocd-env-addons`의 `charts/external-secrets` `namespaceRegexes`)만 씁니다.
새 서비스 namespace는 chart를 배포하기 전에 이 목록에 추가합니다. 없으면 ExternalSecret이 `denied by spec.condition`으로 실패하고
Pod가 `secret "<name>-external" not found`로 시작하지 못합니다. 목록을 고친 뒤에도 실패한 ExternalSecret은 재시도 간격 동안 다시 읽지
않으므로 `kubectl annotate externalsecret <name>-external force-sync=$(date +%s) --overwrite`로 다시 읽게 합니다.

## 실행과 네트워크

Studio는 web(`agent-studio`)과 schedule ticker(`agent-studio-ticker`)만 실행합니다.
오디오 처리는 Knowledge, Workspace 실행은 Forge가 소유하고 Studio는 MCP로 두 서비스를 호출합니다.
Studio에는 audio/Workspace worker, Sandbox 실행 권한, Docker daemon, Kubernetes token이 없습니다.
ticker는 scan/plugin sync를 매분, reindex를 매시간 호출합니다. PR 리뷰는 호출자의 GitHub MCP grant로
`GITHUB_API_URL`·`GITHUB_WEB_URL`에 연결합니다.

Knowledge·Forge는 자기 정책을 직접 판단하며 Studio 정책 callback을 호출하지 않습니다. 그래서 두 서비스에는
`STUDIO_URL`을 설정하지 않습니다.

Knowledge는 웹과 별도 `agent-knowledge-worker` 하나를 사용합니다. 문서/Audio 소비는 worker에서만 활성화합니다.
웹과 worker는 같은 이미지·모델·AI admission 정책을 사용합니다. quota는 동일 PostgreSQL에서
interactive/batch와 전체 provider 상한을 합산합니다. pg-boss에는 별도 DB를 만들지 않습니다.

Forge는 `agent-forge` 하나의 프로세스에서 HTTP·MCP·worker·native Gateway를 제공합니다.
`agent-forge-workspaces` namespace의 controller-owned 실행 Pod만 다룹니다.
Sandbox 이미지는 같은 release의 `agent-forge:sandbox-<full-v-tag>`이며 독립 `latest`를 쓰지 않습니다.
공급자·Git 장기 키는 Sandbox에 전달하지 않습니다. 자세한 권한·보존 계약은
[Kubernetes Workspace 운영](workspace-kubernetes.md)을 따릅니다.

Forge에는 Git 호스트 설정이나 credential이 없습니다. 사용자는 콘솔에서 Git 호스트를 연결하고,
Forge는 Identity에서 사용자의 Git token을 받습니다. Run에 넘기는 Git 위임은 `FORGE_GIT_DELEGATION_SECRET`으로 봉인합니다.
호스트는 [Git 호스트](#git-호스트)에서 선언합니다.

내부 Service와 Pod의 HTTP 포트는 모든 Agent 서비스(AgentOps·Studio·Memory·Knowledge 웹/worker·Forge·Models·Runway)가 `3000`으로 같습니다. Forge는 Sandbox 전용 보조 포트 `3001`을 함께 엽니다.
HTTPRoute backend, Studio 서비스 연결, callback, ticker와 지표 수집도 이 포트를 사용합니다.
앱 ingress는 해당 플랫폼 Gateway, 수집기, 같은 namespace와 명시된 서비스 peer만 허용합니다.
EKS의 Gateway 출발 Pod는 `istio-system` namespace의 `istio-ingressgateway`입니다.
Studio에는 Forge·Knowledge namespace의 연결, Forge에는 Sandbox의 인증된 Gateway 연결, Models에는 네 소비 서비스의
연결을 허용합니다. Sandbox는 Models에 직접 연결하지 않고 Forge Gateway를 거칩니다.
신뢰된 control-plane의 외부 LLM·MCP egress는 기존 동작을 유지합니다.
Sandbox는 별도 기본 거부 정책으로 DNS·public HTTP/HTTPS·Forge Pod 포트만 허용합니다.

## 최초 전환 순서

1. 네 앱의 필수 검사와 release image 게시를 확인합니다. 전환 동안 `GITOPS_AUTO_DEPLOY=false`로
   자동 버전 dispatch를 멈추고 Studio·Memory·Knowledge·Forge controller+Sandbox image를 모두 준비합니다.
2. Terraform의 ECR/IAM, addons의 `.agrt.dev` DNS/TLS·ESO·Gateway·관측성 계약을 확인합니다.
   이 절차로 클러스터를 만들지 않습니다. 대상 클러스터의 기존 node·PVC·bucket은 보존합니다.
3. 기존 Studio 작업 신규 접수를 막고 진행 중 실행의 체크포인트·종료를 확인합니다.
   새 앱을 올리기 전에 Secret, `agent_platform`, Knowledge object storage 버킷을 준비합니다.
4. 새 이미지로 Memory/Knowledge 초기화를 완료하고 실제 organization ID와 모델 설정을 검증합니다.
   사용자 연결은 로그인한 계정으로 설정합니다. 조직 ID가 준비되기 전에 Studio 연결을 활성화하지 않습니다.
5. `scripts/gitops.py deploy --dry-run`으로 phase 버전과 이력을 생성하고 chart 변경과 함께 반영합니다.
   phase 값을 직접 편집하지 않습니다. 동일 배포에서 새 이미지·DB URL·서비스 경계를 전환합니다.
6. Argo CD `Synced/Healthy`, 정확한 image tag, DB readiness와 metrics를 확인합니다.
   로그인, Memory recall/remember, Knowledge 원본 upload→전사/회의록→검색, Forge 실행→로그→취소/복원을 확인합니다.
7. 정상 동작을 확인한 뒤 앱 release의 자동 GitOps gate를 원래 설정으로 복원합니다.

## AgentOps 연결

AgentOps 콘솔(Deployment `agentops`)과 Identity(Deployment `agentops-identity`)는 Application `agentops-<cluster>`
(namespace `agentops`) 하나로 배포됩니다. 콘솔은 Identity로 로그인하고, Identity는 Dex로 로그인합니다
([로그인과 모델 연결](#로그인과-모델-연결)).

1. **Dex client**: argocd-env-addons의 `env/<cluster>.yaml`에 `agentops`가 있으면 Dex에 client `agentops-identity`가 생깁니다.
   redirect URI는 `https://<agentops.hostname>/api/auth/callback`입니다. argocd-env-addons `install/eks/secrets.sh`가
   `/k8s/<cluster>/agentops-identity/oidc-client-id`(값 `agentops-identity`)와 `oidc-client-secret`을 만듭니다.
2. **Secret 준비**: bootstrap으로 Identity의 생성 값을 만듭니다.
   ```bash
   python3 scripts/bootstrap_agent_platform.py --cluster eks-demo --apply
   ```
   사람이 정하는 두 값을 만듭니다. `git-host-secrets`는 [Git 호스트](#git-호스트)의 JSON 객체를 파일로 전달합니다.
   ```bash
   aws secretsmanager create-secret --name /k8s/eks-demo/agentops-identity/admin-emails \
     --secret-string 'admin@example.com,ops@example.com'
   aws secretsmanager create-secret --name /k8s/eks-demo/agentops-identity/git-host-secrets \
     --secret-string file://git-host-secrets.json
   ```
   `agentops` namespace가 `secrets-manager` 허용 목록에 있는지 확인합니다([인증과 Secret 준비](#인증과-secret-준비)).
3. **배포**: `env/<cluster>.yaml`에 `agentops.identity`(`oidc_issuer`, `enrollment_domains`, `import: 'off'`)와
   `agentops.git_hosts`를 선언합니다. AgentOps release image를 게시하고 `agentops-eks-demo`가 `Synced/Healthy`인지 확인합니다.
   ```bash
   kubectl -n agentops rollout status deploy/agentops
   kubectl -n agentops rollout status deploy/agentops-identity
   ```
4. **확인**: `https://agentops.agrt.dev/api/health`가 200이고, `ADMIN_EMAILS`의 계정으로 로그인하면 홈이 열리며,
   로그아웃하면 `/signed-out`으로 돌아옵니다.

AgentOps는 지금 각 서비스를 공개 host로 부릅니다. Studio·Memory·Knowledge가 공개 host만 허용하기
때문입니다. 서비스가 AgentOps 전환 단계에 들어가면 그 서비스의 허용 host와 NetworkPolicy에 내부 주소와 `agentops` namespace를
더하고, AgentOps의 `<SERVICE>_API_URL`을 내부 주소로 바꿉니다.

### Studio 데이터 가져오기

Studio DB에 사람 데이터가 남아 있는 설치(Studio schema 16 이하)는 Identity를 처음 켤 때 한 번 가져오기를 실행합니다.
eks-demo는 가져오기를 마쳤고 `import: 'off'`입니다.

가져오기는 Studio의 사람·등급·팀·Git 연결을 Identity로 복사합니다. Studio v0.276.0이 시작하기 전에 끝내야 합니다.
Studio schema 17이 가져오기가 읽는 사람 데이터를 지우기 때문입니다.

`agentops.identity.import`가 `count` 또는 `apply`이면 chart가 PostSync hook Job `agentops-identity-import`를 렌더합니다.
Job은 Identity가 테이블을 만든 뒤 `node scripts/import-identity.ts`(`apply`이면 `--apply`)를 한 번 실행합니다
(`backoffLimit: 0`, `activeDeadlineSeconds: 600`). Job은 `agentops-identity-external`의 `DATABASE_URL`·`AES_ENCRYPTION_KEY`와,
ExternalSecret `agentops-identity-import`로 읽는 `agent-studio/aes-encryption-key`(`STUDIO_AES_ENCRYPTION_KEY`)를 사용합니다.
Studio의 Git 연결 token을 Studio 키로 열고 Identity 키로 다시 봉인합니다.

1. `agentops.identity.import: count`로 배포합니다. sync가 끝나면 Job log에서 건수를 확인합니다. 이 단계는 아무것도 쓰지 않으며,
   log에는 건수만 있습니다.
   ```bash
   kubectl -n agentops logs job/agentops-identity-import
   ```
2. `apply`로 바꿔 배포한 뒤 수동으로 sync합니다. Job log에서 결과를 확인합니다. `apply`를 다시 실행해도 바뀌는 것이 없습니다.
3. `'off'`로 바꿔 배포합니다. Job과 ExternalSecret `agentops-identity-import`가 렌더에서 빠집니다.

`off`는 따옴표로 씁니다. 따옴표가 없는 `off`는 YAML boolean이 되어 `gen_values.py`가 거부합니다.
hook Job만 바뀌는 변경(`count` → `apply`)은 Application을 OutOfSync로 만들지 않으므로 auto-sync가 Job을 다시 실행하지 않습니다.
이때는 Argo CD에서 `agentops-eks-demo`의 sync를 직접 시작합니다.

## Studio worker 폐기와 독립 서비스 전환

이 절은 Studio audio/Workspace worker를 쓰던 이전 버전의 DB를 변환하는 절차입니다. `eks-demo`의 현재 선언에는
두 worker가 없습니다.

이 전환에서 Studio의 audio/Workspace worker와 Knowledge·Forge의 `STUDIO_URL`이 제거됩니다.
Argo CD의 `prune`이 두 worker Deployment를 삭제합니다. 아래 명령은 ECR 주소 `ECR`과 서비스별 image tag
`V_<SERVICE>`를 사용합니다. 각 서비스의 새 release image 게시를 먼저 확인합니다.
`run_once`는 해당 namespace의 Secret을 읽는 일회성 Pod를 실행하고, 끝나면 Pod를 지웁니다.

```bash
run_once() { ns=$1; image=$2; shift 2
  kubectl -n "$ns" run "$ns-once" --rm -i --quiet --restart=Never --image="$image" --overrides="$(jq -nc \
    --arg i "$image" --arg s "$ns-external" '{spec:{imagePullSecrets:[{name:"ecr-registry"}],
    containers:[{name:"run",image:$i,command:$ARGS.positional,envFrom:[{secretRef:{name:$s}}]}]}}' --args -- "$@")"; }
```

1. **Knowledge DB 변환**: `agent_knowledge_maintenance: true`로 Knowledge web과 worker를 먼저 정지합니다.
   두 Deployment의 desired/ready가 0인지 확인한 뒤 새 image로 변환합니다.
   ```bash
   run_once agent-knowledge "$ECR/agent-knowledge:$V_KNOWLEDGE" node scripts/upgrade-audio-profiles.mjs --apply
   ```
   결과는 `upgraded` 또는 `current`여야 합니다. 다른 결과면 Knowledge를 재개하지 않고 원인을 확인합니다.
   변환 후에는 Knowledge를 v0.21.x로 되돌리지 않습니다. 새 버전을 기록하고 maintenance를 `false`로 되돌립니다.
2. **Forge**: 새 image를 배포합니다. schema v11→v12는 Forge가 시작할 때 자동으로 적용합니다.
3. **Studio**: 새 Studio image를 배포한 뒤 platform auth를 변환합니다.
   ```bash
   run_once agent-studio "$ECR/agent-studio:$V_STUDIO" node build/upgrade-platform-auth.cjs --apply
   ```
   이 도구는 서비스 API scope를 추가하고 Forge·Knowledge의 policy/control scope를 제거합니다.
   여러 번 실행해도 결과가 같습니다.
4. **확인**: `agent-studio` namespace에 `agent-studio`와 `agent-studio-ticker` Deployment만 있는지 확인합니다.
   Knowledge 원본 upload→전사/회의록과 Forge 실행→로그→취소를 Studio MCP 호출로 확인합니다.
   ```bash
   kubectl -n agent-studio get deploy
   curl -fsS https://knowledge.agrt.dev/api/health && curl -fsS https://forge.agrt.dev/health
   ```

## 검증

```bash
GITHUB_PUSH=false bash scripts/build.sh
python3 scripts/validate.py --all-charts
pytest
```

Helm/pytest는 EKS 선언의 resource 정책, 같은 DB URL, 인증·metrics, 이미지 연동,
RBAC·NetworkPolicy·quota·보존 계약을 검사합니다. 실제 환경 검증은 별도입니다.
Memory/Knowledge web은 liveness `/api/health`와 readiness `/api/ready`를 나누고, Knowledge worker는 초기화 후
`/api/health`를 제공합니다. Forge는 liveness `/health`와 readiness `/ready`를 나눕니다.
Knowledge/Memory `/api/metrics`와 Forge `/metrics`는 `METRICS_BEARER_TOKEN` 전용 bearer를 요구합니다.
Forge는 `agent-forge/metrics-bearer-token`을 `METRICS_BEARER_TOKEN`으로 받습니다. 이 값을 지원하지 않는
Forge image는 Studio가 발급한 `workspace:read` token만 받으므로 EKS ServiceMonitor의 metrics 수집이 실패합니다.
health 성공만으로 원본 storage·provider·작업 성공을 판정하지 않습니다.

DB 용량과 롤링 배포 연결 예산은 [용량 운영](production-capacity.md)을 따릅니다.
PostgreSQL OS/libc를 바꿀 때는 기존·보관 DB와 `template1`의 collation 버전도 확인합니다.
경고를 없애려고 데이터나 인덱스 재구성 없이 버전만 덮어쓰지 않습니다.

## Agent Runway

EKS에만 배포한다(`env/<cluster>.yaml`의 `agent_runway`). 운영 클러스터 `eks-demo`에서 `runway.agrt.dev`로 제공한다.
Runway는 Pod Identity로 Secrets Manager·STS·EKS를 호출한다. 계약은 `agent-platform`의 `docs/DELIVERY_RESOURCES.md`, AWS 리소스는 `terraform-env-demo`의
`terraform-env-opsp/demo/8-agent-runway`가 소유한다.

| Application | namespace | 내용 |
| --- | --- | --- |
| `agent-runway-<cluster>` | `agent-runway` | Runway web·API·MCP. `agent_runway_maintenance: true`이면 0개 |
| `delivery-pool-<cluster>` | `delivery-pool` | `postgres` 리소스의 공유 PostgreSQL. bootstrap Job이 비superuser `delivery_admin`(CREATEDB·CREATEROLE)과 `template1`의 `vector`를 준비한다 |
| `delivery-tenants-<cluster>` | (클러스터 범위) | tenant secrets 역할을 쓰는 SecretStore를 검사하는 ValidatingAdmissionPolicy `delivery-tenant-secret-stores` |

- `agent_runway.connected`가 `true`일 때만 Studio(`RUNWAY_URL`, MCP 내부 host), Forge(`RUNWAY_URL`, Sandbox의 풀 egress),
  AgentOps 콘솔(`RUNWAY_API_URL`)이 Agent Runway에 연결된다. Runway는 다른 서비스처럼 Identity 토큰을 받는다.
  Runway의 Identity client secret `agentops-identity/client-secret-runway`는 bootstrap이 만든다.
- 테넌트 앱의 namespace `app-<project>-<env>`와 그 안의 SA `app`, SecretStore `agent-runway`, ExternalSecret `app-env`,
  NetworkPolicy, ResourceQuota, Deployment는 `argocd-env-projects`의 `charts/app`이 만든다. Agent Runway가
  `projects/<project>/<env>/app.yaml`을 쓰면 `argocd-env-addons`의 ApplicationSet `projects`가 Application을 만든다
  (`agent-platform`의 `docs/APP_DELIVERY.md`).
- 테넌트 앱은 Secret `app-env`를 `envFrom`으로 읽는다. SecretStore는 `agent-runway-tenant-secrets` 역할을
  session tag `delivery-project`·`delivery-env`로 assume하므로 다른 Project 경로는 AWS가 거부한다.
- Runway는 `agent_runway.delivery`의 namespace(`app-delivery`)에 Argo Workflows Workflow를 제출한다. 그래서 Runway Pod는
  ServiceAccount token을 마운트하고(`automountServiceAccountToken: true`), `NODE_EXTRA_CA_CERTS`로 API server CA를 신뢰한다.
  제출 권한(Role `agent-runway`)은 `argocd-env-projects`의 `charts/delivery`가 준다.
- AWS는 session tag가 가리키는 Project 경로를 읽게 할 뿐, tag가 정당한지는 확인하지 않는다. 그래서 클러스터의
  ValidatingAdmissionPolicy `delivery-tenant-secret-stores`가 모든 SecretStore·ClusterSecretStore를 검사한다.
  - 테넌트 namespace의 SecretStore는 tenant 역할만 쓸 수 있다.
  - tenant 역할은 이름이 `app-<project>-<env>`이고 그 label(`opspresso.com/delivery-project`·`delivery-env`)이
    session tag와 같은 namespace의 SecretStore에서만 허용한다. 다른 namespace와 ClusterSecretStore는 거부한다.
  - 이름까지 확인하므로 다른 namespace에 테넌트 label을 붙여도 다른 Project를 흉내 낼 수 없다.
- 풀 bootstrap은 `postgres`·`template1` database의 PUBLIC CONNECT를 회수한다.
- 풀은 `agent-runway`, `agent-forge-workspaces`, `opspresso.com/delivery-tenant=true` namespace에서만 접속을 받는다.
  Forge Sandbox egress에는 `agent_runway`가 있을 때 풀 5432 허용이 추가된다.
