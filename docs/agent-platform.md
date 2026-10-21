# Agent Platform 배포

현재 실행 환경은 **k3s `k3s-demo` alpha**입니다. EKS 클러스터는 존재하지 않으며
`eks-demo` prod 값은 향후 설치를 위한 선언입니다. EKS 렌더 성공은 실제 배포·격리 검증을 뜻하지 않습니다.

| 저장소 | 책임 |
| --- | --- |
| `terraform-env-demo` | AWS 리소스, IAM, ECR, EKS Pod Identity, S3 |
| `argocd-env-addons` | Argo CD, External Secrets, Gateway/TLS, CNI, 관측성 |
| `argocd-env-apps` | 네 앱, worker, 실행 namespace/RBAC/NetworkPolicy, PostgreSQL·MinIO |

| 앱 | 공개 호스트 | 처리 책임 |
| --- | --- | --- |
| Studio | `studio.agrt.dev` | Agent·Chat, 승인, 서비스 위임, 오디오/Workspace 관찰·대화 재개 |
| Memory | `memory.agrt.dev` | 사용자·팀·조직의 단기/장기 메모리, recall/remember/handoff |
| Librio | `librio.agrt.dev` | 문서·검색·지식 그래프, 원본 녹음·전사·회의록, durable queue worker |
| Forge | `forge.agrt.dev` | Workspace·Sandbox·Run·Session·Git, native 모델 Gateway |

모든 Agent 앱은 `.agrt.dev` 호스트 하나만 사용합니다. `AUTH_BASE_URL`,
`AUTH_ALLOWED_HOSTS`, HTTPRoute는 같은 호스트를 가리킵니다. Forge는
`ALLOWED_ORIGINS`를 사용합니다. 프록시는 원래 `Host`를 전달해야 합니다.
OAuth 클라이언트에 `https://<hostname>/api/auth/callback/google`을 등록합니다.
Librio는 기존 Memory Google 클라이언트를 명시적으로 참조하고, 세션 Secret은 별도로 사용합니다.
`trusted_proxy_cidrs`는 실제 프록시 hop에 맞춰 EKS는 VPC CIDR, k3s 단일 Traefik은 빈 목록을 사용합니다.

## 기업 OIDC 연결

각 `agent_<service>.oidc` 블록은 Keycloak을 포함한 표준 OIDC 연결을 선언합니다.
실제 client ID·secret은 SSM SecureString으로 준비하고 env에는 경로만 기록합니다.
기존 환경에 이 블록이 없으면 현재 Google 연결을 유지합니다.

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
    client_id_parameter: /k8s/example/agent-studio/oidc-client-id
    client_secret_parameter: /k8s/example/agent-studio/oidc-client-secret
    profile_claims:
      teams: /company/teams
      department: /company/department
      jobTitle: /company/title
      roles: /company/roles
