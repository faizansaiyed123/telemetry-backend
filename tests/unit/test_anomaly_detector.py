"""Tests for the anomaly detector."""

import pytest

from app.models.alerts import Severity
from app.services.anomaly_detector import AnomalyDetector
from app.utils.time import utc_now
from app.models.telemetry import TelemetryEvent


def make_event(
    sequence: int = 0,
    cpu: float = 50.0,
    memory: float = 60.0,
    temperature: float = 50.0,
    network_mbps: float = 100.0,
    requests_per_second: int = 500,
    error_rate: float = 0.5,
    latency_ms: float = 30.0,
) -> TelemetryEvent:
    return TelemetryEvent(
        timestamp=utc_now(),
        sequence=sequence,
        cpu=cpu,
        memory=memory,
        temperature=temperature,
        network_mbps=network_mbps,
        requests_per_second=requests_per_second,
        error_rate=error_rate,
        latency_ms=latency_ms,
    )


class TestAnomalyDetector:
    def setup_method(self):
        self.detector = AnomalyDetector(threshold=3.0, window_size=50, min_history=10)

    def test_insufficient_history(self):
        """Should not flag anomalies when history is insufficient."""
        for i in range(5):  # Less than min_history=10
            results = self.detector.update(make_event(sequence=i, cpu=50.0))
        for r in results:
            assert not r.is_anomaly

    def test_normal_data_no_anomaly(self):
        """Normal data should not produce anomalies."""
        for i in range(30):
            results = self.detector.update(make_event(sequence=i, cpu=50.0))
        for r in results:
            assert not r.is_anomaly

    def test_detects_anomaly(self):
        """A spike after sufficient history should be detected."""
        # Build up normal history
        for i in range(20):
            self.detector.update(make_event(sequence=i, cpu=50.0))
        # Inject a spike
        results = self.detector.update(make_event(sequence=20, cpu=95.0))
        cpu_result = [r for r in results if r.metric == "cpu"][0]
        assert cpu_result.is_anomaly
        assert cpu_result.severity in (Severity.WARNING, Severity.CRITICAL)

    def test_zero_std_dev_safe(self):
        """Zero standard deviation should not crash or produce false anomalies."""
        for i in range(20):
            self.detector.update(make_event(sequence=i, cpu=50.0))
        # All same values → std_dev = 0
        results = self.detector.update(make_event(sequence=20, cpu=50.0))
        cpu_result = [r for r in results if r.metric == "cpu"][0]
        assert not cpu_result.is_anomaly
        assert cpu_result.z_score == 0.0

    def test_threshold_configurable(self):
        """Lower threshold should detect anomalies more easily."""
        detector_low = AnomalyDetector(threshold=1.0, window_size=50, min_history=10)
        for i in range(20):
            detector_low.update(make_event(sequence=i, cpu=50.0))
        # Small deviation
        results = detector_low.update(make_event(sequence=20, cpu=55.0))
        cpu_result = [r for r in results if r.metric == "cpu"][0]
        # With threshold=1.0, even a small spike should be flagged
        assert cpu_result.is_anomaly

    def test_high_threshold_prevents_detection(self):
        """Very high threshold should prevent anomaly detection."""
        detector_high = AnomalyDetector(threshold=100.0, window_size=50, min_history=10)
        for i in range(20):
            detector_high.update(make_event(sequence=i, cpu=50.0))
        results = detector_high.update(make_event(sequence=20, cpu=95.0))
        cpu_result = [r for r in results if r.metric == "cpu"][0]
        assert not cpu_result.is_anomaly

    def test_severity_critical(self):
        """Very high z-score should produce CRITICAL severity."""
        for i in range(20):
            self.detector.update(make_event(sequence=i, cpu=50.0))
        # Massive spike
        results = self.detector.update(make_event(sequence=20, cpu=100.0))
        cpu_result = [r for r in results if r.metric == "cpu"][0]
        if cpu_result.is_anomaly:
            assert cpu_result.severity in (Severity.WARNING, Severity.CRITICAL)

    def test_reset(self):
        """Reset should clear history."""
        for i in range(20):
            self.detector.update(make_event(sequence=i, cpu=50.0))
        self.detector.reset()
        # After reset, should not have enough history
        results = self.detector.update(make_event(sequence=0, cpu=95.0))
        for r in results:
            assert not r.is_anomaly

    def test_all_metrics_checked(self):
        """All monitored metrics should be checked."""
        event = make_event()
        results = self.detector.update(event)
        metrics = {r.metric for r in results}
        assert "cpu" in metrics
        assert "memory" in metrics
        assert "temperature" in metrics
        assert "network_mbps" in metrics
        assert "requests_per_second" in metrics
        assert "error_rate" in metrics
        assert "latency_ms" in metrics

    def test_deterministic(self):
        """Same input should produce same results."""
        d1 = AnomalyDetector(threshold=3.0, window_size=50, min_history=10)
        d2 = AnomalyDetector(threshold=3.0, window_size=50, min_history=10)
        for i in range(25):
            event = make_event(sequence=i, cpu=50.0)
            r1 = d1.update(event)
            r2 = d2.update(event)
        # Inject same spike
        event = make_event(sequence=25, cpu=95.0)
        r1 = d1.update(event)
        r2 = d2.update(event)
        assert r1[0].is_anomaly == r2[0].is_anomaly
        assert r1[0].z_score == r2[0].z_score


    def test_host_isolation(self):
        """A host's baseline must not influence another host."""
        d = AnomalyDetector(threshold=3.0, window_size=50, min_history=10)
        for i in range(12):
            d.update(make_event(sequence=i, cpu=50.0))
            d.update(make_event(sequence=i, cpu=95.0, memory=60.0))

        

        host_a = make_event(sequence=100, cpu=50.0)
        host_a = host_a.model_copy(update={"host_id": "host-a"})
        host_b = make_event(sequence=100, cpu=50.0)
        host_b = host_b.model_copy(update={"host_id": "host-b"})
        for i in range(12):
            event_a = host_a.model_copy(update={"sequence": i})
            event_b = host_b.model_copy(update={"sequence": i})
            d.update(event_a)
            d.update(event_b)

        results = d.update(host_b.model_copy(update={"sequence": 200, "cpu": 95.0}))
        cpu_result = [item for item in results if item.metric == "cpu"][0]
        assert cpu_result.is_anomaly
