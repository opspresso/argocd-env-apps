# Agent Platform 배포

현재 실행 환경은 **k3s `k3s-demo` alpha**입니다. EKS 클러스터는 존재하지 않으며
`eks-demo` prod 값은 향후 설치를 위한 선언입니다. EKS 렌더 성공은 실제 배포·격리 검증을 뜻하지 않습니다.

| 저장소 | 책임 |
| --- | --- |
| `terraform-env-demo` | AWS 리소스, IAM, ECR, EKS Pod Identity, S3 |
| `argocd-env-addons` | Argo CD, External Secrets, Gateway/TLS, CNI, 관측성 |
| `argocd-env-apps` | 다섯 앱, worker, 실행 namespace/RBAC/NetworkPolicy, PostgreSQL·MinIO |

| 앱 | 공개 호스트 | 처리 책임 |
| --- | --- | --- |
| Studio | `studio.agrt.dev` | Agent·Chat, 승인, 서비스 위임, 오디오/Workspace 관찰·대화 재개 |
| Memory | `memory.agrt.dev` | 사용자·팀·조직의 단기/장기 메모리, recall/remember/handoff |
| Librio | `librio.agrt.dev` | 문서·검색·지식 그래프, 원본 녹음·전사·회의록, durable queue worker |
| Forge | `forge.agrt.dev` | Workspace·Sandbox·Run·Session·Git, native 모델 Gateway |
| Models | `models.agrt.dev` | 공급자 연결·자격 증명, 모델 선택, 공급자 호출, 사용량 원장 |

모든 Agent 앱은 `.agrt.dev` 호스트 하나만 사용합니다. `AUTH_BASE_URL`,
`AUTH_ALLOWED_HOSTS`, HTTPRoute는 같은 호스트를 가리킵니다. Forge는
`ALLOWED_ORIGINS`를 사용합니다. 프록시는 원래 `Host`를 전달해야 합니다.
`trusted_proxy_cidrs`는 실제 프록시 hop에 맞춰 EKS는 VPC CIDR, k3s 단일 Traefik은 빈 목록을 사용합니다.

## 로그인과 모델 연결

Studio만 외부 로그인 제공자(Google 또는 기업 OIDC)와 연결합니다. Memory·Librio·Forge·Models는
Studio OIDC로 로그인합니다. 템플릿은 다음 값을 고정으로 렌더합니다.

| 설정 | Memory·Librio·Forge·Models |
| --- | --- |
| `OIDC_ISSUER` | `https://<agent_studio.hostname>/api/auth` |
| `OIDC_PROVIDER_ID` | `studio`. callback은 `https://<서비스 호스트>/api/auth/callback/studio` |
| `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` | `/k8s/<cluster>/agent-<service>/oidc-client-id`, `oidc-client-secret` |
| `MODELS_URL` | `http://agent-models.agent-models.svc.cluster.local:3400` (Models 제외) |
| `STUDIO_URL` | `http://agent-studio.agent-studio.svc.cluster.local:3000` (Librio·Forge의 정책 callback) |

`gen_values.py`는 이 네 서비스의 `oidc`, `google_sign_in`, `sign_in_domains`를 거부합니다.
Studio는 같은 `oidc-client-id` 경로를 `MEMORY_OIDC_CLIENT_ID`, `LIBRIO_OIDC_CLIENT_ID`,
`FORGE_OIDC_CLIENT_ID`로 읽습니다. Studio에는 `MODELS_URL`과 브라우저용
`MODELS_PUBLIC_URL=https://<agent_models.hostname>`을 설정합니다.

공급자 주소·API 키·wire ID·가격은 Models만 소유합니다. 소비 서비스에는 공급자 설정을 주입하지 않습니다.
Memory·Librio의 모델 역할(`EMBEDDING_MODEL` 등)은 Models가 선택 모델을 활성화한 뒤 각 서비스의
**설정**에서 고릅니다. Forge의 `FORGE_MODEL_CHANNELS`는 `/k8s/<cluster>/agent-forge/model-roles`에서 읽습니다.
각 항목은 `id`, `runtime`, `model`(Models 선택 ID), `protocol`과 선택 한도만 가집니다. 공급자 연결 정보가
있는 이전 `model-channels` 형식은 Forge가 시작 단계에서 거부합니다. 역할이 없으면 `[]`을 저장합니다.

