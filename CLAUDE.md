# CLAUDE.md

애플리케이션을 Argo CD 로 배포하는 GitOps 저장소.

> 클러스터 addon 배포는 `argocd-env-addons` 저장소가 담당한다.
> 두 저장소는 디렉토리 모양이 닮았지만 **chart 규칙이 다르다** — 마지막 절 참고.

## 저장소 구조

```
apps-eks.yaml                       # EKS용 App of Apps
apps-k3s.yaml                       # k3s용 App of Apps
apps/{eks,k3s}/                     # 플랫폼별 Application/ApplicationSet
charts/<project>/                  # wrapper Helm chart
env/<cluster>.yaml                 # 클러스터별 변수. git files generator·Jinja2 입력
scripts/gen_values.py              # 템플릿 × env → 플랫폼별 chart values
scripts/build.sh                   # 전체 chart 렌더. CI에서 결과를 자동 커밋
scripts/gitops.py                  # dispatch로 들어온 버전을 phase values에 기록
scripts/gitops.sh                  # GitOps 실행 래퍼
scripts/chart.py                   # phase 탐색, values/versions 갱신
scripts/validate.py                # ApplicationSet과 같은 조합으로 Helm 검증
scripts/workload_policy.py         # 플랫폼별 workload 정책 검사
scripts/bootstrap_agent_platform.py # 플랫폼 Secret 준비
requirements/{runtime,dev}.txt     # Python 실행·테스트 의존성
tests/                            # pytest
docs/                             # 운영 문서
```

## charts 규칙

```
charts/<project>/
  Chart.yaml                    # wrapper chart. upstream chart 를 dependency 로 고정
  values.yaml                   # 모든 phase·클러스터 공통 값
  values-<phase>.yaml           # phase 별 값. 배포 시 gitops 가 버전을 갱신
  versions-<phase>.json         # phase 별 배포 이력
  values-template.yaml.j2       # Jinja2 템플릿 (렌더 소스, phase 아님)
  <platform>/values-<cluster>.yaml # scripts/build.sh 가 플랫폼별 env/*.yaml 로 렌더한 결과
```

### 파일별 편집 규칙

| 파일 | 직접 수정 |
|---|---|
| `Chart.yaml` | O |
| `values.yaml` | O |
| `values-<phase>.yaml` | O — 단, 버전 관련 키는 gitops 소유(아래) |
| `versions-<phase>.json` | **X** — gitops 산출물 |
| `values-template.yaml.j2` | O |
| `<platform>/values-<cluster>.yaml` | **X** — 언제나 생성물 |

**`<platform>/values-<cluster>.yaml` 은 예외 없이 `values-template.yaml.j2` 의 렌더 결과다.**
클러스터별 값을 바꾸려면 템플릿을 고치고 `./scripts/build.sh` 를 돌린다. 렌더 결과를 직접 고치면
다음 build 에서 덮어써진다. `scripts/validate.py` 가 이 규칙을 검사하므로, 템플릿 없이 env 디렉토리만
있는 chart 는 CI 에서 실패한다.

클러스터별로 덮어쓸 값이 없는 chart 도 템플릿을 둔다. ApplicationSet 의 `valueFiles` 에 적힌
파일이 없으면 Argo CD 가 sync 에 실패하기 때문이다.

`values-<phase>.yaml` 은 배포마다 `yaml.safe_dump` 로 통째로 재작성된다 (`scripts/chart.py:135`).
**주석은 사라지고 키는 알파벳 순으로 재정렬된다** — 설명이 필요하면 `values.yaml` 이나
템플릿에 적는다.

이 파일에서 `scripts/gitops.py` 가 소유하는 키는 손대지 않는다 — `container` 키(기본 `app`) 아래의
`image.tag`, `configmap.data.VERSION`, `secret.data.SECRET_VERSION`,
`env[]` 의 `VERSION`·`ENV_HASH`.

`ENV_HASH` 는 **`values-<phase>.yaml` 만** 해시한 값이다 (`scripts/chart.py:157`). `values.yaml` 이나
`<platform>/values-<cluster>.yaml` 이 바뀌어도 움직이지 않는다. 다만 pod 재시작은 이것에 의존하지
않는다 — upstream `app` chart 가 `checksum/config`·`checksum/secret` 을 병합된 값 전체에서
계산해 deployment 에 달아준다. `ENV_HASH` 는 앱이 자기 버전을 env 로 읽기 위한 값에 가깝다.

