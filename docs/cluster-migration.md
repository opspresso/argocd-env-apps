# 클러스터 이전 (k3s-demo → eks-demo)

운영자가 Agent 플랫폼의 데이터와 도메인을 다른 클러스터로 옮길 때 따르는 절차다.
원본 클러스터는 이전이 끝난 뒤에도 며칠 동안 maintenance 상태로 보존한다. 원본 PVC는 지우지 않는다.

## 이전 대상

| 대상 | 원본 (k3s-demo) | 대상 (eks-demo) | 방법 |
| --- | --- | --- | --- |
| PostgreSQL `agent_platform` | `agent-studio/postgres-0` (pgvector 0.8.6, PG 18) | 같은 chart의 `postgres-0` (gp3) | `pg_dump -Fc` → `pg_restore` |
| Studio 파일 | MinIO `agent-studio-static` | S3 `agent-studio-static` | rclone, 메타데이터 보존 |
| Librio 원본·오디오 | MinIO `agent-librio`의 `organizations/`, `agent-librio/audio/` | S3 `agent-librio-static` | rclone, 메타데이터 보존 |
| 연속성 secret | SSM `/k8s/k3s-demo/...` | SSM `/k8s/eks-demo/...` | `bootstrap_agent_platform.py --copy-from` |

이전하지 않는 것: Prometheus·Loki 데이터, `sample-node` redis, MinIO `agent-memory` 버킷(현재 코드가 사용하지 않음),
`agent-librio`의 `recovery/` 운영자 백업(별도 보관).

DB 행에는 버킷 이름이 없고, 객체 key는 그대로 유지한다. Librio 오디오는 `Metadata.sha256`·`ContentType`을,
Studio 원본은 `x-amz-meta-source-deleted`를 검증하므로 복사 도구는 사용자 메타데이터와 Content-Type을 보존해야 한다.

## 사전 조건

1. `terraform-env-demo`: `4-role` → `8-agent-librio` → `8-agent-broker` → `8-agent-studio` → `5-eks` → `6-eks-node`.
2. `argocd-env-addons/install/eks/install.sh`로 Argo CD를 설치하고 `addons-eks.yaml`, `apps-eks.yaml`을 등록한다.
3. 연속성 secret을 복사한다. 원본 값이 하나라도 없거나 대상에 다른 값이 있으면 쓰기 전에 중단한다.

   ```bash
   python3 scripts/bootstrap_agent_platform.py --cluster eks-demo --copy-from k3s-demo
   python3 scripts/bootstrap_agent_platform.py --cluster eks-demo --copy-from k3s-demo --apply
   ```

4. `env/eks-demo.yaml`의 `agent_*_maintenance`가 모두 `true`인 상태에서 `postgresql-eks-demo`가 Healthy인지 확인한다.
   앱 Pod는 0개다.
5. 모든 서비스 image가 `linux/arm64`를 포함하는지 확인한다(`aws ecr batch-get-image`의 OCI index).

## 전환

예상 중단 시간은 30~45분이다.

1. **원본 중지.** `env/k3s-demo.yaml`의 `agent_{studio,memory,librio,forge,models}_maintenance`를 `true`로 바꿔 push한다.
   web·ticker·worker Pod가 0개가 되었는지 확인한다. PostgreSQL·MinIO는 계속 실행한다.
2. **기준값 기록.** 원본에서 테이블별 행 수와 `agent_studio_schema`·`agent_forge_schema` 버전을 기록한다.
3. **DB 이전.** 원본 Pod에서 `pg_dump -U agent_studio -Fc -d agent_platform`을 만들고, 대상 `postgres-0`에서
   `pg_restore -U agent_studio -d agent_platform --exit-on-error`와 `ANALYZE`를 실행한다.
   bootstrap Job이 만든 빈 `agent_platform`이 대상이다. 실패하면 대상 DB를 다시 만들고 반복한다.
4. **객체 이전.** 원본 클러스터 안에서 일회용 rclone Job으로 MinIO → S3를 `--metadata`로 복사한다.
   AWS credential은 1시간 STS session을 Job의 Secret으로만 전달하고 Job과 함께 삭제한다.
5. **검증.** 대상에서 2의 행 수와 schema 버전이 같아야 한다. S3 객체 수가 원본과 같고, Librio 오디오 표본의
   `head-object`에 `sha256` 메타데이터가 있어야 한다.
6. **대상 시작.** `env/eks-demo.yaml`의 maintenance를 `false`로 바꿔 push한다. Broker는 image가 게시된 뒤 켠다.
7. **DNS 전환 전 확인.** `curl --resolve <host>:443:<ALB IP>`로 Studio 로그인, OIDC discovery, 첨부 다운로드,
   Librio 오디오, Forge checkpoint 복원을 확인한다.
8. **DNS 전환.** `terraform-env-demo`에서 `8-agent-studio`(k3s wildcard를 state에서 제거) → `3-alb`(`*.agrt.dev`를 ALB alias로)
   순서로 적용한다. TTL은 300초다.

## 롤백

- 대상에 쓰기가 생기기 전: DNS를 되돌리고 원본 maintenance를 `false`로 바꾼다.
- 대상에 쓰기가 생긴 뒤: 대상에서 같은 방법으로 원본에 역이전해야 한다. 그래서 원본을 며칠 보존한다.