### Studio 기업 OIDC

`agent_studio.oidc` 블록은 Keycloak을 포함한 기업 OIDC 연결을 선언합니다.
실제 client ID·secret은 SSM SecureString으로 준비하고 env에는 경로만 기록합니다.
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
    client_id_parameter: /k8s/example/agent-studio/oidc-client-id
    client_secret_parameter: /k8s/example/agent-studio/oidc-client-secret
    profile_claims:
      teams: /company/teams
      department: /company/department
      jobTitle: /company/title
      roles: /company/roles
```

`google_sign_in: false`는 Studio의 Google credential 주입을 끕니다. 기존 Google 계정과 새 OIDC 계정을
이메일만으로 자동 연결하지 않으므로 전환할 계정을 먼저 확인합니다. `provider_id`는 기존 계정과
callback을 식별합니다. 기존 Keycloak 전용 설정에서 바꿀 때는 `provider_id: keycloak`을 유지합니다.
새 설치 기본값은 `oidc`입니다. 회사 팀·역할은 표시 정보이며 앱 권한을 자동 부여하지 않습니다.

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
기존 PostgreSQL 비밀번호에서 공통 DB URL을 만들며, Models의 세션 secret과 base64 32바이트 암호화 키,
Forge 체크포인트·세션 키를 서로 독립적으로 생성합니다. DB URL이 기존 비밀번호와 모순되면 쓰기 전에 중단합니다.
서비스 OIDC client와 Forge `model-roles`는 생성하지 않고 준비할 경로만 출력합니다.
Secret 환경변수를 변경한 뒤에는 소비자 Deployment를 새 설정으로 rollout해야 합니다.

| SSM 경로 (`/k8s/<cluster>/` 아래) | 소비자 | 준비 |
| --- | --- | --- |
| `agent-platform/database-url` | 다섯 서비스 | bootstrap |
| `agent-<service>/metrics-bearer-token` | Studio·Memory·Librio·Forge와 수집기 | bootstrap |
| `agent-models/auth-secret`, `encryption-key` | Models | bootstrap |
| `agent-librio/better-auth-secret` | Librio web·worker | bootstrap |
| `agent-forge/checkpoint-secret`, `credential-secret`, `model-secret`, `session-secret` | Forge | bootstrap |
| `agent-forge/organization-id` | Studio·Forge | bootstrap |
| `agent-{models,memory,librio,forge}/oidc-client-id` | 해당 서비스, Studio(Models 제외) | Studio 등록 후 |
| `agent-{models,memory,librio,forge}/oidc-client-secret` | 해당 서비스 | Studio 등록 후 |
| `agent-forge/model-roles` | Forge | Models 선택 후, 없으면 `[]` |

Memory의 `AUTH_SECRET`은 `/k8s/common/agent-memory/better-auth-secret`을 유지합니다.
Memory·Librio 조직 ID는 설정하지 않습니다. Studio가 각 서비스의 `GET /api/v1/installation`에서 읽습니다.
Forge의 조직은 설정된 namespace이며 기본값은 `agent-platform-<cluster>`입니다.
ExternalSecret은 참조한 경로가 하나라도 없으면 Secret을 갱신하지 않습니다. 새 경로를 먼저 준비합니다.

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

내부 Service와 Pod 포트는 Studio·Memory·Librio(웹/worker) `3000`, Forge `3103`, Models `3400`으로 같습니다.
HTTPRoute backend, Studio 서비스 연결, callback, ticker와 지표 수집도 이 포트를 사용합니다.
앱 ingress는 해당 플랫폼 Gateway, 수집기, 같은 namespace와 명시된 서비스 peer만 허용합니다.
k3s Gateway 리소스는 `traefik-gateway` namespace에 있지만 실제 출발 Pod는 `kube-system`에 있습니다.
Gateway 접근은 `app.kubernetes.io/name=traefik`, `app.kubernetes.io/instance=traefik-kube-system`을
함께 가진 Pod로 제한하며 다른 `kube-system` Pod를 허용하지 않습니다.
Studio에는 Forge/Librio callback, Forge에는 Sandbox의 인증된 Gateway 연결, Models에는 네 소비 서비스의
연결을 허용합니다. Sandbox는 Models에 직접 연결하지 않고 Forge Gateway를 거칩니다.
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

## Models 활성화와 Studio OIDC 전환 (alpha)

`agent_models_maintenance: true`이면 Models Deployment는 0개이고 HPA가 없습니다.
ConfigMap·ExternalSecret·HTTPRoute·NetworkPolicy는 유지합니다. `values-<phase>.yaml`의 버전 키는
`scripts/gitops.py`만 기록합니다. 직접 tag를 쓰지 않습니다.

### 부팅 호환성

새 image는 이전 설정에서, 이전 image는 새 설정에서 정상 동작하지 않습니다. 그래서 Studio만 먼저
올리고 나머지 네 서비스는 설정과 image를 한 커밋으로 바꿉니다.

| 서비스 | 새 image + 현재 `main` 설정 | 새 image + 새 설정 |
| --- | --- | --- |
| Studio | 부팅함. `DATABASE_URL`·`AES_ENCRYPTION_KEY`·`STAGE`·`ADMIN_EMAILS`·Google 로그인이 있음. 이전 서비스와의 위임 호출은 실패 | 부팅함. `*_OIDC_CLIENT_ID` SSM이 없으면 ExternalSecret이 갱신되지 않고 서비스 호출만 실패 |
| Memory·Librio | 시작 실패. production은 `OIDC_ISSUER`·`OIDC_CLIENT_ID`·`OIDC_CLIENT_SECRET`을 요구 | client SSM과 schema 변환 후 부팅 |
| Forge | 시작 실패. Studio OIDC 필수, 이전 `model-channels` 형식 거부 | client SSM과 `model-roles` 후 부팅. schema는 시작 시 확장 |
| Models | 해당 없음 | `auth-secret`·`encryption-key`·client SSM 후 부팅 |

Studio·Memory·Librio의 새 image는 기존 schema를 자동 변환하지 않습니다. 변환 도구를 새 image로
실행한 직후 새 버전을 시작합니다. 변환 후 이전 image는 readiness에 실패합니다.

### 순서와 명령

아래 명령은 k3s `kubectl` context와 `argocd-env-apps` checkout을 사용합니다.
`ECR=396608815058.dkr.ecr.ap-northeast-2.amazonaws.com`, `V_<SERVICE>`는 각 서비스의 새 release tag입니다.

```bash
# One-off command with a new image and the service's existing Secret.
run_once() { ns=$1; image=$2; shift 2
  kubectl -n "$ns" run "$ns-once" --rm -i --restart=Never --image="$image" --overrides="$(jq -nc \
    --arg i "$image" --arg s "$ns-external" '{spec:{imagePullSecrets:[{name:"ecr-registry"}],
    containers:[{name:"run",image:$i,command:$ARGS.positional,envFrom:[{secretRef:{name:$s}}]}]}}' --args -- "$@")"; }