`ENV_HASH` 는 배포할 때만 다시 계산된다. 그래서 `values-<phase>.yaml` 을 손으로 고치면 다음 배포
전까지 옛 해시가 남고, **같은 버전을 다시 배포해도 `ENV_HASH` 한 줄은 바뀐다.** 재배포 diff 에
이 줄만 있으면 정상이다.

### phase

- phase 는 `values-<phase>.yaml` 파일 존재로 결정된다 (`chart.discover_phases`).
- 템플릿이 `values-template.yaml.j2` 인 이유가 이것이다 — `.yaml` 이면 phase 패턴에 걸린다.
  `template` 은 `chart.RESERVED_PHASES` 에 남겨 둔 안전장치다. 옛 이름을 쓰는 chart 가 남아도
  phase 로 잡히지 않는다. 이 예외가 없던 시절 `versions-template.json` 이 생겼던 적이 있다.
- 새 phase 를 추가하려면 `values-<phase>.yaml` 을 만들면 된다. `versions-<phase>.json` 은
  첫 배포 때 자동 생성된다.
- `prod`는 기본적으로 브랜치를 만들어 PR을 올린다. `auto_merge: true` (`TG_AUTO_MERGE=true`
  또는 `--auto-merge`)면 prod도 `main`에 바로 push한다. 나머지 phase는 항상 직접 push한다.
- `eks-demo`는 `phase: prod`, `k3s-demo`는 `phase: alpha`를 사용한다. 운영 클러스터 `eks-demo`는 `values-prod.yaml`을 읽고,
  `values-alpha.yaml`을 읽는 배포 클러스터는 없다.
  EKS ApplicationSet이 읽는 앱과 MCP chart는 `values-prod.yaml`을 제공해야 한다.
  추가 EKS env의 phase는 각 `env/<cluster>.yaml`에서 선택한다.

### Chart.yaml

- 자체 SemVer 를 쓴다 (`v1.3.0`). upstream chart 버전을 그대로 쓰는 addons 저장소와 다르다.
- 애플리케이션 chart 는 `opspresso/helm-charts` 의 `app` chart 를 dependency 로 쓴다.
  `app` chart 버전을 올릴 때 wrapper `version` 도 함께 올린다.
- wrapper에는 `templates/`를 두지 않는다. `app` → `cronjob` → incubator `raw` 순서로 선택하며,
  필요한 공통 기능은 `helm-charts`에서 확장한다. PostgreSQL·MinIO는 공식 또는 주요
  배포처의 전용 차트를 사용한다. Studio ticker·Librio worker는 `app.workloads`, 추가 리소스는 `app.raw`로 관리한다.
- 한 chart 를 두 번 쓰거나 이름을 바꿔 붙일 때는 `alias` 로 values 키를 정한다
  (예: postgresql 의 `raw` → `bootstrap`).

### values 병합 순서

ApplicationSet 의 `helm.valueFiles` 순서 그대로다. 뒤가 앞을 덮는다.

1. `values.yaml`
2. `values-<phase>.yaml`
3. `<platform>/values-<cluster>.yaml`

`phase` 는 `env/<cluster>.yaml` 의 `phase` 필드에서 온다. 즉 **클러스터가 phase 를 고른다.**

## env/<cluster>.yaml

- 파일 이름이 클러스터 이름이고, 안의 `cluster` 필드와 일치해야 한다 (`{{cluster}}` 로 치환됨).
- 클러스터별 SSM 경로는 템플릿의 `{{cluster}}`에서 만든다. 공통 `values.yaml`에 특정 클러스터의 경로를 기본값으로 두지 않는다.
- `env` 는 플랫폼(`eks`, `k3s`, `local`)이며 렌더 출력 디렉터리이자 valueFiles 경로가 된다 (`{{env}}/values-{{cluster}}.yaml`).
  `scripts/gen_values.py`는 필수 템플릿 변수 누락, 지원하지 않는 env, cluster와 파일명 불일치를 거부한다.
  `resources`·`autoscaling`은 YAML boolean이어야 한다.
- `replicas` 와 `autoscaling` 은 애플리케이션 템플릿의 `app.replicaCount` 및
  `app.autoscaling.enabled`의 원천이다. k3s·local은 각각 `1`, `false`를 사용한다.
