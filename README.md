# argocd-env-apps

운영 클러스터 EKS `eks-demo`의 자원·확장 상한은 [용량 운영](docs/production-capacity.md)을 따른다.

Agent Studio·Models·Memory·Knowledge·Forge·Delivery의 EKS 구성, 저장소별 책임, Secret 준비와 배포 순서는
[Agent Platform 배포](docs/agent-platform.md)를 따른다. 클러스터 사이의 데이터·도메인 이전은 [클러스터 이전](docs/cluster-migration.md)을 따른다.

## 저장소 구조

```text
apps-*.yaml       # App of Apps 설치 진입점
apps/             # 플랫폼별 Argo CD Application/ApplicationSet
charts/           # Helm chart와 배포 버전
env/              # 클러스터별 설정
scripts/          # 빌드·GitOps·검증·운영 스크립트
requirements/     # Python 실행(runtime.txt)·테스트(dev.txt) 의존성
tests/            # 자동화 테스트
docs/             # 운영 문서
```

아래 명령은 저장소 루트 기준이다. 스크립트는 다른 디렉터리에서도 경로를 지정해 실행할 수 있다.

## apps

> apps 를 등록 합니다.

```bash
kubectl apply -n argocd -f https://raw.githubusercontent.com/opspresso/argocd-env-apps/main/apps-eks.yaml
```

운영 클러스터 `eks-demo`의 Argo CD는 `apps-eks.yaml`로 `apps/eks/`의 ApplicationSet을 등록한다.
각 ApplicationSet은 `generators.git.files`에 나열한 env만 읽는다.
k3s 선언(`apps-k3s.yaml`, `apps/k3s/`, `env/k3s-demo.yaml`, `charts/*/k3s/`)은 저장소에 남아 있지만 어느 클러스터에도 배포하지 않는다.

replica 수, autoscaling 여부와 resource 사용 여부는 `env/<cluster>.yaml`이 원천이다.
공통 `values.yaml`은 EKS를 기본으로 하며 EKS는 기존 env 설정을 적용한다.
k3s 선언과 local fixture는 `replicas: 1`, `autoscaling: false`, `resources: false`로 선언한다.
metrics backend는 `metrics.backend`로 선택하며, EKS는 `prometheus`, k3s 선언은
`grafana-cloud`를 사용한다.
`grafana-cloud`에서는 Alloy가 `/api/metrics`(Forge는 `/metrics`)를 직접 수집하므로 ServiceMonitor CRD와 로컬 metrics DB가
필요하지 않다. `prometheus` backend만 ServiceMonitor를 생성한다.
각 chart의 기본 values가 운영용 requests/limits를 제공하며, `resources: false`인
환경에서는 템플릿이 해당 resource block을 제거한다. 컨테이너·초기화 컨테이너·worker·CronJob에도 같은 규칙을 적용한다.
Neo4j는 공식 차트가 강제하는 최소 requests(CPU `500m`, 메모리 `2Gi`)만 허용하며 limits는 두지 않는다.
HPA·VPA·KEDA autoscaler를 생성하지 않으며 PVC storage 요청은 유지한다.
`scripts/validate.py`는 위 Neo4j 최소 requests를 제외한 compute requests/limits나 autoscaler가 남으면 실패한다.

## 로컬 Kubernetes 개발 환경

`apps-local.yaml`, `apps/local/`, `env/local-demo.yaml`은
제공하지 않는다. 템플릿의 `local` 분기는 `tests/fixtures/local-env.yaml`을 사용해
MCP 인증 설정과 PostgreSQL 초기화 동작을 검증한다. 이 fixture는 배포 대상이 아니다.

## charts

차트 구성은 `opspresso/helm-charts`의 `app` → `cronjob` → `raw` 순서로
선택한다. PostgreSQL·MinIO·Neo4j 등 데이터 서비스는 공식 또는 주요 배포처의 전용 차트를
사용한다. 공통 차트에 필요한 기능이 없으면 공통 차트를 확장하며, wrapper에는 `templates/`를 두지 않는다.

Studio의 scan·plugin sync·reindex ticker와 Knowledge의 queue worker는
`app.workloads`를 사용한다. Forge가 별도 실행 namespace의 Sandbox를 소유하며 RBAC·네트워크
정책·보존용 PVC는 `raw`가 렌더한다. Studio에는 Docker daemon이나 Sandbox 실행 권한이 없다.
`app.image`를 단일 버전 원천으로 유지하여 각 서비스 worker가 같은 release image를 사용한다.
Forge Sandbox는 controller의 full v-tag에 `sandbox-` 접두어를 붙인다.
Memory에는 queue worker가 없으며 문서·전사 처리는 Knowledge가 담당한다.

