# 서비스 용량과 DB 연결 예산

운영 클러스터는 EKS `eks-demo`(Auto Mode, phase `prod`)입니다. 기준 NodePool은 Graviton arm64 `m7g.xlarge` 2대이고,
동적 NodePool `general`과 `workspaces`가 추가 용량을 만듭니다. 실제 사용량은 배포 때 다시 확인합니다.
배포하지 않는 k3s 선언에는 compute requests/limits나 HPA를 두지 않고 PVC 용량과 Forge Pod/Job 수 제한만 유지합니다.

## EKS 워크로드

requests는 `eks-demo`의 유휴 peak working set에 여유를 더한 값입니다. memory limit은 48 동시 요청 부하
시험 peak(Studio 1.1Gi, Memory 530Mi, Models 400Mi, Knowledge 230Mi, Forge 185Mi)보다 크게 둡니다.
Models의 400Mi는 v0.1.10의 요청 signal 누수 수정 전에 측정한 값입니다. 수정 후 같은 부하를 반복했을 때 working set은
54~79Mi에서 늘지 않았고 재시작은 없었습니다. Models limit 1Gi는 이 값보다 크므로 유지합니다.
CPU HPA를 쓰는 Studio·Memory는 70% 목표가 의미 있도록 CPU request를 50m로 유지합니다.

| 워크로드 | requests CPU / memory | memory limit | 확장 |
| --- | --- | --- | --- |
| Studio web | 50m / 256Mi | 2Gi | 2~6, active Agent 실행 8/Pod·app CPU 70% |
| Memory web | 50m / 160Mi | 1Gi | 2~6, app CPU 70% |
| Knowledge web | 25m / 192Mi | 1Gi | 2, app CPU 70% |
| Knowledge queue worker | 25m / 256Mi | 2Gi | 1, web과 분리 |
| Forge controller/gateway/worker | 25m / 256Mi | 1Gi | 2, app CPU 70% |
| Models | 10m / 128Mi | 1Gi | 2, app CPU 70% |
| Delivery | 10m / 128Mi | 512Mi | 2 |
| PostgreSQL | 50m / 512Mi | 2Gi | 단일 StatefulSet, 550 connections |
| Delivery pool PostgreSQL | 25m / 128Mi | 512Mi | 단일 StatefulSet |
| Forge Sandbox | 1 / 2Gi | 2Gi | namespace에서 최대 Pod32·Job32 |

Studio의 active execution metric은 Studio Agent 실행을 나타냅니다. native 모델 요청은
Forge가 별도 집계합니다. Istio sidecar CPU는 앱 CPU에 합산하지 않습니다.
노드 수 계산은 sidecar·DaemonSet·rolling surge와 가장 큰 노드 한 대 손실까지 포함해야 합니다.
낮은 유휴 CPU만으로 production 기준 노드를 줄이지 않습니다. 기준 노드 2대는 전체 requests
(약 1.5 vCPU·9.2Gi)를 노드 한 대(3.77 vCPU·14.3Gi allocatable)가 수용하는 조건으로 정했습니다.
rolling surge와 순간 증가분은 `general` NodePool이 노드를 추가해 처리합니다.

## PostgreSQL

다섯 앱은 같은 `agent_platform` DB를 사용합니다. 풀은 생성 여부보다 실제 동시 접속 가능 상한으로
계산합니다. 각 프로세스의 명시된 풀 크기는 다음과 같습니다.

| 프로세스 | query | queue | readiness | 잠금·보조 | AI admission | 합계 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Studio web | 4 | 0 | 1 | content 4 | 0 | 9 |
| Memory web | 4 | 0 | 1 | background 2 + OIDC 2 | 0 | 9 |
| Knowledge web | 4 | 2 | 1 | maintenance 2 + OIDC 2 | 9 | 20 |
| Knowledge worker | 4 | 2 | 0 | maintenance 2 + OIDC 2 | 9 | 19 |
| Forge | 12 | 0 | 0 | OIDC 2 | 0 | 14 |
| Models (k3s / EKS) | 4 / 8 | 0 | 1 | OIDC 4 + lease 1 | 0 | 10 / 14 |

