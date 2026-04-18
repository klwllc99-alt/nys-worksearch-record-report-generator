import unittest
from types import SimpleNamespace
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from metrics import MetricsStore, build_metrics_store


class FakeDocument:
    def __init__(self, key, store):
        self.key = key
        self.store = store

    def set(self, payload, merge=False):
        current = self.store.setdefault(self.key, {}) if merge else {}
        current.update(payload)
        self.store[self.key] = current

    def get(self):
        data = self.store.get(self.key)
        return SimpleNamespace(exists=data is not None, to_dict=lambda: data or {})

    def delete(self):
        self.store.pop(self.key, None)


class FakeCollection:
    def __init__(self, store):
        self.store = store

    def document(self, key):
        return FakeDocument(key, self.store)

    def stream(self):
        return [SimpleNamespace(id=key, to_dict=lambda data=value: data) for key, value in self.store.items()]


class FakeFirestoreClient:
    def __init__(self):
        self.collections = {}

    def collection(self, name):
        return FakeCollection(self.collections.setdefault(name, {}))


class MetricsRollupTests(unittest.TestCase):
    def make_request(self, path='/api/health', method='GET', headers=None, ip='127.0.0.1'):
        return SimpleNamespace(
            method=method,
            headers=headers or {},
            url=SimpleNamespace(path=path),
            client=SimpleNamespace(host=ip),
        )

    def test_summary_exposes_rolling_retention_metadata(self):
        store = MetricsStore(max_events=10, retention_days=7)
        store.record(self.make_request(), status_code=200, duration_ms=12.5)

        summary = store.summary()

        self.assertEqual(summary['storage_mode'], 'rolling-window')
        self.assertEqual(summary['retention_days'], 7)
        self.assertTrue(isinstance(summary['daily_totals'], list))
        self.assertGreaterEqual(len(summary['daily_totals']), 1)

    def test_build_metrics_store_falls_back_to_local_mode(self):
        store = build_metrics_store(max_events=10, retention_days=5)
        summary = store.summary()

        self.assertEqual(summary['storage_mode'], 'rolling-window')
        self.assertEqual(summary['retention_days'], 5)

    def test_firestore_mode_persists_daily_rollups(self):
        fake_client = FakeFirestoreClient()
        store = build_metrics_store(
            max_events=10,
            retention_days=14,
            storage_backend='firestore',
            firestore_client=fake_client,
            firestore_collection='metrics',
        )

        store.record(self.make_request(path='/api/generate'), status_code=200, duration_ms=25.0)
        summary = store.summary()

        self.assertEqual(summary['storage_mode'], 'firestore')
        self.assertEqual(summary['retention_days'], 14)
        self.assertEqual(summary['total_requests'], 1)
        self.assertGreaterEqual(len(summary['daily_totals']), 1)
        self.assertTrue(fake_client.collections['metrics'])


if __name__ == '__main__':
    unittest.main()