Agent 서비스의 공통 인증 설정·기업 프로필 스키마를 기존 데이터와 함께 전환하려면
[기업 프로필 전환 절차](docs/agent-identity-upgrade.md)를 따른다.

PostgreSQL은 Bitnami 전용 차트를 사용하며, `pgvector/pgvector` 이미지의 entrypoint·인증 환경변수·
probe·데이터 경로를 values로 지정한다. `postgres` StatefulSet과 `data-postgres-0` PVC를 유지한다.
MinIO는 Bitnami, Neo4j는 공식 Neo4j 차트를 사용하고, ExternalSecret과 PostgreSQL 초기화 Job은 `raw`로 관리한다.

공통 차트 의존성 버전을 올릴 때는
`helm-charts`의 게시를 확인하고, 원격 의존성으로 검증한 뒤 이 저장소에 반영한다.

```
charts/<project>/
  Chart.yaml
  values.yaml                  # 공통 값
  values-<phase>.yaml          # phase 별 값. 배포시 gitops 가 갱신
  versions-<phase>.json        # phase 별 배포 이력
  values-template.yaml.j2      # jinja2 템플릿 (phase 아님)
  <platform>/values-<cluster>.yaml # scripts/build.sh 가 해당 플랫폼 env로 렌더한 결과
```

`phase` 는 `values-<phase>.yaml` 파일에서 찾는다. `values-template.yaml.j2` 는 렌더 소스이므로
phase 로 취급하지 않는다.

`<platform>/values-<cluster>.yaml` 은 **언제나** `values-template.yaml.j2` 의 렌더 결과다.
직접 고치지 말고 템플릿을 고친 뒤 `./scripts/build.sh` 를 돌린다.
덮어쓸 값이 없는 chart 도 템플릿을 둔다 — ApplicationSet 의 `valueFiles` 에 적힌 파일이 없으면
sync 에 실패하기 때문이다.

ApplicationSet 은 `env/*.yaml` 의 `phase` 필드로 어떤 `values-<phase>.yaml` 을 읽을지 정한다.
`eks-demo`는 `phase: prod`, 배포하지 않는 `k3s-demo` 선언은 `phase: alpha`를 사용한다.
앱과 MCP의 두 phase values를 별도로 관리한다.

클러스터별 SSM 경로는 `values-template.yaml.j2`가 `env/<cluster>.yaml`의 `cluster`에서
생성한다. 공통 `values.yaml`에는 특정 클러스터의 `global.ssmPrefix`를 두지 않는다.
PostgreSQL·MinIO·Neo4j는 클러스터 values가 없으면 렌더링에 실패하며, `scripts/validate.py`는
최종 `global.ssmPrefix`가 대상 클러스터와 다르면 실패한다. EKS 공통 기본값과 클러스터의
자격 증명 경로는 별개다.

## gitops

* <https://github.com/argoproj/argo-cd>

`repository_dispatch` → [`.github/workflows/gitops.yml`](.github/workflows/gitops.yml) →
`scripts/gitops.sh` → `scripts/gitops.py`

| client_payload | 설명 |
|---|---|
| `username` | 이미지 owner |
| `project` | 차트 이름 (`charts/<project>`) |
| `version` | 배포할 버전 |
| `container` | 갱신할 values 최상위 mapping. 기본 `app`; 없거나 버전 필드가 없으면 실패 |
| `action` | 비움 또는 `approved`. 승인 이력 기록용이며 머지 방식과는 독립적 |
| `phase` | 대상 phase. **비우면 차트의 모든 phase 로 fan-out** |
| `type` | `helm` |
| `auto_merge` | JSON boolean. 기본 `false`; `true`면 prod도 `main`에 즉시 반영 |

`alpha` 등 prod 이외의 phase는 `main`에 바로 푸시한다. `prod`는 기본적으로
브랜치를 만들어 PR을 올리며, `auto_merge: true`를 지정하면 PR 생성 없이
`main`에 바로 푸시한다. GitHub의 브랜치 보호 규칙은 그대로 적용된다.
`phase`를 비워 fan-out 할 때도 `auto_merge`가 각 이벤트에 전달되므로 prod에 적용된다.
GitOps와 생성 파일 게시 workflow는 `gitops` concurrency 그룹을 공유한다.
`queue: max`로 최대 100개의 대기 실행을 보존한다.

예를 들어 prod를 즉시 반영하는 payload는 다음과 같다.

```json
{
  "event_type": "gitops",
  "client_payload": {
    "project": "sample-node",
    "version": "v1.2.3",
    "phase": "prod",
    "auto_merge": true
  }
}
```