```

Memory·Librio도 같은 `oidc` 필드를 사용하며 `display_name`은 Studio 전용입니다.
`google_sign_in: false`는 Studio·Memory·Librio의 Google credential 주입을 끕니다.
기존 Google 계정과 새로운 OIDC 계정을 이메일만으로 자동 연결하지 않으므로 전환할 계정을 먼저 확인합니다.

Forge는 `agent_forge.oidc`와 `sign_in_domains`를 함께 설정합니다. `oidc`가 없으면 현재
Google issuer와 공유 client를 사용합니다. Forge는 앱 로그아웃만 지원하므로 `logout_mode`는
생략하거나 `app`이어야 합니다. `provider`는 생성 단계에서 거절합니다.

`provider_id`는 기존 계정과 callback을 식별합니다. 기존 Keycloak 전용 설정에서 바꿀 때는
`provider_id: keycloak`을 유지합니다. 새 설치 기본값은 `oidc`입니다. 각 호스트에
`/api/auth/callback/<provider_id>`를 등록하고, 필요한 회사 claim을 ID token에 포함합니다.
회사 팀·역할은 표시 정보이며 앱 권한이나 리소스 팀 멤버십을 자동 부여하지 않습니다.

프로필 schema가 없는 기존 DB는 새 image의 데이터 보존 전환 도구로 준비합니다.
Studio는 schema 11→12 전환, Memory·Librio는 직전 fingerprint→현재 fingerprint 전환을
제공합니다. 서비스를 maintenance로 정지한 뒤 해당 image에서 명시적으로 실행하고 새 버전을
시작합니다. Forge는 확인된 v4–v7에서 v8로 데이터를 보존해 확장합니다. 운영 DB reset은 사용하지 않습니다.

## DB와 원본 보존

네 서비스의 `DATABASE_URL`은 `/k8s/<cluster>/agent-platform/database-url` 하나입니다.
PostgreSQL의 새 빈 `agent_platform` DB를 사용하고, 업무 테이블은 각각
`agent_studio_`, `agent_memory_`, `agent_librio_`, `agent_forge_` 접두어로 구분합니다.
Librio pg-boss도 같은 DB에 전용 queue namespace를 사용합니다.

`postgres` StatefulSet, `data-postgres-0` PVC, 기존 `agent_studio` DB 사용자와 비밀번호는 유지합니다.
`postgres-bootstrap`은 새 DB가 없을 때만 만들고 `vector` extension을 활성화합니다.
이미 있는 `agent_studio`·`agent_memory` DB를 삭제하거나 복사하지 않습니다.
앱 이미지는 자신의 namespace만 초기화하며 운영 DB reset은 배포 절차에 없습니다.
PostgreSQL readiness는 기존 DB와 새 DB 어느 쪽에도 의존하지 않는 `postgres` DB로 확인합니다.
`max_connections` 변경은 기존 StatefulSet의 재시작을 수반합니다.

k3s MinIO는 기존 PVC와 `agent-studio-static`, `agent-memory` 버킷을 보존하고
`agent-librio` 버킷을 추가합니다. Studio Artifact와 Librio 원본은 각 앱의 인증된 API로 접근합니다.
Memory와 Forge에는 S3 자격 증명을 주입하지 않습니다.
Librio 지식 그래프는 PostgreSQL을 사용하며 Neo4j 환경변수를 주입하지 않습니다.
Neo4j 배포·Secret bootstrap은 제공하지 않습니다. 이전 리소스의 삭제 절차는
[폐기된 실행·그래프 자원 정리](retired-agent-resources.md)를 따릅니다.

미래 EKS의 S3 SDK는 Pod Identity를 사용합니다. MinIO endpoint와 정적 S3 키는 주입하지 않습니다.

| ServiceAccount | IAM role | 버킷 |
| --- | --- | --- |
| `agent-studio/agent-studio` | `pod-role--agent-studio` | `agent-studio-static` |
| `agent-librio/agent-librio` | `pod-role--agent-librio` | `agent-librio-static` |

## 인증과 Secret 준비

External Secrets는 `/k8s/<cluster>/` 경로를 읽습니다. 앱 namespace와 Forge 실행 namespace에는
각각 ECR generator와 `ecr-registry` Secret이 있습니다. k3s 기존 EC2 역할,
미래 EKS External Secrets의 IAM 연결을 사용하며 앱에 ECR 장기 키를 전달하지 않습니다.

```bash
python3 scripts/bootstrap_agent_platform.py --cluster k3s-demo
python3 scripts/bootstrap_agent_platform.py --cluster k3s-demo --apply
```

첫 명령은 누락 경로만 출력합니다. `--apply`는 누락된 SecureString만 만들고 기존 값을 덮어쓰지 않습니다.
기존 PostgreSQL 비밀번호에서 공통 DB URL을 만들며, 위임 키·정책 callback 키·체크포인트 키는
서로 독립적으로 생성합니다. 연결 URL이나 파생 JSON이 기존 키와 모순되면 쓰기 전에 중단합니다.
Secret 환경변수를 변경한 뒤에는 소비자 Deployment를 새 설정으로 rollout해야 합니다.
추가 personal credential 또는 key rotation은 이 최초 bootstrap과 분리해 모든 소비자를 함께 갱신합니다.

| SSM 경로 (`/k8s/<cluster>/` 아래) | 소비자 |
| --- | --- |
| `agent-platform/database-url` | 네 서비스 |
| `agent-studio/metrics-bearer-token` | Studio와 수집기의 bearer |
| `agent-memory/delegation-secret` | Memory와 Studio |
| `agent-librio/delegation-secret` | Librio와 Studio |
| `agent-librio/policy-secret`, `issuer-policies` | Studio callback / Librio |
| `agent-librio/better-auth-secret`, `metrics-bearer-token`, `audio-models` | Librio web·worker |
| `agent-forge/delegation-secret`, `organization-id`, `delegators` | Studio / Forge |
| `agent-forge/policy-secret`, `issuer-policies` | Studio callback / Forge |
| `agent-forge/credentials`, `metrics-bearer-token` | Forge의 hash / 수집기의 bearer |
| `agent-forge/checkpoint-secret`, `credential-secret`, `model-secret`, `model-channels` | Forge |
| `agent-forge/session-secret` | Forge 콘솔 로그인 세션 쿠키 서명 |
| `common/agent-memory/google-client-id`, `google-client-secret` | Memory와 Forge 콘솔 로그인(같은 Google client) |

Forge 콘솔 로그인은 `env/<cluster>.yaml`의 `agent_forge.sign_in_domains`가 있을 때만 켜집니다.
Google client의 redirect URI에 `https://<agent_forge.hostname>/api/auth/callback/google`이 등록돼 있어야 합니다.