```

1. **ECR·IAM**: `terraform-env-demo/demo/8-agent-studio`에서 Models ECR·게시 역할만 먼저 적용합니다.
   `models.agrt.dev` DNS는 9단계에서 바꿉니다.
   ```bash
   terraform apply -target='aws_ecr_repository.this["agent-models"]' -target='aws_ecr_lifecycle_policy.this["agent-models"]' \
     -target='aws_iam_role.github_ecr["agent-models"]' -target='aws_iam_policy.github_ecr["agent-models"]' \
     -target='aws_iam_role_policy_attachment.github_ecr["agent-models"]' -target=aws_iam_policy.k3s_ecr_pull
   ```
2. **Gateway 인증서**: `argocd-env-addons`의 `models.agrt.dev` host 추가를 병합합니다.
   `kubectl -n traefik-gateway get certificate traefik-gateway-tls`가 `Ready=True`이고
   `kubectl -n traefik-gateway get secret traefik-gateway-tls -o jsonpath='{.data.tls\.crt}' | base64 -d | openssl x509 -noout -ext subjectAltName`에
   `models.agrt.dev`가 있는지 확인합니다.
3. **image 게시**: 다섯 서비스의 자동 GitOps를 멈추고 release tag를 push합니다. image만 ECR에 게시됩니다.
   ```bash
   for r in agent-studio agent-memory agent-librio agent-forge agent-models; do
     gh variable set GITOPS_AUTO_DEPLOY --body false -R "opspresso/$r"; done
   # 각 저장소에서 git tag vX.Y.Z && git push origin vX.Y.Z, release workflow 성공 확인
   aws ecr describe-images --repository-name agent-forge --image-ids imageTag="$V_FORGE" imageTag="sandbox-$V_FORGE"
   ```
4. **Studio 먼저 전환**: schema를 변환하고 바로 alpha에 배포합니다. 이 순간부터 7단계까지 Studio에서
   Memory·Librio·Forge 호출과 모델 실행은 실패합니다.
   ```bash
   run_once agent-studio "$ECR/agent-studio:$V_STUDIO" node build/upgrade-platform-auth.cjs --apply  # baseline 12→13
   gh api repos/opspresso/argocd-env-apps/dispatches -f event_type=gitops -f client_payload[username]=opspresso \
     -f client_payload[project]=agent-studio -f client_payload[version]="$V_STUDIO" -f client_payload[container]=app \
     -f client_payload[phase]=alpha -f client_payload[type]=helm
   curl -fsS https://studio.agrt.dev/api/ready
   curl -fsS https://studio.agrt.dev/api/auth/.well-known/openid-configuration | jq -r .issuer  # https://studio.agrt.dev/api/auth
   ```
   부팅이 `Unsupported stored secret format`으로 중단되면 Studio 운영 문서의 Settings 시크릿 전환을 실행합니다.
5. **client 등록**: Studio 관리자로 **Settings → Platform connections**에서 네 서비스를 등록합니다.
   provider ID는 `studio`, 서비스 URL은 `https://models.agrt.dev`, `https://memory.agrt.dev`,
   `https://librio.agrt.dev`, `https://forge.agrt.dev`입니다. callback은 `<URL>/api/auth/callback/studio`입니다.
   같은 기능의 API는 관리자 세션의 `POST /api/platform/clients`
   (`{"service":"agent-memory","baseUrl":"https://memory.agrt.dev","providerId":"studio"}`)입니다.
   응답의 `client.id`와 `secret`은 한 번만 표시되므로 바로 6단계에 저장합니다.
