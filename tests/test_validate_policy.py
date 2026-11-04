import json
import subprocess
import unittest
from unittest.mock import patch

import validate


class ValidatePolicyTests(unittest.TestCase):
    def test_successful_helm_render_with_resources_fails_local_validation(self):
        output = json.dumps({
            "apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "upstream"},
            "spec": {"template": {"spec": {"containers": [
                {"name": "main", "resources": {"requests": {"cpu": "100m"}}},
            ]}}},
        })
        result = subprocess.CompletedProcess([], 0, stdout=output, stderr="")
        for platform in ("eks", "local"):
            target = {"name": "upstream", "chart": "charts/upstream", "namespace": "default",
                      "appset": f"addons/{platform}/upstream.yaml", "value_files": []}
            with self.subTest(platform=platform), patch.object(validate.subprocess, "run", return_value=result):
                error = validate.render(target, None)
                if platform == "eks":
                    self.assertIn("limits.memory", error)
                else:
                    self.assertIn("reserves compute", error)


if __name__ == "__main__":
    unittest.main()