- `agent_studio_maintenance`는 Agent Studio만 위한 boolean이다. `true`이면 해당 클러스터의
  앱과 ticker를 0개로 내리고 HPA를 끈다.
  다른 앱과 PostgreSQL은 건드리지 않는다. 기본값은 `false`다.
  Memory·Librio·Forge·Models도 각각 `agent_<service>_maintenance`로 자기 프로세스만 정지한다.
- `storage_gc_maintenance`는 k3s 선언에서 네 Agent maintenance가 모두 `true`이고 `minio.console: false`일 때만 허용한다.
  MinIO 서버·PVC는 유지하고 S3 ingress를 닫는다. [중지·재개 절차](docs/storage-gc-maintenance.md)를 따른다.
- `metrics.backend`는 수집 방식을 정한다. EKS는 `prometheus`, k3s 선언은 Alloy가 직접 수집하는
  `grafana-cloud`를 사용한다. ServiceMonitor는 `prometheus`에서만 생성한다.
- `resources` 는 workload의 requests/limits 사용 여부를 제어하는 env 원천값이다.
  `true`면 chart 기본 requests/limits를 유지하고, `false`면 템플릿이 resource block을
  제거한다. 공통 chart는 EKS 기본값이며 k3s·local은 반드시 `resources: false`를 사용한다.
  worker·초기화 컨테이너·CronJob에도 requests/limits를 두지 않고 HPA·VPA·KEDA를 생성하지 않는다.
  PVC의 storage 요청은 유지하고 `scripts/validate.py`의 렌더 결과 검사로 이 규칙을 검증한다.
- `phase` 는 그 클러스터가 읽을 `values-<phase>.yaml` 을 정한다.
- env 파일을 추가하면 `scripts/build.sh` 가 모든 chart 에 대해 렌더 결과를 새로 만든다.

## apps/{eks,k3s,local}/<project>.yaml

- EKS는 `kind: ApplicationSet` + git files generator로 `env/*.yaml`을 읽는다. k3s 선언은 단일 클러스터의 `Application`을 사용한다.
- 운영 클러스터는 EKS `eks-demo`(phase `prod`)이며 같은 클러스터의 Argo CD가 `apps-eks.yaml`을 동기화한다.
  `eks-demo-a`·`eks-demo-b`는 향후 서비스 클러스터 선언이다. k3s 선언(`apps-k3s.yaml`, `apps/k3s/`, `env/k3s-demo.yaml`, `charts/*/k3s/`)은 저장소에 남아 있지만 어느 클러스터에도 배포하지 않는다.
  배포하지 않는 선언의 렌더 검증을 실제 배포로 보고하지 않는다. k3s 선언을 삭제하지 않는다.
- 배포할 클러스터는 `generators.git.files` 의 주석을 풀어 고른다.
- Application 이름은 `<project>-{{cluster}}`.
- label `opspresso.com/group: apps`, `opspresso.com/cluster: {{cluster}}` 를 유지한다.
- `syncPolicy.automated` 는 chart 마다 다르다. 켜져 있는 것을 임의로 끄거나 반대로 켜지 않는다.
- `apps/` 에 ApplicationSet 이 없는 chart 는 배포되지 않는다 (`charts/sample-spring`).

## gitops