6. **SSM**: 값을 명령줄 이력에 남기지 않도록 입력 파일이나 prompt로 전달합니다.
   ```bash
   for s in agent-models agent-memory agent-librio agent-forge; do
     read -rs -p "$s client id: " id; echo; read -rs -p "$s client secret: " secret; echo
     aws ssm put-parameter --type SecureString --name "/k8s/k3s-demo/$s/oidc-client-id" --value "$id"
     aws ssm put-parameter --type SecureString --name "/k8s/k3s-demo/$s/oidc-client-secret" --value "$secret"; done
   aws ssm put-parameter --type SecureString --name /k8s/k3s-demo/agent-forge/model-roles --value '[]'
   python3 scripts/bootstrap_agent_platform.py --cluster k3s-demo --apply  # agent-models/auth-secret, encryption-key
   python3 scripts/bootstrap_agent_platform.py --cluster k3s-demo          # 남은 누락이 없어야 함
   ```
7. **설정과 image를 한 커밋으로 전환**: 전환 브랜치에서 네 서비스의 alpha·prod 버전을 기록하고
   `env/k3s-demo.yaml`의 `agent_models_maintenance`를 `false`로 바꿉니다.
   ```bash
   git rebase origin/main
   for p in alpha prod; do
     TG_PROJECT=agent-memory TG_VERSION="$V_MEMORY" TG_PHASE=$p python3 scripts/gitops.py deploy --dry-run
     TG_PROJECT=agent-librio TG_VERSION="$V_LIBRIO" TG_PHASE=$p python3 scripts/gitops.py deploy --dry-run
     TG_PROJECT=agent-forge  TG_VERSION="$V_FORGE"  TG_PHASE=$p python3 scripts/gitops.py deploy --dry-run
     TG_PROJECT=agent-models TG_VERSION="$V_MODELS" TG_PHASE=$p python3 scripts/gitops.py deploy --dry-run; done
   TG_PROJECT=agent-studio TG_VERSION="$V_STUDIO" TG_PHASE=prod python3 scripts/gitops.py deploy --dry-run
   ./scripts/build.sh && ./scripts/validate.py --all-charts && pytest
   git commit -am "Deploy Agent platform Studio OIDC and Models" && git push
   ```
   병합 직전에 Memory·Librio schema를 변환합니다. 이 시점부터 새 버전이 Ready가 될 때까지 두 서비스는 503입니다.
   ```bash
   kubectl -n agent-studio exec postgres-0 -- psql -U agent_studio -d agent_platform -Atc 'SELECT fingerprint FROM agent_memory_schema'
   run_once agent-memory "$ECR/agent-memory:$V_MEMORY" node scripts/upgrade-enterprise-profile.mjs --apply  # 이전 fingerprint일 때만
   run_once agent-memory "$ECR/agent-memory:$V_MEMORY" node scripts/upgrade-model-work.mjs --apply          # 9eeb878d…일 때
   run_once agent-librio "$ECR/agent-librio:$V_LIBRIO" node scripts/upgrade-platform-auth.mjs --apply      # v0.18.1·v0.19.0–v0.20.1
   ```
   도구가 기준선을 거절하고 데이터 보존이 필요 없으면 `node scripts/reset-database.mjs`로 해당 서비스 namespace만
   다시 만듭니다. 이어서 PR을 병합합니다. PostgreSQL은 `max_connections` 변경으로 한 번 재시작합니다.
