"""A mismatched Knowledge schema must stop the sync before the new image replaces any Pod."""

from test_agent_platform import manifests, resource


def test_presync_check_runs_the_new_image_against_the_app_database():
    job = resource("agent-knowledge", "eks", "Job", "agent-knowledge-schema-check", image_tag="v9.8.7")
    annotations = job["metadata"]["annotations"]
    assert annotations["argocd.argoproj.io/hook"] == "PreSync"
    assert annotations["argocd.argoproj.io/hook-delete-policy"] == "BeforeHookCreation,HookSucceeded"

    web = resource("agent-knowledge", "eks", "Deployment", "agent-knowledge", image_tag="v9.8.7")["spec"]["template"]["spec"]
    pod = job["spec"]["template"]
    assert pod["metadata"]["labels"]["sidecar.istio.io/inject"] == "false"
    container = pod["spec"]["containers"][0]
    assert container["image"] == web["containers"][0]["image"]
    assert container["image"].endswith(":v9.8.7")
    assert "exec node scripts/schema-check.mjs" in container["command"][-1]
    # The check reads DATABASE_URL from the Secret the web Pods load.
    database = next(item for item in container["env"] if item["name"] == "DATABASE_URL")["valueFrom"]["secretKeyRef"]
    assert {"secretRef": {"name": database["name"]}} in web["containers"][0]["envFrom"]
    assert database["key"] == "DATABASE_URL"
    # A failed check fails the hook: no retry loop outlives the sync and nothing restarts in place.
    assert pod["spec"]["restartPolicy"] == "Never"
    assert job["spec"]["activeDeadlineSeconds"] <= 600


def test_presync_check_has_no_kubernetes_access():
    pod = resource("agent-knowledge", "eks", "Job", "agent-knowledge-schema-check")["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    assert sum(doc["kind"] == "Job" for doc in manifests("agent-knowledge", "eks")) == 1
