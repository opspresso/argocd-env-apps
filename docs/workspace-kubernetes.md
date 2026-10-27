# Kubernetes Workspace 운영

실행 소유자는 Forge입니다. `agent-forge` ServiceAccount는 `agent-forge-workspaces`에서
`jobs.batch` get/list/create/delete, Pod get/list, Pod exec get/create만 사용할 수 있습니다.
Studio는 Forge API를 관찰하며 실행 namespace의 RBAC를 갖지 않습니다.

Forge의 Job은 passive supervisor를 실행합니다. 사용자 명령은 저장된 Job·Pod UID를 검증한
뒤 exec로 시작합니다. `backoffLimit: 0`, `restartPolicy: Never`와 단일 parallelism을 사용하여
Kubernetes의 자동 재시도가 사용자 명령을 다시 실행하지 못하게 합니다.
Job owner가 있는 Pod는 EKS CNI 정책 적용을 위한 필수 조건입니다. 운영 클러스터는 EKS `eks-demo`입니다.

실행 Pod는 `sandbox` ServiceAccount를 사용하며 token을 mount하지 않습니다.
운영 Secret·공급자 장기 키·Git token·hostPath도 전달하지 않습니다.
namespace quota는 EKS `eks-demo`에서 Pod와 Job을 각각 32개로 제한합니다(`workspace_max_pods`). 배포하지 않는 k3s 선언은 2개입니다.
`resources: false`인 k3s 선언은 compute requests/limits를 생략하고 Pod/Job 수와 `emptyDir.sizeLimit`을 유지합니다.
EKS는 CPU·메모리·ephemeral-storage requests/limits와 quota를 함께 선언합니다.

## 네트워크

NetworkPolicy는 ingress/egress를 기본 거부합니다. 실행 Pod에는 다음 egress만 허용합니다.

- cluster DNS: EKS `172.20.0.10/32`, k3s `10.43.0.10/32`와 CoreDNS Pod
- public IPv4 HTTP/HTTPS: private·loopback·link-local·metadata·multicast 대역 제외
- 해당 Forge Service가 선택하는 Pod의 실제 HTTP 포트 `3103`

Sandbox가 읽는 `FORGE_SANDBOX_API_URL`은 Service 포트를 사용합니다.
NetworkPolicy는 DNAT 뒤의 Pod 포트를 사용하므로 두 값을 혼동하지 않습니다.
native CLI는 이 주소의 인증된 모델 Gateway를 호출합니다. 같은 namespace의 다른 서비스나
Kubernetes API, Studio·Memory·Librio·PostgreSQL에는 직접 연결할 수 없습니다.
폐쇄망은 `workspace_allow_public: false`, 추가 내부 목적지는 `workspace_extra_egress`에 정확히 선언합니다.

앱 namespace ingress는 별도 정책입니다. Forge에 Studio와 Sandbox의 연결을 허용합니다.
Forge는 Studio 정책 callback을 호출하지 않습니다. Forge controller의 외부 provider/Git 통신과
Kubernetes API 접근은 Sandbox egress와 구분합니다.

## 이미지·디스크·복원

`app.image.tag`가 controller와 Sandbox의 유일한 버전 원천입니다.
controller `<repository>:v0.1.0`이면 Sandbox는 `<repository>:sandbox-v0.1.0`입니다.
ECR imagePullSecret은 실행 namespace의 generator가 갱신합니다.
`agent-forge-sandbox` ConfigMap과 설정 checksum으로 새 controller가 바뀐 실행 설정을 읽습니다.

EKS의 기본 Pod는 1 CPU·2Gi memory·4608Mi ephemeral storage를 예약합니다.
`workspace_disk_mb=2048`에서 `/workspace`, `/control`, `/tmp`를 포함한 저장 예약이며,
32개 Pod의 ephemeral 예약량은 144Gi입니다. 이미지 캐시와 호스트 로그는 별도입니다.
`emptyDir.sizeLimit`은 동기식 쓰기 quota가 아니라 kubelet의 퇴거 기준입니다.

파일·native session은 Forge 전용 키로 암호화한 PostgreSQL checkpoint에서 복원합니다.
Pod별 PVC는 만들지 않습니다. 마지막으로 확정된 checkpoint 뒤의 파일은 Pod 소실 시 복구되지 않습니다.
수명·lease·output cursor·취소·unknown 상태는 Forge가 관리합니다.

## 폐기된 실행 자원

Studio의 Docker daemon·Service·PVC·실행 namespace·RBAC는 선언하지 않습니다.
Studio에는 Workspace worker도 없습니다. Studio는 web과 ticker만 실행하고 Forge를 MCP로 호출합니다.
예전 `app.legacyExecution` 값으로 구 실행기를 재활성화할 수 없습니다.
기존 클러스터 자원의 삭제는 [소비자·내용 확인 절차](retired-agent-resources.md)를 따릅니다.

## 검증

`python3 scripts/validate.py --all-charts`와 pytest는 EKS와 k3s 선언의 이미지 결합,
RBAC, Pod/Job quota, IPv4 네트워크, Service/Pod 포트, 폐기된 Studio 실행 자원 부재를 검사합니다.
이 결과는 CNI의 실제 패킷 차단이나 실행 성공을 보장하지 않습니다.

배포 후에는 제한된 테스트 Workspace에서 다음을 확인합니다.

1. Job ownerReference와 저장된 Job·Pod UID가 일치하고 Sandbox에 운영 token/Secret이 없는지 확인합니다.
2. 명령 접수→출력 cursor 조회→재연결에서 같은 명령이 중복 실행되지 않는지 확인합니다.
3. DNS와 public HTTPS·Forge Gateway 연결, PostgreSQL·metadata·다른 Workspace 차단을 함께 확인합니다.
   차단 대상이 실제로 제공 중인지 양성 대조로 확인해야 합니다.
4. 취소·lease handoff·checkpoint 저장→Pod 종료→복원·namespace quota를 확인합니다.
5. 실행 Pod 이벤트와 Forge terminal/unknown/output_loss, 고아 자원 상태를 대조합니다.

Pod 소실·디스크 압박 시험은 지정한 테스트 Workspace에만 수행합니다.
실제 EKS가 생긴 뒤에는 controller-owned Pod에 CNI 정책이 적용되는지 다시 검증합니다.
관측 지표·경보는 `argocd-env-addons`가 소유합니다.