8. **전환 확인**:
   ```bash
   kubectl get externalsecret -A | grep agent-        # 모두 SecretSynced
   kubectl -n agent-memory annotate externalsecret agent-memory-external force-sync="$(date +%s)" --overwrite  # 필요할 때
   curl -fsS https://memory.agrt.dev/api/health && curl -fsS https://librio.agrt.dev/api/health
   curl -fsS https://forge.agrt.dev/health
   kubectl -n agent-models exec deploy/agent-models -- node -e "fetch('http://127.0.0.1:3400/api/ready').then(r => process.exit(r.ok ? 0 : 1))"
   curl -fsS --resolve models.agrt.dev:443:"$(dig +short studio.agrt.dev)" https://models.agrt.dev/api/ready
   ```
   각 서비스에 Studio로 로그인하고 Studio의 Memory·Librio·Forge 연결 상태를 확인합니다.
9. **Models DNS 전환**: `terraform-env-demo` 변경을 병합하고 전체 apply로 `models.agrt.dev`를 k3s A 레코드로
   바꿉니다. 이어서 GitHub Pages를 끕니다. 카탈로그는 Models image가 같은 경로로 제공합니다.
   ```bash
   terraform plan -out=tf.plan && terraform apply tf.plan
   gh api -X DELETE repos/opspresso/agent-models/pages
   dig +short models.agrt.dev                                   # k3s IP
   curl -fsS https://models.agrt.dev/api/ready && curl -fsS https://models.agrt.dev/models.json | jq -r .version
   ```
10. **모델 연결**: Models 콘솔에서 공급자를 연결하고 모델을 선택합니다. 이전 공급자 키는 여기서 다시 입력합니다.
    Memory·Librio는 **설정**에서 역할을 선택합니다. Forge는 `model-roles`에 선택 ID를 저장하고
    `agent-forge/model-roles-version` annotation을 올려 재시작합니다. 실제 호출과 Models 사용량 집계를 확인합니다.