bootstrap은 모델 JSON을 생성하지 않습니다. Memory·Librio 조직 ID는 설정하지 않습니다.
Studio가 위임 토큰에 서명하기 전에 각 서비스의 `GET /api/v1/installation`에서 설치 조직 ID를 읽습니다.
그래서 Memory·Librio DB를 다시 초기화해도 Studio 설정을 바꿀 필요가 없습니다.
Memory·Librio는 Studio가 서명한 `email` claim으로 회원을 결정합니다. 계정이 없으면 만들고,
가입 대기 회원은 승인하며, 차단·제거된 회원은 거부합니다.
Forge의 조직은 설정된 namespace이며 기본값은 `agent-platform-<cluster>`입니다.

Studio issuer는 `https://studio.agrt.dev`입니다. 위임 토큰은 서비스별 audience·scope·현재 사용자와
조직을 고정합니다. Forge/Librio의 고정 callback은 내부 Studio Service의
`/api/services/forge/model-policy`, `/api/services/librio/model-policy`를 사용하며 별도 키로 인증합니다.
callback의 내부 Studio 주소는 `http://agent-studio.agent-studio.svc.cluster.local:3000`입니다.
임의 callback URL이나 요청 body의 owner를 신뢰하지 않습니다.
모니터링용 Forge principal은 `workspace:read`만 가지며 controller에는 토큰의 SHA256만 전달합니다.

Librio `audio-models`와 Forge `model-channels`는 실제 등록 provider/model을 기준으로 준비합니다.
provider wire ID, 공개 model ID, catalog ID를 구분하고 미확인 가격을 0원으로 만들지 않습니다.
빈 배열이나 존재하지 않는 모델로 음성·native 실행이 준비됐다고 판단하지 않습니다.

## 실행과 네트워크

Studio의 `agent-studio-audio-worker`와 `agent-studio-workspace-worker`는 원격 작업을 관찰하고
후속 Agent/Chat을 실행합니다. Studio에는 Sandbox 실행 권한·Docker daemon·Kubernetes token이 없습니다.
Workspace readiness는 Studio DB와 observer 설정/heartbeat를 확인합니다. Forge 장애는
Studio observer의 liveness를 실패시키지 않습니다. ticker는 scan/plugin sync를 매분, reindex를 매시간 호출합니다.

Librio는 웹과 별도 `agent-librio-worker` 하나를 사용합니다. 문서/Audio 소비는 worker에서만 활성화합니다.
웹과 worker는 같은 이미지·모델·AI admission 정책을 사용합니다. quota는 동일 PostgreSQL에서
interactive/batch와 전체 provider 상한을 합산합니다. pg-boss에는 별도 DB를 만들지 않습니다.

Forge는 `agent-forge` 하나의 프로세스에서 HTTP·MCP·worker·native Gateway를 제공합니다.
`agent-forge-workspaces` namespace의 controller-owned 실행 Pod만 다룹니다.
Sandbox 이미지는 같은 release의 `agent-forge:sandbox-<full-v-tag>`이며 독립 `latest`를 쓰지 않습니다.
공급자·Git 장기 키는 Sandbox에 전달하지 않습니다. 자세한 권한·보존 계약은
[Kubernetes Workspace 운영](workspace-kubernetes.md)을 따릅니다.

