import unittest

from bosshunter.collection.registry import CollectorRegistry


class _StubCollector:
    def __init__(self, tag: str = "default"):
        self.tag = tag


class CollectorRegistryTests(unittest.TestCase):
    def test_init_with_factories(self):
        registry = CollectorRegistry({"alpha": lambda: _StubCollector("a")})

        self.assertIn("alpha", registry.platforms())

    def test_init_without_factories_defaults_to_empty(self):
        registry = CollectorRegistry()

        self.assertEqual(registry.platforms(), ())

    def test_register_adds_platform(self):
        registry = CollectorRegistry()
        registry.register("beta", lambda: _StubCollector("b"))

        self.assertIn("beta", registry.platforms())

    def test_get_returns_collector_instance(self):
        registry = CollectorRegistry({"alpha": lambda: _StubCollector("a")})
        collector = registry.get("alpha")

        self.assertIsInstance(collector, _StubCollector)
        self.assertEqual(collector.tag, "a")

    def test_get_unregistered_platform_raises_value_error(self):
        registry = CollectorRegistry()

        with self.assertRaises(ValueError) as ctx:
            registry.get("nonexistent")

        self.assertIn("nonexistent", str(ctx.exception))

    def test_platforms_returns_tuple(self):
        registry = CollectorRegistry({"a": lambda: _StubCollector(), "b": lambda: _StubCollector()})
        platforms = registry.platforms()

        self.assertIsInstance(platforms, tuple)
        self.assertEqual(len(platforms), 2)


if __name__ == "__main__":
    unittest.main()