11. **자동 배포 복원**: `values-prod.yaml`이 alpha와 같은 버전인지 확인하고 자동 GitOps를 복원합니다.
    ```bash
    for c in agent-studio agent-memory agent-librio agent-forge agent-models; do
      diff <(yq .app.image.tag charts/$c/values-alpha.yaml) <(yq .app.image.tag charts/$c/values-prod.yaml); done
    for r in agent-studio agent-memory agent-librio agent-forge agent-models; do
      gh variable delete GITOPS_AUTO_DEPLOY -R "opspresso/$r"; done
    ```
12. 11단계를 모든 클러스터에서 확인한 뒤 더 이상 참조하지 않는 SSM 파라미터를 삭제합니다.
   `/k8s/common/` 경로는 모든 클러스터가 공유하므로 마지막 클러스터를 전환한 뒤 삭제합니다.
   삭제 전에 `grep -rn '<경로>' charts/*/values*.j2 charts/*/values.yaml`과 `argocd-env-addons`에서
   참조가 없음을 확인합니다. 삭제는 되돌릴 수 없으므로 필요하면 값을 먼저 별도로 보관합니다.

   | 경로 | 이전 용도 |
   | --- | --- |
   | `/k8s/<cluster>/agent-forge/credentials`, `delegators`, `issuer-policies`, `delegation-secret`, `policy-secret` | 공유 HMAC 위임·정책 callback |
   | `/k8s/<cluster>/agent-forge/model-channels` | 공급자 정보를 포함한 이전 Forge 모델 채널 |
   | `/k8s/<cluster>/agent-librio/delegation-secret`, `issuer-policies`, `policy-secret`, `audio-models` | 위임·정책 callback·이전 음성 모델 |
   | `/k8s/<cluster>/agent-memory/delegation-secret` | 위임 |
   | `/k8s/<cluster>/agent-memory/organization-id`, `agent-librio/organization-id` | 사용하지 않음. Studio가 installation API로 읽음 |
   | `/k8s/common/agent-memory/embedding-api-key`, `reranker-api-key`, `decision-api-key`, `knowledge-extraction-api-key` | 공급자 키. Models 공급자 연결로 이동 |
   | `/k8s/common/agent-memory/google-client-id`, `google-client-secret` | Memory·Librio·Forge Google 로그인 |
   | `/k8s/common/agent-studio/llm-provider-{openai,anthropic,google,xai,openrouter,selfhosted}-api-key` | Studio 공급자 키. Models 공급자 연결로 이동 |

   공급자 키는 Models의 공급자 연결에 다시 입력한 뒤 삭제합니다. `/k8s/common/agent-studio/`의
   `a2a-api-key`, `llm-api-key`, `slack-bot-token`, `slack-signing-secret`, `better-auth-secret`,
   `google-client-id`, `google-client-secret`는 이 전환 전부터 GitOps가 참조하지 않습니다.
   다른 소유자가 없는지 확인한 뒤 별도로 정리합니다.

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
Librio/Memory `/api/metrics`와 Forge `/metrics`는 `METRICS_BEARER_TOKEN` 전용 bearer를 요구합니다.
Forge는 `agent-forge/metrics-bearer-token`을 `METRICS_BEARER_TOKEN`으로 받습니다. 이 값을 지원하지 않는
Forge image는 Studio가 발급한 `workspace:read` token만 받으므로 k3s Alloy·EKS ServiceMonitor 수집이 실패합니다.
health 성공만으로 원본 storage·provider·작업 성공을 판정하지 않습니다.

DB 용량과 롤링 배포 연결 예산은 [용량 운영](production-capacity.md)을 따릅니다.
PostgreSQL OS/libc를 바꿀 때는 기존·보관 DB와 `template1`의 collation 버전도 확인합니다.
경고를 없애려고 데이터나 인덱스 재구성 없이 버전만 덮어쓰지 않습니다.