`repository_dispatch` → `.github/workflows/gitops.yml` → `scripts/gitops.sh` → `scripts/gitops.py`.
payload 필드와 curl 예시는 [README](README.md#gitops) 참고.

- `TG_PHASE` 가 있으면 그 phase 를 배포(`deploy`), 없으면 chart 의 모든 phase 로 fan-out(`dispatch`).
- `TG_AUTO_MERGE`는 비움·`false`가 기본, `true`면 prod도 직접 push한다. fan-out에서도 전달된다.
- 배포는 `values-<phase>.yaml` + `versions-<phase>.json` 을 갱신하고 `nalbam-bot` 으로 커밋한다.
- 대상 container가 없거나 버전 필드가 없으면 이력을 쓰기 전에 실패한다.
- 실제 배포는 깨끗한 `main` checkout과 미푸시 커밋이 없는 상태를 요구한다.
- 같은 버전을 다시 배포하면 파일이 바뀌지 않아 커밋 없이 끝난다 (idempotent).
  같은 버전의 `approved` 재전송도 기존 승인 시각을 유지한다.
- prod 브랜치만 있고 PR이 없으면 PR 생성을 재개한다. 기존 PR은 중복 생성하거나 다시 열지 않는다.
- GitOps와 빌드 게시 workflow는 `gitops` concurrency 그룹으로 직렬화된다.
  `queue: max`로 대기 중인 fan-out 이벤트도 최대 100개까지 보존한다.
- 로컬 확인은 `--dry-run` 으로 한다. 파일만 갱신하고 git·GitHub 은 건드리지 않는다.

```bash
TG_PROJECT="sample-grpc" TG_VERSION="v0.0.0" TG_PHASE="alpha" python3 scripts/gitops.py deploy --dry-run
```

## 재생성 · 검증

EKS 컨테이너·init·테스트 작업은 양수 CPU/memory requests와 memory limit이 필요하다.
CPU limit은 필수가 아니다. 실제 Helm 렌더의 자원 정책과 최대 replica의 DB 연결 예산을 검증한다.

```bash
./scripts/build.sh                      # 전체 chart 렌더
./scripts/gen_values.py -r sample-node   # 한 chart 렌더
./scripts/validate.py --all-charts      # 미배포 차트·환경까지 전체 검증
./scripts/validate.py -r sample-node    # 한 chart만
pytest                                  # scripts/와 배포 구성 테스트
```

`.github/workflows/push.yml`은 main push에서 렌더 → Helm 검증 → pytest를 실행한 뒤 생성 파일을 게시한다. Argo CD가 동기화하기 전의 유일한 자동 검사다.
GitOps 배포 커밋처럼 `values-alpha.yaml`·`values-prod.yaml`·`versions-*.json`만 바뀐 push는 렌더 결과가 같으므로 build를 시작하지 않는다. 이 파일을 손으로 고쳤다면 병합 전에 로컬 `./scripts/build.sh`와 `scripts/validate.py`를 실행한다.
`.github/workflows/validate.yml`은 같은 검사를 수동으로만 실행한다. Pull request는 CI를 시작하지 않는다.
`--all-charts`는 `apps/`에 없는 chart(`sample-spring`, `sample-grpc`)와 미사용 클러스터 values도 검사한다.
CI와 생성 파일 게시 workflow는 이 전체 검사를 사용한다. wrapper의 자체 템플릿은 검사에서 거부한다.

`scripts/validate.py` 는 `helm dependency update` 로 upstream chart 를 내려받는다.
결과물(`charts/*/charts/`, `charts/*/Chart.lock`)은 `.gitignore` 처리되어 있다.

`scripts/build.sh`는 기본적으로 파일만 렌더한다. `GITHUB_PUSH=true`는 깨끗한 main에서
원격 변경을 먼저 반영하고, Helm 검증과 pytest 통과 후 플랫폼별 생성 values만 커밋한다.
`requirements/runtime.txt`와 `requirements/dev.txt`가 모두 필요하다.
`scripts/gitops.py` / `scripts/chart.py`를 고치면 `tests/`도 함께 본다.

local 배포 manifest와 env는 제공하지 않는다. 남아 있는 local 템플릿 분기는
`tests/fixtures/local-env.yaml`로 검증하며 fixture를 실제 배포 env로 취급하지 않는다.

## argocd-env-addons 와 다른 점

같은 이름의 파일이 다른 의미를 가지므로 두 저장소를 오갈 때 주의한다.

| | argocd-env-apps (여기) | argocd-env-addons |
|---|---|---|
| 배포 단위 | application | addon |
| 템플릿 파일 | `values-template.yaml.j2` | 같음 |
| phase | `values-<phase>.yaml`, `versions-<phase>.json` | 없음 |
| `env/*.yaml` | `phase` 필드 있음 | `phase` 필드 없음 |
| valueFiles | `values.yaml` → `values-<phase>.yaml` → `<platform>/values-<cluster>.yaml` | `values-<phase>.yaml` 단계가 없음 |
| 렌더 출력 경로 | `charts/<project>/<platform>/` | 같음 |
| chart 버전 | 자체 SemVer | upstream chart 버전을 그대로 |
| 버전 배포 | `repository_dispatch` → `scripts/gitops.py` | 없음 (수동 chart 버전 변경) |

## 관련 저장소

- `argocd-env-addons` — 클러스터 addon 배포. Argo CD·Istio·external-secrets 등 이 저장소의 전제.
- `terraform-env-demo` — EKS·VPC·ALB·IAM Role.
- `opspresso/helm-charts` — dependency 로 쓰는 `app`, `cronjob` chart 의 저장소.