Studio schedule ticker는 HTTP만 호출하고 DB에 연결하지 않습니다. Memory의 dreaming·embedding index는 실행 중에만 전용 session
연결을 하나씩 사용합니다. Knowledge maintenance lock은 query pool 밖의 전용 pool(최대 2)을 쓰며 worker도
같은 pool을 만듭니다. 1시간 주기 platform retention은 worker에서만 query pool로 실행합니다.
worker health는 process-only입니다. Forge pool 12와 OIDC 잠금 pool은 코드에 고정돼 있고
readiness는 query pool을 사용합니다. Models readiness는 전용 연결 1개를 사용하므로 query pool이 포화돼도
probe가 연결을 기다리지 않습니다. lease 갱신도 전용 연결 1개를 사용합니다.

Models의 DB 작업은 사용량 예약·정산과 세션의 짧은 transaction이며 공급자 호출 동안 연결을 잡지 않습니다.
그래서 `DATABASE_POOL_SIZE`를 기본 16 대신 k3s 4, EKS 8로 주입합니다. 연결 대기는 `connectionTimeoutMillis`
5초 뒤 실패하므로 pool을 줄인 뒤 연결 대기 시간과 오류를 관찰합니다.

Knowledge AI admission pool9는 요청용8+정리/정책제어1 연결입니다. 전체8·batch4 제한은
공유 PostgreSQL에서 합산되며 interactive 용량을 남깁니다. pool을5로 줄이면 같은 프로세스의
batch4가 local admission 슬롯을 모두 점유할 수 있으므로 총량·batch 정책과 함께 조정해야 합니다.

| 환경 | 정상 최대 세대 | 2세대 rollout | 운영 여유 최소 | PostgreSQL 한도 |
| --- | ---: | ---: | ---: | ---: |
| k3s 선언(미배포) | `Studio9+Memory9+Knowledge(20+19)+Forge14+Models10 = 81` | 162 | 20 | 250 |
| EKS `eks-demo` | `Studio6×9+Memory6×9+Knowledge(2×20+19)+Forge2×14+Models2×14 = 223` | 446 | 20 | 550 |

웹 Deployment는 `maxSurge: 100%`, `maxUnavailable: 0`이고 worker의 surge는 replica 수 이하이므로 rollout 중
프로세스 수는 정상 세대의 2배를 넘지 않습니다. 테스트가 이 조건도 검사합니다.

`test_production_capacity.py`는 실제 렌더의 HPA·worker replica·pool 값으로 이 상한을 계산합니다.
새 프로세스, HPA 상한, pool 크기를 바꾸면 함께 갱신합니다. 이전 rollout이 끝나기 전에 다음
rollout을 누적하지 않습니다. PostgreSQL `max_connections`·공유 메모리 설정 변경은 재시작이 필요합니다.
현재 shared buffers는256MB, work_mem은4MB입니다. `max_connections`가 모든 query의 메모리
상한을 보장하지는 않으므로 실제 쿼리 메모리·RSS·연결 대기 시간을 관찰합니다.

## 실행 용량과 보존

`workspace_max_pods`는 Forge 실행 namespace의 Pod/Job 상한입니다. 실행 Pod는 일시적인 디스크를 사용하고 마지막 확정 checkpoint로 복원합니다.
EKS Pod당 4608Mi ephemeral storage 예약에 더해 이미지·호스트 로그 디스크가 필요합니다.
Workspace NodePool(`workspaces`)은 taint `agent-forge/workspace`와 앱의 toleration을 맞춰야 합니다.

기존 Studio DinD PVC는 보존하지만 daemon은 시작하지 않습니다. 기존 PostgreSQL·object storage·Neo4j
데이터를 새 서비스 namespace로 자동 복사하지 않습니다. PostgreSQL은 단일 StatefulSet이며
이 배포가 failover나 backup을 제공하지는 않습니다. 백업 복원과 실제 CNI 격리는 별도 검증합니다.
