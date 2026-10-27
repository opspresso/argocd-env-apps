"""Stop application writers and replace the existing MinIO allow policy during GC."""
import copy
from pathlib import Path

import pytest
import yaml

from test_agent_memory_maintenance import render

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ('studio', 'memory', 'librio', 'forge')


def contexts():
    normal = yaml.safe_load((ROOT / 'env/k3s-demo.yaml').read_text())
    for service in SERVICES:
        normal[f'agent_{service}_maintenance'] = False
    normal.pop('storage_gc_maintenance', None)
    normal['minio'].pop('console', None)
    paused = copy.deepcopy(normal)
    for service in SERVICES:
        paused[f'agent_{service}_maintenance'] = True
    paused['storage_gc_maintenance'] = True
    paused['minio']['console'] = False
    return normal, paused


def identity(item):
    return item['kind'], item['metadata'].get('namespace', 'default'), item['metadata']['name']


def test_gc_stops_all_application_processes(tmp_path):
    _, paused = contexts()
    deployments = []
    for service in SERVICES:
        docs = render('agent-' + service, paused, tmp_path)
        deployments.extend(item for item in docs if item['kind'] == 'Deployment')
        assert not any(item['kind'] in {'HorizontalPodAutoscaler', 'CronJob'} for item in docs)
    assert {item['metadata']['name'] for item in deployments} == {
        'agent-studio', 'agent-studio-ticker', 'agent-memory', 'agent-librio', 'agent-librio-worker', 'agent-forge'}
    assert all(item['spec']['replicas'] == 0 for item in deployments)


def test_gc_replaces_minio_ingress_without_changing_server_or_storage(tmp_path):
    normal, paused = contexts()
    before = {identity(item): item for item in render('minio', normal, tmp_path)}
    after = {identity(item): item for item in render('minio', paused, tmp_path)}
    before_policy = next(item for item in before.values() if item['kind'] == 'NetworkPolicy' and item['metadata']['name'] == 'minio')
    policies = [item for item in after.values() if item['kind'] == 'NetworkPolicy']
    assert len(policies) == 1
    policy = policies[0]
    assert identity(policy) == identity(before_policy)
    assert policy['spec'] == {**before_policy['spec'], 'ingress': []}
    assert not any(item['metadata']['name'] == 'minio-console' for item in after.values())
    for kind in ['Deployment', 'PersistentVolumeClaim', 'Service', 'Secret', 'ExternalSecret']:
        for key, item in before.items():
            if key[0] == kind and key[2] != 'minio-console':
                assert after[key] == item
    server = next(item for item in after.values() if item['kind'] == 'Deployment')
    assert server['spec'].get('replicas', 1) == 1


def test_gc_keeps_postgresql_manifest_identical(tmp_path):
    normal, paused = contexts()
    assert render('postgresql', normal, tmp_path) == render('postgresql', paused, tmp_path)


@pytest.mark.parametrize('platform', ['eks', 'k3s'])
def test_resumed_minio_uses_vendor_network_policy(platform, tmp_path):
    env = yaml.safe_load((ROOT / f'env/{platform}-demo.yaml').read_text())
    env.pop('storage_gc_maintenance', None)
    env.get('minio', {}).pop('console', None)
    docs = render('minio', env, tmp_path)
    policies = [item for item in docs if item['kind'] == 'NetworkPolicy' and item['metadata']['name'] == 'minio']
    assert len(policies) == 1
    assert policies[0]['spec']['ingress'] == [{'ports': [{'port': 9000}]}]
