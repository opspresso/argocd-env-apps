# 스토리지 GC 유지보수

GC는 애플리케이션이 object를 새로 참조하거나 변경하지 않는 구간에서만 실행합니다.
이 문서는 k3s 선언(MinIO 사용)의 쓰기 중지·재개 절차입니다. k3s 선언은 현재 어느 클러스터에도 배포하지 않으며, EKS는 MinIO 대신 S3를 사용합니다. object 삭제 대상과 데이터 검증은 별도 GC 계획을 따릅니다.
PostgreSQL·MinIO 서버와 PVC를 유지하며 EKS의 설정·배포 지원을 변경하지 않습니다.

## 중지 설정

`env/k3s-demo.yaml`에 다음 값을 함께 설정하고 생성기로 values를 갱신합니다.

```yaml
agent_studio_maintenance: true
agent_memory_maintenance: true
agent_librio_maintenance: true
agent_forge_maintenance: true
storage_gc_maintenance: true
minio:
  storage: 10Gi
  console: false
```

`storage_gc_maintenance`는 k3s에서 네 서비스가 모두 maintenance이고 콘솔이 꺼져 있을 때만
허용됩니다. 잘못된 조합은 생성기가 산출물을 쓰기 전에 거부합니다.

- Studio web·ticker, Memory web, Librio web·worker, Forge controller의 총 6개 Deployment가
  0개가 됩니다. HPA도 비활성화합니다.
- MinIO console의 Deployment·Service·NetworkPolicy는 제거됩니다.
- MinIO 서버의 기존 NetworkPolicy를 같은 이름·Pod selector의 `ingress: []` 정책으로 교체합니다.
  별도 deny 정책을 추가하는 방식이 아닙니다. 여러 NetworkPolicy의 allow 규칙은 합쳐지므로,
  같은 MinIO Pod를 선택하는 다른 정책이 연결을 허용하지 않는지도 확인해야 합니다.
- MinIO 서버 Deployment·PVC·Service와 PostgreSQL manifest는 변경하지 않습니다.

MinIO chart의 일반 NetworkPolicy는 전체 ingress를 빈 목록으로 바꾸는 옵션을 제공하지 않습니다.
유지보수 중에만 이 정책을 끄고 조건부 `raw` dependency가 동일 리소스를 생성합니다.
재개하면 vendor 정책으로 돌아갑니다.

## 적용과 GC 전제조건

1. 기존 활성 실행·업로드·queue 작업을 확인하고 승인된 유지보수 변경을 반영합니다.
   Argo Application은 각각 동기화되므로 여러 앱이 동시에 정지했다고 가정하지 않습니다.
2. 승인 revision 반영, 6개 Deployment의 desired/ready 0, 해당 비종료 Pod와 MinIO console Pod 0을
   확인합니다. Forge 실행 namespace에도 활성 Sandbox Pod·Job이 없어야 합니다.
3. 실제 env/envFrom·Secret key 참조에서 다른 S3 writer가 없는지 확인합니다.
   S3용 NodePort·LoadBalancer·Ingress·HTTPRoute·hostPort·외부 alias가 없는지도 대조합니다.
4. MinIO Pod에 적용되는 모든 NetworkPolicy를 대조한 뒤 일반 Pod의 S3 연결이 차단되고,
   지정한 operator SSH host→MinIO ClusterIP 터널에서는 인증된 HEAD가 성공하는지 확인합니다.
   NetworkPolicy는 노드 발신과 `hostNetwork` Pod까지 완전히 차단하지 않습니다.
   전용 경로의 성공을 추측하지 않으며, 실패하면 GC를 실행하지 않습니다.
5. 중지와 접근 경계를 확인한 뒤에만 PostgreSQL 참조 snapshot과 GC 대상 검증을 수행합니다.
   정책 변경 전 기존 연결이 남아 있을 수 있으므로 Pod 종료 확인을 생략하지 않습니다.

현재 GC 경로는 operator local SDK→검증된 SSH host→MinIO ClusterIP입니다.
GC Pod를 만들거나 공유 ServiceAccount에 새 권한을 부여하지 않습니다.
credential은 메모리로 전달하며 GitOps 값·argv·로그에 저장하지 않습니다.

## 재개

GC 완료·참조 보존·남은 대상 검증이 끝난 뒤 같은 env에서 네 maintenance 값을 `false`로 돌리고,
`storage_gc_maintenance`와 이번 작업에서 추가한 `minio.console` override를 제거합니다.
다른 설정과 새로 배포된 앱 버전은 보존합니다.

```bash
GITHUB_PUSH=false bash scripts/build.sh
python3 scripts/validate.py --all-charts
pytest
```

재개 렌더에서 원래 MinIO ingress 정책과 콘솔이 돌아오고 앱 6개가 정상 replica를 갖는지 확인합니다.
반영 후 Argo 상태, 네 서비스 health, worker·storage 접근을 확인합니다.