내부 Service와 Pod 포트는 Studio·Memory·Librio(웹/worker) `3000`, Forge `3103`으로 같습니다.
HTTPRoute backend, Studio 서비스 연결, callback, ticker와 지표 수집도 이 포트를 사용합니다.
앱 ingress는 해당 플랫폼 Gateway, 수집기, 같은 namespace와 명시된 서비스 peer만 허용합니다.
k3s Gateway 리소스는 `traefik-gateway` namespace에 있지만 실제 출발 Pod는 `kube-system`에 있습니다.
Gateway 접근은 `app.kubernetes.io/name=traefik`, `app.kubernetes.io/instance=traefik-kube-system`을
함께 가진 Pod로 제한하며 다른 `kube-system` Pod를 허용하지 않습니다.
Studio에는 Forge/Librio callback, Forge에는 Sandbox의 인증된 Gateway 연결을 허용합니다.
신뢰된 control-plane의 외부 LLM·MCP egress는 기존 동작을 유지합니다.
Sandbox는 별도 기본 거부 정책으로 DNS·public HTTP/HTTPS·Forge Pod 포트만 허용합니다.

## 최초 전환 순서

1. 네 앱의 필수 검사와 release image 게시를 확인합니다. 전환 동안 `GITOPS_AUTO_DEPLOY=false`로
   자동 버전 dispatch를 멈추고 Studio·Memory·Librio·Forge controller+Sandbox image를 모두 준비합니다.
2. Terraform의 ECR/IAM, addons의 `.agrt.dev` DNS/TLS·ESO·Gateway·관측성 계약을 확인합니다.
   이 절차로 EKS를 만들지 않습니다. k3s의 기존 node·PVC·bucket은 보존합니다.
3. 기존 Studio 작업 신규 접수를 막고 진행 중 실행의 체크포인트·종료를 확인합니다.
   새 앱을 올리기 전에 Secret, `agent_platform`, MinIO Librio 버킷을 준비합니다.
4. 새 이미지로 Memory/Librio 초기화를 완료하고 실제 organization ID와 모델 설정을 검증합니다.
   사용자 연결은 로그인한 계정으로 설정합니다. 조직 ID가 준비되기 전에 Studio 연결을 활성화하지 않습니다.
5. `scripts/gitops.py deploy --dry-run`으로 phase 버전과 이력을 생성하고 chart 변경과 함께 반영합니다.
   phase 값을 직접 편집하지 않습니다. 동일 배포에서 새 이미지·DB URL·서비스 경계를 전환합니다.
6. Argo CD `Synced/Healthy`, 정확한 image tag, DB readiness와 metrics를 확인합니다.
   로그인, Memory recall/remember, Librio 원본 upload→전사/회의록→검색, Forge 실행→로그→취소/복원을 확인합니다.
7. 정상 동작을 확인한 뒤 앱 release의 자동 GitOps gate를 원래 설정으로 복원합니다.

`agent_<service>_maintenance: true`는 해당 서비스의 웹·worker/HPA만 정지합니다.
Studio는 ticker도 정지합니다. 각 flag는 다른 앱·DB·storage를 삭제하지 않습니다.
이전 Workspace PVC/namespace와 구 실행기는 더 이상 선언하지 않습니다.
기존 실데이터 삭제는 [별도 운영 절차](retired-agent-resources.md)로 수행합니다.

## 검증

```bash
GITHUB_PUSH=false bash scripts/build.sh
python3 scripts/validate.py --all-charts
pytest
```

Helm/pytest는 alpha와 미래 EKS의 resource 정책, 같은 DB URL, 인증·metrics, 이미지 연동,
RBAC·NetworkPolicy·quota·보존 계약을 검사합니다. 실제 환경 검증은 별도입니다.
Memory/Librio web은 `/api/live`와 DB readiness `/api/health`를 나누고, Librio worker는 초기화 후
`/api/health`를 제공합니다. Forge `/health`는 DB/backend readiness, `/`는 독립 HTTP liveness입니다.
Librio/Memory `/api/metrics`, Forge `/metrics`는 전용 bearer를 요구합니다.
health 성공만으로 원본 storage·provider·작업 성공을 판정하지 않습니다.

DB 용량과 롤링 배포 연결 예산은 [용량 운영](production-capacity.md)을 따릅니다.
PostgreSQL OS/libc를 바꿀 때는 기존·보관 DB와 `template1`의 collation 버전도 확인합니다.
경고를 없애려고 데이터나 인덱스 재구성 없이 버전만 덮어쓰지 않습니다.
