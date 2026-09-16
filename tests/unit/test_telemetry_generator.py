"""Tests for the telemetry generator."""

import pytest

from app.services.telemetry_generator import TelemetryGenerator, ANOMALY_METRICS


class TestTelemetryGenerator:
    """Test the telemetry generator produces valid, realistic output."""

    def setup_method(self):
        self.gen = TelemetryGenerator()

    def test_generates_valid_event(self):
        event = self.gen.generate(sequence=1)
        assert event.sequence == 1
        assert 0 <= event.cpu <= 100
        assert 0 <= event.memory <= 100
        assert 0 <= event.temperature <= 120
        assert 0 <= event.network_mbps <= 10000
        assert 0 <= event.requests_per_second <= 1000000
        assert 0 <= event.error_rate <= 100
        assert 0 <= event.latency_ms <= 10000

    def test_sequence_is_monotonic(self):
        sequences = [self.gen.generate(sequence=s).sequence for s in range(100, 110)]
        assert sequences == list(range(100, 110))

    def test_timestamps_are_timezone_aware(self):
        event = self.gen.generate(sequence=1)
        assert event.timestamp.tzinfo is not None

    def test_realistic_variation(self):
        """Values should vary over time, not be constant."""
        events = [self.gen.generate(sequence=i) for i in range(50)]
        cpu_values = [e.cpu for e in events]
        assert len(set(cpu_values)) > 5  # Should have significant variation

    def test_values_are_not_purely_random(self):
        """Successive values should be correlated (smooth), not jumping wildly."""
        events = [self.gen.generate(sequence=i) for i in range(100)]
        cpu_diffs = [abs(events[i + 1].cpu - events[i].cpu) for i in range(len(events) - 1)]
        avg_diff = sum(cpu_diffs) / len(cpu_diffs)
        # Smooth variation: average change should be small relative to the range
        assert avg_diff < 10

    def test_anomaly_trigger_cpu(self):
        """Triggering a CPU anomaly should increase CPU and correlated metrics."""
        # Generate baseline events
        baseline = [self.gen.generate(sequence=i) for i in range(20)]
        baseline_cpu_avg = sum(e.cpu for e in baseline) / len(baseline)

        self.gen.set_anomaly("cpu", intensity=1.0, duration=10)
        anomaly_events = [self.gen.generate(sequence=i) for i in range(20, 30)]
        anomaly_cpu_avg = sum(e.cpu for e in anomaly_events) / len(anomaly_events)

        assert anomaly_cpu_avg > baseline_cpu_avg

    def test_anomaly_trigger_memory(self):
        self.gen.set_anomaly("memory", intensity=1.0, duration=10)
        events = [self.gen.generate(sequence=i) for i in range(10)]
        assert any(e.memory > 80 for e in events)

    def test_anomaly_trigger_temperature(self):
        self.gen.set_anomaly("temperature", intensity=1.0, duration=10)
        events = [self.gen.generate(sequence=i) for i in range(10)]
        assert any(e.temperature > 70 for e in events)

    def test_anomaly_trigger_latency(self):
        self.gen.set_anomaly("latency", intensity=1.0, duration=10)
        events = [self.gen.generate(sequence=i) for i in range(10)]
        assert any(e.latency_ms > 60 for e in events)

    def test_anomaly_trigger_error_rate(self):
        self.gen.set_anomaly("error_rate", intensity=1.0, duration=10)
        events = [self.gen.generate(sequence=i) for i in range(10)]
        assert any(e.error_rate > 5 for e in events)

    def test_anomaly_correlation_cpu_to_latency(self):
        """High CPU should correlate with higher latency."""
        baseline = [self.gen.generate(sequence=i) for i in range(20)]
        baseline_lat = sum(e.latency_ms for e in baseline) / len(baseline)

        self.gen.set_anomaly("cpu", intensity=1.0, duration=10)
        anomaly = [self.gen.generate(sequence=i) for i in range(20, 30)]
        anomaly_lat = sum(e.latency_ms for e in anomaly) / len(anomaly)

        assert anomaly_lat > baseline_lat

    def test_anomaly_correlation_latency_to_error_rate(self):
        """High latency should correlate with higher error rate."""
        baseline = [self.gen.generate(sequence=i) for i in range(20)]
        baseline_err = sum(e.error_rate for e in baseline) / len(baseline)

        self.gen.set_anomaly("latency", intensity=1.0, duration=10)
        anomaly = [self.gen.generate(sequence=i) for i in range(20, 30)]
        anomaly_err = sum(e.error_rate for e in anomaly) / len(anomaly)

        assert anomaly_err > baseline_err

    def test_anomaly_expires(self):
        """An anomaly should expire after its duration."""
        self.gen.set_anomaly("cpu", intensity=1.0, duration=5)
        for i in range(5):
            self.gen.generate(sequence=i)
        assert self.gen.active_anomaly is None  # Should have expired

    def test_unknown_anomaly_metric_raises(self):
        with pytest.raises(ValueError, match="Unknown anomaly metric"):
            self.gen.set_anomaly("nonexistent")

    def test_anomaly_metrics_set(self):
        assert ANOMALY_METRICS == {"cpu", "memory", "temperature", "latency", "error_rate"}

    def test_reset(self):
        self.gen.set_anomaly("cpu", duration=100)
        self.gen.reset()
        assert self.gen.active_anomaly is None
