# Agent 기업 프로필 전환

운영자가 기존 계정·세션·콘텐츠를 보존하면서 공통 인증 설정과 기업 프로필 스키마를 적용하는 절차다.
이 절차는 다음 이전 스키마만 지원한다. DB reset이나 PVC 교체를 사용하지 않는다.

| 서비스 | 이미지 | 이전 스키마 | 변환 |
| --- | --- | --- | --- |
| Studio | `v0.166.0` | version 11 | `node build/upgrade-enterprise-profile.cjs --apply` |
| Memory | `v0.58.0` | fingerprint `6e8f4473aac6760b7bae77750f952d05a4bf2241c1b15908caa6d71b2f1d74dc` | `node scripts/upgrade-enterprise-profile.mjs --apply` |
| Librio | `v0.19.0` | fingerprint `f3a3fc210e67c9b987a5999a28d79a1fb305c902249e230a81ac535f9a3b3432` | `node scripts/upgrade-enterprise-profile.mjs --apply` |
| Forge | `v0.10.0` | version 7 | 시작 시 기존 행을 보존하고 version 8로 변환 |

## 준비

1. 서비스별 Verify와 release workflow의 성공, 실제 image tag와 revision을 확인한다.
   설정 이름과 이미지가 따로 반영되지 않도록 조정된 배포에서는 원본 저장소의
   `GITOPS_AUTO_DEPLOY=false`로 이미지 발행과 GitOps 반영을 분리한다. 기존 변수 상태를 기록한다.
2. 실제 대상은 각 Argo Application의 namespace와 DB 연결 참조로 확인한다.
   현재 k3s alpha가 운영 대상이며 EKS 값은 미래 환경 선언이다.
3. 활성 실행·업로드·queue·Sandbox 작업과 현재 스키마를 읽기 전용으로 확인한다.
   현재 스키마가 표와 다르면 실행하지 않고 해당 버전의 전환 절차를 확인한다.
4. 기존 인증 secret, 암호화 key, issuer, provider ID를 보존한다. 공통 변수 이름은
   [설정 규칙](https://github.com/opspresso/agent-platform/blob/main/docs/CONVENTIONS.md)을 따른다.
   기존 Secret의 SSM 경로와 값은 변경하지 않는다.

## 중지와 변환

1. `env/k3s-demo.yaml`의 네 `agent_<service>_maintenance` 값을 `true`로 설정한다.
   `scripts/gitops.py deploy --dry-run`으로 alpha·prod의 새 이미지 버전 값을 생성하고,
   `scripts/build.sh`, 전체 Helm 검증과 pytest를 실행한다. 검증된 PR을 병합한다.
   PostgreSQL·MinIO·PVC는 유지하며 `storage_gc_maintenance`는 설정하지 않는다.
2. 네 Argo Application이 해당 revision을 반영했고 8개 서비스 Deployment의 desired/ready가
   모두 0인지 확인한다. 기존 Pod가 종료되기 전에 변환을 시작하지 않는다.
3. 개인별 값을 출력하지 않고 기존 테이블의 행 수·식별자와 내용 digest를 기록한다.
   신규 nullable 필드와 스키마 marker는 내용 비교에서 구분한다.
4. 각 서비스 namespace에서 표의 새 이미지로 일회성 Job을 실행한다. 기존
   `<service>-external` Secret의 `DATABASE_URL`만 참조하고 `ecr-registry` imagePullSecret을
   사용한다. ServiceAccount token은 마운트하지 않는다. 자동 재시도 없이 실패를 확인한다.
   변환 도구는 스키마를 검증하고 같은 DB transaction과 advisory lock 안에서 적용한다.
5. Studio version 12, Memory·Librio의 해당 이미지 fingerprint, 기존 행의 내용 보존을 확인한다.
   Memory·Librio의 저장된 `BETTER_AUTH_URL` override는 같은 값을 `AUTH_BASE_URL`로 옮긴다.
   서로 다른 두 값이 있으면 도구가 거절하므로 운영자가 원인을 확인한다.

## 재개와 확인

1. 네 maintenance 값을 `false`로 되돌리고 생성·검증·병합한다. 신규 이미지와 공통 환경변수
   이름을 함께 유지한다. 변환 후 이전 이미지만 재배포하지 않는다.
2. 네 Argo Application의 revision·Synced·Healthy, Pod image·readiness와 worker를 확인한다.
   Forge version 8, 기존 workspace·소유권 보존도 확인한다.
3. 공개 health와 실제 로그인, 기존 계정·콘텐츠 접근, `/profile` 표시를 확인한다.
   외부 회사 역할은 앱의 관리 권한을 자동으로 부여하지 않는다.
4. 완료된 일회성 Job을 정리하고 `GITOPS_AUTO_DEPLOY`를 이전 상태로 복구한다.
   변수 자체가 없었다면 삭제한다. 데이터·PVC·인증 secret은 정리 대상이 아니다.

변환이 실패하면 해당 Job의 종료 상태와 비밀값을 제외한 오류를 확인한다. 실패 transaction은
rollback되며, 성공한 서비스의 스키마를 임의로 낮추지 않는다. 새 버전의 기동 조건을 해결한 뒤
재개한다. 완료된 변환 도구는 같은 대상 스키마에서 다시 실행해도 데이터를 초기화하지 않는다.
