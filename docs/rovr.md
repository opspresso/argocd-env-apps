# rovr 배포

rovr(<https://rovr.dev>)는 목표를 받아 Leader 에이전트가 작업을 나누고, Sub 에이전트가 작업마다 격리된 Sandbox에서
브랜치와 PR을 만드는 SaaS다. 애플리케이션 저장소는 `opspresso/rovr`이고, 이 저장소는 `charts/rovr`로 운영 클러스터
`eks-demo`(phase `prod`)에 배포한다.

| 저장소 | rovr에 대한 책임 |
| --- | --- |
| `terraform-env-demo` | ECR `rovr`(`v*`, `sandbox-v*`), 릴리스 역할 `github--rovr-ecr`, NodePool `rovr-sandboxes`, `rovr.dev` 인증서·DNS |
| `argocd-env-addons` | ClusterSecretStore `parameter-store`가 `rovr` namespace를 허용한다. Istio Gateway·VPC CNI NetworkPolicy는 그대로 쓴다 |
| `argocd-env-apps` | `charts/rovr`, `apps/eks/rovr.yaml`, `env/eks-demo.yaml`의 `rovr` 블록, 공유 PostgreSQL의 rovr DB·역할 |
| `opspresso/rovr` | 애플리케이션, `v*` 태그 릴리스, 이 저장소로의 GitOps dispatch |

## 구성

| 자원 | 위치 | 설명 |
| --- | --- | --- |
| `Deployment/rovr` | `rovr` | 고정 2 replica. 한 replica만 PostgreSQL advisory lock으로 orchestrator를 실행한다. HPA 없음 |
| `Service/rovr` | `rovr` | `8080` 공개 listener(웹·API·로그인·webhook), `8081` 내부 listener(Sandbox callback·모델 proxy) |
| `HTTPRoute/rovr` | `rovr` | `rovr.dev` → `8080`만. `8081`은 외부로 노출하지 않는다 |
| `NetworkPolicy/rovr` | `rovr` | `8080`은 같은 namespace·Istio·Prometheus, `8081`은 Sandbox Pod만 |
| Sandbox namespace | `rovr-sandboxes` | Job 소유 Pod, 토큰 미마운트 SA `sandbox`, default-deny와 DNS·공인 80/443·rovr `8081`만 허용, quota, ECR pull secret |
| `Role/rovr-sandbox` | `rovr-sandboxes` | rovr SA는 jobs 조회·생성·삭제, pods 조회, run Secret 생성, 작업 PVC 목록·생성·삭제만 할 수 있다. 목록은 끝난 task의 PVC 정리에 쓴다 |
| DB bootstrap | `agent-studio` | Sync hook Job이 공유 PostgreSQL에 `rovr` 역할(비 superuser)과 `rovr` DB를 만든다. 관리자 비밀번호는 `agent-studio` 밖으로 나가지 않는다 |

Sandbox 이미지 버전의 원천은 `app.image.tag` 하나다. 서버가 `<repository>:vX`이면 Sandbox는 `<repository>:sandbox-vX`이다.
Sandbox 설정이나 이미지가 바뀌면 `checksum/sandbox`로 서버가 재시작한다.

rovr의 DB 연결은 replica당 `ROVR_DATABASE_MAX_CONNS`(10)이고, 용량 테스트는 2 replica × 10 = 20을 넣고, rollout 중 두 세대가 겹치는 것을 반영해 2배(40)를 공유 PostgreSQL 예산에 포함한다.

## SSM 파라미터 (`/k8s/eks-demo/rovr/`)

| 파라미터 | 준비 |
| --- | --- |
| `postgres-password`, `database-url` | bootstrap. URL은 비밀번호에서 만든다 |
| `session-secret`, `run-token-secret`, `metrics-bearer-token` | bootstrap |
| `secret-key` | bootstrap. 계정이 rovr Settings에 저장한 provider key를 봉인한다. 바꾸면 저장된 key를 읽을 수 없어 계정이 다시 입력해야 한다 |
| `github-app-id`, `github-app-private-key`, `github-client-id`, `github-client-secret`, `github-webhook-secret` | rovr GitHub App에서 사람이 준비 (rovr `docs/github-app.md`) |
| `anthropic-api-key` | 사람이 준비. 자기 key를 저장하지 않은 계정의 Leader와 Claude Code·OpenCode가 쓰는 platform key다 |
| `openai-api-key` | 선택. 자기 key가 없는 계정의 Codex용 platform key다. 준비한 뒤 `env/eks-demo.yaml`의 `rovr.openai: true`로 켠다 |

```bash
python3 scripts/bootstrap_agent_platform.py --cluster eks-demo          # 누락 경로만 출력
python3 scripts/bootstrap_agent_platform.py --cluster eks-demo --apply  # 누락된 생성형 값만 만든다
```

ExternalSecret은 참조 경로가 하나라도 없으면 Secret을 만들지 않는다. 사람이 준비하는 값을 먼저 넣는다.

## 첫 배포 순서

1. `terraform-env-demo`: `demo/8-agent-studio`(ECR·릴리스 역할), `demo/6-eks-node`(NodePool), `demo/3-alb`(`rovr.dev`)를 apply한다.
2. rovr GitHub App을 만들고 위 SSM 파라미터를 준비한다. bootstrap을 `--apply`로 실행한다.
3. `env/eks-demo.yaml`의 `rovr.github_app_slug`를 App slug로 맞추고 `charts/rovr`를 `main`에 병합한다.
   GitOps dispatch는 `main`의 chart를 갱신하므로 태그보다 먼저 병합한다.
4. `opspresso/rovr`에 `GHP_TOKEN` 시크릿을 등록하고 `v0.1.0` 태그를 push한다. 릴리스가 이미지를 올리고 이 저장소에
   `prod` 배포를 dispatch한다.
5. Argo CD `rovr-eks-demo`가 Synced·Healthy인지, `https://rovr.dev/readyz`가 200인지 확인한다.

3과 4 사이에는 이미지가 없어 `rovr-eks-demo`가 Degraded로 남는다. Secret이 없을 때도 같다. 다른 앱에는 영향이 없다.

Sandbox Pod는 `karpenter.sh/do-not-disrupt`를 달고 NodePool `rovr-sandboxes`의 노드 drain은 Sandbox 마감(90분)까지 기다린다.
노드는 Pod가 시작된 뒤 NetworkPolicy가 적용될 때까지 트래픽을 막으므로(`DefaultDeny`) `rovr-agentd`는 첫 요청에서 최대 90초 기다린다.

## 유지보수 정지

`env/eks-demo.yaml`의 `rovr_maintenance: true`는 서버를 0개로 내린다. 실행 중인 Sandbox Job은 deadline까지 남는다.
