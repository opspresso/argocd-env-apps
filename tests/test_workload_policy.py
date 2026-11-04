import json
import unittest

from workload_policy import policy_errors, target_platform


def manifest(kind, spec, name="test"):
    return json.dumps({"apiVersion": "v1", "kind": kind, "metadata": {"name": name}, "spec": spec})


class WorkloadPolicyTests(unittest.TestCase):
    def test_eks_ignores_configmap_data_and_checks_nested_job_pod_specs(self):
        config = json.dumps({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "config"}, "data": {"containers": "[]", "initContainers": "{}"}})
        self.assertEqual(policy_errors(config, "eks"), [])
        pod = {"containers": [{"name": "unreserved"}]}
        cron = manifest("CronJob", {"jobTemplate": {"spec": {"template": {"spec": pod}}}})
        analysis = manifest("ClusterAnalysisTemplate", {"metrics": [{"provider": {"job": {"spec": {"template": {"spec": pod}}}}}]})
        self.assertEqual(len(policy_errors(cron, "eks")), 3)
        self.assertEqual(len(policy_errors(analysis, "eks")), 3)

    def test_eks_quantity_suffixes_are_case_sensitive(self):
        for memory, valid in [("128Ki", True), ("128ki", False), ("1K", False), ("1k", True), ("1e6", True)]:
            resources = {"requests": {"cpu": "100m", "memory": memory}, "limits": {"memory": "256Mi"}}
            self.assertEqual(not policy_errors(manifest("Pod", {"containers": [{"resources": resources}]}), "eks"), valid)

    def test_retired_neo4j_name_does_not_bypass_resource_policy(self):
        for platform in ("local",):
            for name, container, collection, resources, allowed in [
                ("memory-neo4j", "neo4j", "containers", {"requests": {"cpu": "500m", "memory": "2Gi"}}, False),
                ("other", "neo4j", "containers", {"requests": {"cpu": "500m", "memory": "2Gi"}}, False),
                ("memory-neo4j", "sidecar", "containers", {"requests": {"cpu": "500m", "memory": "2Gi"}}, False),
                ("memory-neo4j", "neo4j", "initContainers", {"requests": {"cpu": "500m", "memory": "2Gi"}}, False),
                ("memory-neo4j", "neo4j", "containers", {"requests": {"cpu": "1000m", "memory": "2Gi"}}, False),
                ("memory-neo4j", "neo4j", "containers", {"limits": {"memory": "2Gi"}}, False),
            ]:
                with self.subTest(platform=platform, name=name, container=container, collection=collection, resources=resources):
                    spec = {"template": {"spec": {collection: [{"name": container, "resources": resources}]}}}
                    self.assertEqual(not policy_errors(manifest("StatefulSet", spec, name), platform), allowed)

    def test_eks_requires_reservations_and_memory_limits_without_forcing_cpu_limits(self):
        self.assertEqual(target_platform({"appset": "apps/example.yaml"}, {}), "eks")
        self.assertEqual(target_platform({"appset": "apps/eks/example.yaml"}, {"env": "eks"}), "eks")
        self.assertEqual(policy_errors(manifest("HorizontalPodAutoscaler", {}), "eks"), [])
        complete = {"requests": {"cpu": "100m", "memory": "128Mi"}, "limits": {"memory": "256Mi"}}
        self.assertEqual(policy_errors(manifest("Pod", {"containers": [{"resources": complete}]}), "eks"), [])
        for field in ("containers", "initContainers"):
            self.assertEqual(len(policy_errors(manifest("Pod", {field: [{"name": "unreserved"}]}), "eks")), 3)
        self.assertEqual(len(policy_errors(manifest("Prometheus", {"resources": complete}), "eks")), 0)
        self.assertEqual(len(policy_errors(manifest("Prometheus", {}), "eks")), 3)

    def test_eks_rejects_zero_or_invalid_quantities(self):
        for cpu in ("0", "0m", "bad", False):
            resources = {"requests": {"cpu": cpu, "memory": "128Mi"}, "limits": {"memory": "256Mi"}}
            self.assertTrue(policy_errors(manifest("Pod", {"containers": [{"resources": resources}]}), "eks"))

    def test_low_reservation_platform_is_detected(self):
        for platform in ("local",):
            with self.subTest(platform=platform):
                self.assertEqual(target_platform({"appset": f"apps/{platform}/example.yaml"}, {}), platform)
                self.assertEqual(target_platform({"appset": "apps/example.yaml"}, {"env": platform}), platform)

    def test_main_init_and_sidecar_container_resources_are_rejected(self):
        for platform in ("local",):
            for container_key in ("containers", "initContainers", "ephemeralContainers"):
                for field in ("requests", "limits"):
                    with self.subTest(platform=platform, containers=container_key, field=field):
                        spec = {"template": {"spec": {container_key: [{"name": "sidecar", "resources": {field: {"memory": "128Mi"}}}]}}}
                        errors = policy_errors(manifest("Job", spec), platform)
                        self.assertEqual(len(errors), 1)
                        self.assertIn(field, errors[0])

    def test_pvc_storage_and_empty_compute_settings_are_allowed(self):
        pvc = manifest("PersistentVolumeClaim", {"resources": {"requests": {"storage": "5Gi"}}})
        pod = manifest("Pod", {"containers": [{"resources": None}, {"resources": {}}, {}], "initContainers": None})
        for platform in ("local",):
            self.assertEqual(policy_errors(pvc + "\n---\n" + pod, platform), [])

    def test_all_autoscaler_kinds_are_rejected(self):
        for kind in ("HorizontalPodAutoscaler", "VerticalPodAutoscaler", "ScaledObject", "ScaledJob"):
            for platform in ("local",):
                with self.subTest(kind=kind, platform=platform):
                    self.assertEqual(len(policy_errors(manifest(kind, {}), platform)), 1)

    def test_compute_quotas_cannot_force_dynamic_pod_reservations(self):
        for platform in ("local",):
            for name in ("cpu", "memory", "ephemeral-storage", "requests.cpu", "limits.memory",
                         "requests.ephemeral-storage", "requests.nvidia.com/gpu"):
                with self.subTest(platform=platform, name=name):
                    self.assertTrue(policy_errors(manifest("ResourceQuota", {"hard": {name: "1"}}), platform))
            self.assertEqual(policy_errors(manifest("ResourceQuota", {"hard": {
                "pods": "2", "persistentvolumeclaims": "2", "requests.storage": "10Gi"
            }}), platform), [])
        self.assertEqual(policy_errors(manifest("ResourceQuota", {"hard": {"requests.cpu": "2"}}), "eks"), [])

    def test_operator_defaults_and_reloader_resources_are_checked(self):
        for kind in ("VMAgent", "VMAlert", "VMAlertmanager", "VMSingle"):
            with self.subTest(kind=kind):
                self.assertTrue(policy_errors(manifest(kind, {}), "local"))
                self.assertEqual(policy_errors(manifest(kind, {"useDefaultResources": False}), "local"), [])
        reloader = {"useDefaultResources": False, "configReloaderResources": {"limits": {"cpu": "100m"}}}
        self.assertIn("configReloaderResources", policy_errors(manifest("VMAgent", reloader), "local")[0])

    def test_vm_resources_must_be_objects_when_present(self):
        for kind in ("VMAgent", "VMAlert", "VMAlertmanager", "VMSingle"):
            for field in ("resources", "configReloaderResources"):
                with self.subTest(kind=kind, field=field):
                    spec = {"useDefaultResources": False, field: None}
                    self.assertTrue(policy_errors(manifest(kind, spec), "local"))
                    spec[field] = {}
                    self.assertEqual(policy_errors(manifest(kind, spec), "local"), [])

    def test_vmcluster_policy_is_checked_per_component(self):
        valid = {name: {"useDefaultResources": False} for name in ("vminsert", "vmselect", "vmstorage")}
        self.assertEqual(policy_errors(manifest("VMCluster", valid), "local"), [])
        valid["vmselect"] = {"replicaCount": 1}
        self.assertIn("vmselect", policy_errors(manifest("VMCluster", valid), "local")[0])
        valid["vmselect"] = {}
        self.assertIn("vmselect", policy_errors(manifest("VMCluster", valid), "local")[0])

    def test_crd_schemas_are_not_treated_as_workloads(self):
        schema = {"versions": [{"schema": {"default": {"resources": {"requests": {"cpu": "100m"}}}}}]}
        self.assertEqual(policy_errors(manifest("CustomResourceDefinition", schema), "local"), [])


if __name__ == "__main__":
    unittest.main()
