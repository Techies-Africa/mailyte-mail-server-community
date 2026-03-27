
"""
Webhooks Metrics Module
Provides metrics collection functionality for the webhooks service.
"""

import time
from typing import Dict, Any

# Simple metrics collection
_metrics = {
    'webhooks_sent': 0,
    'webhooks_failed': 0,
    'webhook_success_rate': 0.0,
    'avg_response_time': 0,
    'uptime_seconds': 0
}

_start_time = time.time()

def get_metrics() -> Dict[str, Any]:
    """Get current metrics for the webhooks service."""
    global _metrics, _start_time
    
    current_time = time.time()
    _metrics['uptime_seconds'] = int(current_time - _start_time)
    
    # Calculate success rate
    total_webhooks = _metrics['webhooks_sent'] + _metrics['webhooks_failed']
    if total_webhooks > 0:
        _metrics['webhook_success_rate'] = (_metrics['webhooks_sent'] / total_webhooks) * 100
    
    return _metrics.copy()

def increment_metric(name: str, value: int = 1):
    """Increment a metric by the specified value."""
    global _metrics
    if name in _metrics:
        _metrics[name] += value

def set_metric(name: str, value: Any):
    """Set a metric to a specific value."""
    global _metrics
    _metrics[name] = value

def reset_metrics():
    """Reset all metrics to default values."""
    global _metrics, _start_time
    _metrics = {
        'webhooks_sent': 0,
        'webhooks_failed': 0,
        'webhook_success_rate': 0.0,
        'avg_response_time': 0,
        'uptime_seconds': 0
    }
    _start_time = time.time()
