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
| Studio | `studio.agrt.dev` | Agent·Chat, 승인, 로그인(OIDC 발급), MCP로 Memory·Knowledge·Forge 호출 |
| Memory | `memory.agrt.dev` | 사용자·팀·조직의 단기/장기 메모리, recall/remember/handoff |
| Knowledge | `knowledge.agrt.dev` | 문서·검색·지식 그래프, 원본 녹음·전사·회의록, durable queue worker |
| Forge | `forge.agrt.dev` | Workspace·Sandbox·Run·Session·Git, native 모델 Gateway |
| Models | `models.agrt.dev` | 공급자 연결·자격 증명, 모델 선택, 공급자 호출, 사용량 원장 |
| Runway | `runway.agrt.dev` | 앱 리소스(PostgreSQL database·S3 prefix)·Variable 저장과 전달. [Agent Runway](#agent-runway) |

공개 요청은 public ALB `demo`(`terraform-env-demo`의 `3-alb`, Route53 `*.agrt.dev`) → `istio-ingressgateway`(`istio-system`)
→ 각 앱의 HTTPRoute 순서로 전달됩니다.

모든 Agent 앱은 `.agrt.dev` 호스트 하나만 사용합니다. `AUTH_BASE_URL`,
`AUTH_ALLOWED_HOSTS`, HTTPRoute는 같은 호스트를 가리킵니다. Forge는
`ALLOWED_ORIGINS`를 사용합니다. 프록시는 원래 `Host`를 전달해야 합니다.
`trusted_proxy_cidrs`는 실제 프록시 hop에 맞춰 EKS VPC CIDR(`10.10.0.0/16`)을 사용합니다.

## 로그인과 모델 연결

Studio만 외부 로그인 제공자(Google 또는 기업 OIDC)와 연결합니다. Memory·Knowledge·Forge·Models는
Studio OIDC로 로그인합니다. 템플릿은 다음 값을 고정으로 렌더합니다.

| 설정 | Memory·Knowledge·Forge·Models |
| --- | --- |
| `OIDC_ISSUER` | `https://<agent_studio.hostname>/api/auth` |
| `OIDC_PROVIDER_ID` | `studio`. callback은 `https://<서비스 호스트>/api/auth/callback/studio` |
| `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` | `/k8s/<cluster>/agent-<service>/oidc-client-id`, `oidc-client-secret` |
| `MODELS_URL` | `http://agent-models.agent-models.svc.cluster.local:3400` (Models 제외) |

`gen_values.py`는 이 네 서비스의 `oidc`, `google_sign_in`, `sign_in_domains`를 거부합니다.
Studio는 같은 `oidc-client-id` 경로를 `MEMORY_OIDC_CLIENT_ID`, `KNOWLEDGE_OIDC_CLIENT_ID`,
`FORGE_OIDC_CLIENT_ID`로 읽습니다. Studio에는 `MODELS_URL`과 브라우저용
`MODELS_PUBLIC_URL=https://<agent_models.hostname>`을 설정합니다.

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

### Studio 기업 OIDC

`agent_studio.oidc` 블록은 Keycloak을 포함한 기업 OIDC 연결을 선언합니다.
client ID·secret은 다른 서비스 client처럼 `/k8s/<cluster>/agent-studio/oidc-client-id`·`oidc-client-secret`
SecureString에서 읽습니다. env에는 경로를 적지 않습니다.
이 블록이 없으면 Studio는 현재 Google 연결을 유지합니다.

```yaml
agent_studio:
  hostname: studio.example.com
  google_sign_in: false
  oidc:
    issuer: https://sso.example.com/realms/company
    provider_id: oidc
    display_name: Company SSO
    scopes: openid profile email
    logout_mode: app
    trust_email: false
    profile_claims:
      teams: /company/teams
      department: /company/department
      jobTitle: /company/title
      roles: /company/roles
```

`google_sign_in: false`는 Studio의 Google credential 주입을 끕니다.
`trust_email`은 Studio `OIDC_TRUST_EMAIL`(Studio v0.240.0 이상)이 되며 기본값은 `false`입니다.
`false`이면 기존 Google 계정과 새 OIDC 계정을 이메일만으로 연결하지 않으므로 전환할 계정을 먼저 확인합니다.
`true`이면 IdP가 `email_verified=true`로 보낸 이메일이 Studio의 확인된 이메일과 같을 때 첫 OIDC 로그인에서
기존 계정에 연결합니다. 사용자가 IdP에서 자기 이메일을 바꿀 수 없을 때만 켭니다. 바꿀 수 있으면 다른 사람의
계정을 가져갈 수 있습니다. eks-demo는 LLDAP UI를 관리자 그룹에만 열어 이 조건을 지킵니다. `provider_id`는 기존 계정과
callback을 식별합니다. 기존 Keycloak 전용 설정에서 바꿀 때는 `provider_id: keycloak`을 유지합니다.
새 설치 기본값은 `oidc`입니다. 회사 팀·역할은 표시 정보이며 앱 권한을 자동 부여하지 않습니다.

### Studio Git 호스트

`agent_studio.git_hosts`는 사용자가 Studio **도구 → Git 계정**에서 연결할 Git 호스트를 선언합니다(Studio v0.242.0 이상).
Forge와 Runway는 이 연결로 사용자의 저장소를 씁니다. 두 서비스에는 Git 호스트 설정이나 credential이 없습니다.
계약은 agent-platform `docs/GIT_CONNECTIONS.md`를 따릅니다.

```yaml
agent_studio:
  git_hosts:
    - name: github              # 이름은 GIT_HOST_SECRETS의 key와 같습니다.
      provider: github          # github 또는 gitlab
      origin: https://github.com
      client_id: <OAuth application의 client ID>
```

1. Git 호스트마다 OAuth application을 등록합니다. redirect URI는 `https://<agent_studio.hostname>/api/git/callback`입니다.
   GitLab application은 scope `api`, `read_repository`, `write_repository`를 허용합니다.
2. client secret을 `/k8s/<cluster>/agent-studio/git-host-secrets` SecureString에 JSON 객체 하나로 저장합니다.
   key는 호스트 `name`, 값은 그 application의 client secret입니다. 이 parameter가 없으면 Studio Secret 동기화가 실패하므로
   `git_hosts`를 추가하기 전에 만듭니다.
3. `git_hosts`를 추가해 배포한 다음 Studio Pod에서 `node build/upgrade-platform-auth.cjs --apply`를 실행합니다.
   기존 Forge·Runway client가 `platform:git` scope와 토큰 교환 grant를 받습니다.

기존 DB는 새 image의 데이터 보존 전환 도구로 현재 schema로 바꿉니다. 도구는 정해진 시작 schema만 받습니다.

| 서비스 | 현재 schema | 시작 schema와 도구 |
| --- | --- | --- |
| Studio | baseline 13 | baseline 12: `node build/upgrade-platform-auth.cjs --apply`. baseline 11: `node build/upgrade-enterprise-profile.cjs --apply`(12 단계 포함) |
| Memory | 현재 fingerprint | 기업 프로필 이전: `upgrade-enterprise-profile.mjs` 후 `upgrade-model-work.mjs`. 그 다음 기준선: `upgrade-model-work.mjs` |
| Knowledge | 현재 fingerprint | v0.18.1, v0.19.0–v0.20.1: `node scripts/upgrade-platform-auth.mjs --apply` |
| Forge | version 11 | v4–v10을 시작할 때 자동으로 단계별 확장 |

Studio의 현재 baseline은 `SELECT version FROM agent_studio_schema`로 확인합니다. 해당 image에서 도구를
명시적으로 실행한 직후 새 버전을 시작합니다. 운영 DB reset은 데이터 유실이 허용될 때만 사용합니다.

## DB와 원본 보존

여섯 서비스의 `DATABASE_URL`은 `/k8s/<cluster>/agent-platform/database-url` 하나입니다.
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

첫 명령은 누락 경로만 출력합니다. `--apply`는 누락된 SecureString만 만들고 기존 값을 덮어쓰지 않습니다.

기존 클러스터의 데이터베이스를 새 클러스터로 옮길 때는 `--copy-from <원본 cluster>`를 함께 사용합니다.
데이터·등록이 의존하는 값(`CONTINUITY`: Studio 인증 secret·암호화 키, Memory·Knowledge 인증 secret,
Forge 키·조직·GitHub OAuth, Models 인증 secret·암호화 키, 네 서비스의 OIDC client)을 원본에서 그대로 복사하고,
나머지(DB URL·metrics token·schedule scan token)만 대상 클러스터 값으로 만듭니다.
원본에 값이 하나라도 없거나 대상에 다른 값이 이미 있으면 쓰기 전에 중단합니다. 값은 출력하지 않습니다.

```bash
python3 scripts/bootstrap_agent_platform.py --cluster eks-demo --copy-from <원본 cluster>
python3 scripts/bootstrap_agent_platform.py --cluster eks-demo --copy-from <원본 cluster> --apply
```
기존 PostgreSQL 비밀번호에서 공통 DB URL을 만들며, 각 서비스의 인증 secret, Studio·Models의 base64 32바이트
암호화 키, Forge 체크포인트·Git credential·모델 키를 서로 독립적으로 생성합니다. DB URL이 기존 비밀번호와
모순되면 쓰기 전에 중단합니다. 서비스 OIDC client, Studio의 Google OAuth client와 plugins 저장소 token은
생성하지 않고 준비할 경로만 출력합니다.
Secret 환경변수를 변경한 뒤에는 소비자 Deployment를 새 설정으로 rollout해야 합니다.

| SSM 경로 (`/k8s/<cluster>/` 아래) | 소비자 | 준비 |
| --- | --- | --- |
| `agent-platform/database-url` | 여섯 서비스 | bootstrap |
| `agent-<service>/metrics-bearer-token` | Studio·Memory·Knowledge·Forge와 수집기 | bootstrap |
| `agent-studio/auth-secret`, `aes-encryption-key` | Studio | bootstrap |
| `agent-studio/schedule-scan-token` | Studio web·ticker | bootstrap |
| `agent-studio/google-client-id`, `google-client-secret` | Studio(Google 로그인을 켠 경우) | Google OAuth client 생성 후 |
| `agent-studio/plugins-repo-token` | Studio | `PLUGINS_REPO` 저장소에 접근할 GitHub token |
| `agent-studio/git-host-secrets` | Studio(`agent_studio.git_hosts`를 정한 경우) | 각 Git 호스트에 OAuth application 등록 후 |
| `agent-memory/auth-secret` | Memory | bootstrap |
| `agent-models/auth-secret`, `aes-encryption-key` | Models | bootstrap |
| `agent-knowledge/auth-secret` | Knowledge web·worker | bootstrap |
| `agent-forge/auth-secret`, `checkpoint-secret`, `model-secret` | Forge | bootstrap |
| `agent-forge/organization-id` | Studio·Forge | bootstrap |
| `agent-{models,memory,knowledge,forge}/oidc-client-id` | 해당 서비스, Studio(Models 제외) | Studio 등록 후 |
| `agent-{models,memory,knowledge,forge}/oidc-client-secret` | 해당 서비스 | Studio 등록 후 |
| `agent-runway/auth-secret` | Runway | bootstrap(`agent_runway`가 있는 클러스터) |
| `agent-runway/pool-admin-password`, `pool-database-url` | Runway, 풀 bootstrap Job | bootstrap. URL은 비밀번호에서 만든다 |
| `delivery-pool/postgres-password` | 풀 PostgreSQL superuser(bootstrap Job 전용) | bootstrap |
| `agent-runway/oidc-client-id`, `oidc-client-secret` | Runway, Studio | Studio 등록 후 |

Memory·Knowledge 조직 ID는 설정하지 않습니다. Studio가 각 서비스의 공개 MCP `installation_get`으로 확인합니다.
요청 header와 확인 주기는 [Studio 서비스 연결 계약](https://github.com/opspresso/agent-studio/blob/main/docs/design/agent-services.md#연결-설정)을 따릅니다.
Forge의 조직은 설정된 namespace이며 기본값은 `agent-platform-<cluster>`입니다.
ExternalSecret은 참조한 경로가 하나라도 없으면 Secret을 갱신하지 않습니다. 새 경로를 먼저 준비합니다.

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

Forge에는 Git 호스트 설정이나 credential이 없습니다(Forge v0.40.0 이상). 사용자는 Studio에서 Git 호스트를 연결하고,
Forge는 사용할 때마다 Studio에서 짧은 수명의 Git token을 받습니다. 호스트는 [Studio Git 호스트](#studio-git-호스트)에서 선언합니다.

내부 Service와 Pod 포트는 Studio·Memory·Knowledge(웹/worker) `3000`, Forge `3103`, Models `3400`으로 같습니다.
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

## Studio worker 폐기와 독립 서비스 전환

이 절은 Studio audio/Workspace worker를 쓰던 이전 버전의 DB를 변환하는 절차입니다. `eks-demo`의 현재 선언에는
두 worker가 없습니다.

이 전환에서 Studio의 audio/Workspace worker와 Knowledge·Forge의 `STUDIO_URL`이 제거됩니다.
Argo CD의 `prune`이 두 worker Deployment를 삭제합니다. 아래 명령은 [순서와 명령](#순서와-명령)의
`run_once`와 `ECR`, `V_<SERVICE>`를 사용합니다. 각 서비스의 새 release image 게시를 먼저 확인합니다.

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
Runway는 Pod Identity로 SSM·STS·EKS를 호출한다. 계약은 `agent-platform`의 `docs/DELIVERY_RESOURCES.md`, AWS 리소스는 `terraform-env-demo`의
`terraform-env-opsp/demo/8-agent-runway`가 소유한다.

| Application | namespace | 내용 |
| --- | --- | --- |
| `agent-runway-<cluster>` | `agent-runway` | Runway web·API·MCP. `agent_runway_maintenance: true`이면 0개 |
| `delivery-pool-<cluster>` | `delivery-pool` | `postgres` 리소스의 공유 PostgreSQL. bootstrap Job이 비superuser `delivery_admin`(CREATEDB·CREATEROLE)과 `template1`의 `vector`를 준비한다 |
| `delivery-tenants-<cluster>` | (클러스터 범위) | tenant secrets 역할을 쓰는 SecretStore를 검사하는 ValidatingAdmissionPolicy `delivery-tenant-secret-stores` |

- `agent_runway.connected`가 `true`일 때만 Studio(`RUNWAY_URL`, Runway OIDC client ID, MCP 내부 host)와 Forge(`RUNWAY_URL`, Sandbox의 풀 egress)가
  Agent Runway에 연결된다. Runway OIDC client는 Studio가 발급하므로, 새 설치에서는 Studio를 먼저 실행하고
  `register-platform-client.cjs --service agent-runway`로 client를 등록해 SSM에 저장한 뒤 Runway를 켜고 `connected: true`로 바꾼다.
  이 순서를 지키지 않으면 Studio Secret이 없는 client ID를 참조해 Studio가 시작되지 않는다.
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