```bash
PAYLOAD="{\"event_type\":\"gitops\","
PAYLOAD="${PAYLOAD}\"client_payload\":{"
PAYLOAD="${PAYLOAD}\"username\":\"${TG_USERNAME}\","
PAYLOAD="${PAYLOAD}\"project\":\"${TG_PROJECT}\","
PAYLOAD="${PAYLOAD}\"version\":\"${TG_VERSION}\","
PAYLOAD="${PAYLOAD}\"phase\":\"${TG_PHASE}\","
PAYLOAD="${PAYLOAD}\"auto_merge\":${TG_AUTO_MERGE:-false},"
PAYLOAD="${PAYLOAD}\"type\":\"helm\""
PAYLOAD="${PAYLOAD}}}"

curl -sL -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer ${GITHUB_TOKEN}" \
  -d "${PAYLOAD}" \
  https://api.github.com/repos/opspresso/argocd-env-apps/dispatches
```

### 로컬 실행

`--dry-run` 은 파일만 갱신하고 git·GitHub 은 건드리지 않는다.
실제 배포는 미커밋 변경·미추적 파일·미푸시 커밋이 없는 `main` checkout에서 실행한다.
배포 커밋에는 대상 `values-<phase>.yaml`과 `versions-<phase>.json`만 포함한다.

```bash
export TG_PROJECT="sample-grpc"
export TG_VERSION="v0.0.0"
export TG_PHASE="alpha"

python3 scripts/gitops.py deploy --dry-run
```

prod 즉시 반영은 `TG_AUTO_MERGE=true` 또는 `--auto-merge`로 선택한다.
환경변수는 `true`·`false`만 허용하며(대소문자 무관), 비우면 `false`다.
`--auto-merge`는 환경변수의 `false`보다 우선한다.

```bash
TG_PROJECT="sample-node" TG_VERSION="v1.2.3" TG_PHASE="prod" \
  python3 scripts/gitops.py deploy --auto-merge --dry-run
```

실제로 반영하려면 `--dry-run`을 제외한다. 같은 버전의 prod 브랜치가 이미 있으면
기존 PR은 중복 생성하거나 다시 열지 않고, 브랜치만 있고 PR 생성이 실패했던 경우는
PR 생성을 재개한다. `auto_merge`는 기존 PR 유무와 관계없이 `main`을 갱신하며,
기존 PR을 닫거나 머지하지는 않는다.

`TG_PHASE` 를 비우면 phase 목록을 찾아 fan-out 한다.

```bash
TG_PROJECT="sample-grpc" TG_VERSION="v0.0.0" python3 scripts/gitops.py dispatch --dry-run
```

## build

`values-template.yaml.j2` 를 플랫폼에 맞는 `env/*.yaml` 마다 렌더해
`charts/<project>/<platform>/` 에 저장한다.
템플릿 변수 누락, 지원하지 않는 `env`, 파일명과 다른 `cluster`, 잘못된 boolean은 실패한다.
차트별로 모든 대상 환경을 렌더·검사한 후 파일을 쓴다.

```bash
./scripts/build.sh
```

기본은 파일 생성만 수행한다. CI의 `GITHUB_PUSH=true` 모드는 깨끗한 `main`에서
원격 변경을 먼저 반영하고, 렌더 → Helm 검증 → pytest가 모두 성공한 뒤
생성된 플랫폼별 values만 커밋·push한다. 실행·테스트 의존성 모두 필요하다.

## validate

ApplicationSet 이 지정한 것과 같은 valueFiles 조합으로 `helm template` 을 돌린다.
values 파일 누락이나 chart 오류를 Argo CD sync 가 아니라 CI 에서 잡기 위한 것이다.
대상 디렉터리나 Application이 없으면 실패하며, 의존성 다운로드 실패 후 남아 있는
기존 차트로 검증을 계속하지 않는다.

```bash
./scripts/validate.py

# 배포하지 않는 차트·환경까지 포함한 전체 검사 (CI 기본)
./scripts/validate.py --all-charts

./scripts/validate.py -r sample-node
```

## 서비스 실행 경계

Studio는 web과 ticker만 실행하고 MCP로 Knowledge·Forge를 호출한다. Knowledge는 별도 queue
worker 하나로 문서·오디오를 처리한다. Forge는 HTTP·MCP·Gateway·worker를 한 서비스로 실행한다.
Sandbox는 `agent-forge-workspaces`의 controller-owned Pod와 제한된 emptyDir를 사용한다.
Models는 공급자 호출과 사용량을 소유하며 네 서비스는 Studio OIDC와 내부 `MODELS_URL`로 연결한다.
실행·네트워크·자원·기존 PVC 보존은 [Kubernetes Workspace 운영](docs/workspace-kubernetes.md)을 따른다.

## test

PR에서는 `.github/workflows/validate.yml`이 렌더·Helm 검증·pytest를 실행한다.
main push에서도 같은 검사를 수행하며, 검사 실패 시 생성 파일을 게시하지 않는다.

```bash
pip install -r requirements/runtime.txt -r requirements/dev.txt
pytest
```
