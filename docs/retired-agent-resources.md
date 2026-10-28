# 폐기된 Agent 실행·그래프 자원 정리

현재 네 서비스는 `agent_platform` PostgreSQL을 사용합니다. Knowledge 지식 그래프는 PostgreSQL이
원장이며, 이 배포는 `NEO4J_*`를 주입하지 않습니다. Forge 실행 Pod는 `emptyDir`를 사용하고
파일·세션은 `agent_forge_checkpoints`의 암호화된 checkpoint에서 복원합니다.
Studio의 audio/Workspace worker도 더 이상 선언하지 않습니다. Argo CD `prune`이 두 Deployment를 삭제합니다.

GitOps는 Neo4j chart/Application/Secret bootstrap과 Studio의 이전 Docker PVC·실행 namespace를
더 이상 생성하지 않습니다. 현재 앱, Forge 실행 namespace, PostgreSQL과 object storage 선언은 유지합니다.
Knowledge 소스의 선택적 Neo4j adapter와 Forge의 독립 Docker provider는 지원 기능이며 이 정리의 대상이 아닙니다.

## 기존 클러스터의 제거 순서

1. 실제 앱 프로세스의 `NEO4J_URI` 미설정과 Forge Kubernetes provider를 확인합니다.
   전체 namespace의 Pod와 workload template에서 이전 Service·PVC·namespace 소비자를 확인합니다.
2. PostgreSQL과 MinIO의 PVC/PV UID를 보호 목록에 고정합니다. 두 PVC는 삭제하지 않습니다.
   폐기 후보의 PVC/PV UID, reclaim policy, 저장 경로·크기와 내용을 별도로 확인합니다.
   `Delete` reclaim policy에서는 PVC 삭제가 실제 bytes 삭제로 이어집니다.
3. GitOps 제거 변경을 검증하고 반영합니다. 실제 Application finalizer와 Argo prune 동작을 확인합니다.
   finalizer 없는 Application 삭제는 하위 workload 삭제를 보장하지 않습니다.
   이전 `Prune=false,Delete=false` 자원은 manifest가 없어져도 남을 수 있습니다.
4. 검토한 UID의 Neo4j StatefulSet을 정지하고 Pod 종료·사용자 연결 부재를 확인합니다.
   남은 Neo4j Service·설정·Secret과 폐기된 Studio namespace 자원을 정확한 이름으로 제거합니다.
   활성 `agent-memory` namespace는 유지합니다.
5. 삭제 직전에 PVC 소비자가 다시 생기지 않았는지 확인한 뒤 승인된 폐기 PVC만 제거합니다.
   namespace 전체 삭제는 그 안에 현재 Pod·Job·PVC와 다른 소유 자원이 없을 때만 수행합니다.
6. PostgreSQL·MinIO PVC UID, 네 서비스 health와 Knowledge 검색·Forge checkpoint 복원을 확인합니다.
   삭제 대상 재생성·남은 endpoints·고아 PV 여부도 확인합니다.

구체적인 대상 UID와 실제 검증 결과는 운영 영수증에 기록합니다. GitOps 검사 통과만으로
운영 자원이나 데이터를 삭제했다고 보고하지 않습니다. local 개발 DB·볼륨은 이 절차의 대상이 아닙니다.
