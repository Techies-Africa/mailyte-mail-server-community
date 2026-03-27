
#!/usr/bin/env python3
"""
Rate Limiter - Alert Service

This service manages threshold monitoring and webhook-based alerting:
- Real-time threshold monitoring for rate limit breaches
- Webhook-based alert delivery with retry logic
- Alert deduplication and rate limiting
- Background monitoring threads
- Configurable alert levels and thresholds

The service monitors usage patterns and sends alerts via webhooks to external
systems when rate limits approach or exceed configured thresholds.
"""

import logging
import time
import threading
from typing import Dict, Any, Optional, List, Set
from datetime import datetime, timedelta
from dataclasses import dataclass
import json

logger = logging.getLogger(__name__)

@dataclass
class AlertThreshold:
    """
    Configuration for alert thresholds.
    
    This class defines when alerts should be triggered based on
    usage percentages and time windows.
    """
    warning_percentage: float = 80.0
    critical_percentage: float = 95.0
    exceeded_percentage: float = 100.0
    
    # Minimum time between duplicate alerts (seconds)
    dedup_window: int = 300  # 5 minutes
    
    # Alert priorities
    priority_map: Dict[str, int] = None
    
    def __post_init__(self):
        if self.priority_map is None:
            self.priority_map = {
                'warning': 3,
                'critical': 2,
                'exceeded': 1  # Highest priority
            }

class RateLimitAlertService:
    """
    Service for monitoring rate limit thresholds and sending webhook alerts.
    
    This service continuously monitors usage statistics and triggers alerts
    when entities approach or exceed their configured rate limits. All alerts
    are delivered via webhooks to external systems for handling.
    """
    
    def __init__(self, config_service, usage_service):
        """
        Initialize alert service with dependencies.
        
        Args:
            config_service: Configuration service for rate limit rules
            usage_service: Usage service for current statistics
        """
        self.config_service = config_service
        self.usage_service = usage_service
        
        # Alert configuration
        self.alert_thresholds = AlertThreshold()
        self.monitoring_enabled = True
        self.monitoring_interval = 60  # Check every minute
        
        # Alert deduplication tracking
        self.recent_alerts: Set[str] = set()
        self.alert_history: Dict[str, datetime] = {}
        
        # Performance tracking
        self.alerts_sent = 0
        self.alerts_failed = 0
        self.monitoring_cycles = 0
        self.last_monitoring_run = None
        
        # Background thread
        self.monitoring_thread = None
        self.stop_monitoring = threading.Event()
        
        logger.info("Rate limit alert service initialized")
    
    def start_monitoring(self):
        """
        Start background monitoring thread for threshold checking.
        
        This method starts a daemon thread that continuously monitors
        usage statistics and triggers alerts when thresholds are breached.
        """
        if self.monitoring_thread and self.monitoring_thread.is_alive():
            logger.warning("Alert monitoring already running")
            return
        
        self.stop_monitoring.clear()
        self.monitoring_thread = threading.Thread(
            target=self._monitoring_loop,
            name="rate_limit_alert_monitor",
            daemon=True
        )
        self.monitoring_thread.start()
        
        logger.info("Rate limit alert monitoring started")
    
    def stop_monitoring(self):
        """Stop background monitoring thread"""
        if self.monitoring_thread:
            self.stop_monitoring.set()
            self.monitoring_thread.join(timeout=10)
            logger.info("Rate limit alert monitoring stopped")
    
    def _monitoring_loop(self):
        """
        Main monitoring loop that runs in background thread.
        
        This loop continuously checks for threshold breaches and sends
        alerts via webhooks. It includes error handling and performance
        tracking to ensure reliable operation.
        """
        logger.info("Alert monitoring loop started")
        
        while not self.stop_monitoring.is_set():
            try:
                start_time = time.time()
                
                # Run monitoring cycle
                self._run_monitoring_cycle()
                
                # Update performance metrics
                self.monitoring_cycles += 1
                self.last_monitoring_run = datetime.now()
                
                # Log performance info
                cycle_time = time.time() - start_time
                logger.debug(f"Monitoring cycle completed in {cycle_time:.2f}s")
                
                # Wait for next cycle
                self.stop_monitoring.wait(self.monitoring_interval)
                
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                # Wait before retrying on error
                self.stop_monitoring.wait(30)
    
    def _run_monitoring_cycle(self):
        """
        Execute one monitoring cycle to check all active entities.
        
        This method retrieves active rate limit configurations and
        checks their current usage against thresholds.
        """
        try:
            # Get all active rate limit configurations
            # This would ideally be optimized to only check recently active entities
            active_configs = self.config_service.list_rate_limit_rules()
            
            if not active_configs:
                logger.debug("No active rate limit configurations to monitor")
                return
            
            entities_checked = 0
            alerts_triggered = 0
            
            for config_rule in active_configs:
                if not config_rule.active:
                    continue
                
                try:
                    # Check thresholds for this entity
                    alert_triggered = self.check_thresholds(
                        config_rule.entity_type,
                        config_rule.identifier, 
                        config_rule.identifier,  # Using identifier for both org and entity
                        config_rule.direction
                    )
                    
                    entities_checked += 1
                    if alert_triggered:
                        alerts_triggered += 1
                        
                except Exception as e:
                    logger.warning(f"Error checking entity {config_rule.entity_type}:"
                                 f"{config_rule.identifier}: {e}")
                    continue
            
            logger.debug(f"Monitoring cycle: checked {entities_checked} entities, "
                        f"triggered {alerts_triggered} alerts")
            
        except Exception as e:
            logger.error(f"Error in monitoring cycle: {e}")
    
    def check_thresholds(self, organization_id: str, domain: str, email: str, 
                        direction: str) -> bool:
        """
        Check rate limit thresholds for specific entity and trigger alerts.
        
        This method evaluates current usage against configured thresholds
        and sends webhook alerts when limits are approached or exceeded.
        
        Args:
            organization_id: Organization identifier
            domain: Domain name
            email: Email address (for mailbox-level checking)
            direction: 'inbound' or 'outbound'
            
        Returns:
            bool: True if any alerts were triggered
        """
        alerts_triggered = False
        
        try:
            # Check thresholds for all levels: organization, domain, mailbox
            entities_to_check = [
                ('organization', organization_id),
                ('domain', domain),
                ('mailbox', email)
            ]
            
            for entity_type, identifier in entities_to_check:
                try:
                    # Get configuration and usage for this entity
                    config_rule = self.config_service.get_rate_limit_rule(
                        entity_type, identifier, direction
                    )
                    
                    if not config_rule.active:
                        continue
                    
                    usage_stats = self.usage_service.get_usage_statistics(
                        entity_type, identifier, direction, config_rule
                    )
                    
                    # Check each time window for threshold breaches
                    time_windows = [
                        ('hourly', usage_stats.hourly_percentage, config_rule.hourly_limit),
                        ('daily', usage_stats.daily_percentage, config_rule.daily_limit),
                        ('monthly', usage_stats.monthly_percentage, config_rule.monthly_limit)
                    ]
                    
                    for window_type, usage_percentage, limit_value in time_windows:
                        if limit_value <= 0:  # Skip if no limit set
                            continue
                        
                        # Determine alert level based on percentage
                        alert_level = self._determine_alert_level(
                            usage_percentage, config_rule
                        )
                        
                        if alert_level:
                            # Check if we should send this alert (deduplication)
                            if self._should_send_alert(
                                entity_type, identifier, direction, alert_level, window_type
                            ):
                                # Send alert via webhook
                                alert_sent = self._send_threshold_alert(
                                    entity_type, identifier, direction, alert_level,
                                    usage_percentage, limit_value, window_type, usage_stats
                                )
                                
                                if alert_sent:
                                    alerts_triggered = True
                                    
                                    # Track alert for deduplication
                                    self._track_sent_alert(
                                        entity_type, identifier, direction, 
                                        alert_level, window_type
                                    )
                    
                except Exception as e:
                    logger.warning(f"Error checking thresholds for {entity_type}:"
                                 f"{identifier}: {e}")
                    continue
            
            return alerts_triggered
            
        except Exception as e:
            logger.error(f"Error in threshold checking: {e}")
            return False
    
    def _determine_alert_level(self, usage_percentage: float, config_rule) -> Optional[str]:
        """
        Determine alert level based on usage percentage and thresholds.
        
        Args:
            usage_percentage: Current usage percentage
            config_rule: Rate limit configuration
            
        Returns:
            Alert level string or None if no alert needed
        """
        if usage_percentage >= 100:
            return 'exceeded'
        elif usage_percentage >= config_rule.critical_threshold:
            return 'critical'
        elif usage_percentage >= config_rule.warning_threshold:
            return 'warning'
        else:
            return None
    
    def _should_send_alert(self, entity_type: str, identifier: str, direction: str,
                          alert_level: str, window_type: str) -> bool:
        """
        Check if alert should be sent based on deduplication rules.
        
        Args:
            entity_type: Entity type
            identifier: Entity identifier
            direction: Rate limit direction
            alert_level: Alert level
            window_type: Time window type
            
        Returns:
            bool: True if alert should be sent
        """
        # Create unique alert key for deduplication
        alert_key = f"{entity_type}:{identifier}:{direction}:{alert_level}:{window_type}"
        
        # Check if we've sent this alert recently
        if alert_key in self.alert_history:
            last_sent = self.alert_history[alert_key]
            time_since_last = datetime.now() - last_sent
            
            if time_since_last.total_seconds() < self.alert_thresholds.dedup_window:
                logger.debug(f"Skipping duplicate alert: {alert_key}")
                return False
        
        return True
    
    def _track_sent_alert(self, entity_type: str, identifier: str, direction: str,
                         alert_level: str, window_type: str):
        """
        Track sent alert for deduplication purposes.
        
        Args:
            entity_type: Entity type
            identifier: Entity identifier  
            direction: Rate limit direction
            alert_level: Alert level
            window_type: Time window type
        """
        alert_key = f"{entity_type}:{identifier}:{direction}:{alert_level}:{window_type}"
        self.alert_history[alert_key] = datetime.now()
        self.recent_alerts.add(alert_key)
        
        # Clean up old alerts to prevent memory leaks
        self._cleanup_old_alerts()
    
    def _cleanup_old_alerts(self):
        """Clean up old alert history entries to prevent memory growth"""
        cutoff_time = datetime.now() - timedelta(seconds=self.alert_thresholds.dedup_window * 2)
        
        # Remove old entries
        keys_to_remove = [
            key for key, timestamp in self.alert_history.items()
            if timestamp < cutoff_time
        ]
        
        for key in keys_to_remove:
            self.alert_history.pop(key, None)
            self.recent_alerts.discard(key)
    
    def _send_threshold_alert(self, entity_type: str, identifier: str, direction: str,
                             alert_level: str, usage_percentage: float, limit_value: int,
                             window_type: str, usage_stats) -> bool:
        """
        Send threshold alert via webhook.
        
        Args:
            entity_type: Entity type
            identifier: Entity identifier
            direction: Rate limit direction
            alert_level: Alert level (warning/critical/exceeded)
            usage_percentage: Current usage percentage
            limit_value: The limit value
            window_type: Time window type
            usage_stats: Complete usage statistics
            
        Returns:
            bool: True if alert was sent successfully
        """
        try:
            # Create alert payload
            alert_payload = {
                'event_type': 'rate_limit.threshold_breach',
                'alert_level': alert_level,
                'entity': {
                    'type': entity_type,
                    'identifier': identifier,
                    'direction': direction
                },
                'threshold': {
                    'window_type': window_type,
                    'usage_percentage': round(usage_percentage, 2),
                    'limit_value': limit_value,
                    'current_usage': getattr(usage_stats, f'{window_type}_count', 0)
                },
                'usage_statistics': usage_stats.to_dict(),
                'timestamp': datetime.now().isoformat(),
                'priority': self.alert_thresholds.priority_map.get(alert_level, 5),
                'service': 'rate_limiter'
            }
            
            # Store alert in database for webhook processing
            # The webhook service will pick up and deliver these alerts
            alert_stored = self.usage_service.database_service.create_alert_record(
                entity_type, identifier, direction, alert_level,
                getattr(usage_stats, f'{window_type}_count', 0),
                limit_value, window_type
            )
            
            if alert_stored:
                self.alerts_sent += 1
                logger.info(f"Sent {alert_level} alert for {entity_type}:{identifier} "
                           f"({usage_percentage:.1f}% of {window_type} limit)")
                return True
            else:
                self.alerts_failed += 1
                logger.error(f"Failed to store alert for {entity_type}:{identifier}")
                return False
                
        except Exception as e:
            self.alerts_failed += 1
            logger.error(f"Error sending threshold alert: {e}")
            return False
    
    def send_manual_alert(self, entity_type: str, identifier: str, direction: str,
                         message: str, alert_level: str = 'warning') -> bool:
        """
        Send manual alert for administrative purposes.
        
        Args:
            entity_type: Entity type
            identifier: Entity identifier
            direction: Rate limit direction
            message: Custom alert message
            alert_level: Alert level
            
        Returns:
            bool: True if alert was sent successfully
        """
        try:
            alert_payload = {
                'event_type': 'rate_limit.manual_alert',
                'alert_level': alert_level,
                'entity': {
                    'type': entity_type,
                    'identifier': identifier,
                    'direction': direction
                },
                'message': message,
                'timestamp': datetime.now().isoformat(),
                'priority': self.alert_thresholds.priority_map.get(alert_level, 5),
                'service': 'rate_limiter',
                'manual': True
            }
            
            # Store manual alert
            alert_stored = self.usage_service.database_service.create_alert_record(
                entity_type, identifier, direction, alert_level,
                0, 0, 'manual'
            )
            
            if alert_stored:
                logger.info(f"Sent manual {alert_level} alert for {entity_type}:{identifier}")
                return True
            else:
                logger.error(f"Failed to store manual alert for {entity_type}:{identifier}")
                return False
                
        except Exception as e:
            logger.error(f"Error sending manual alert: {e}")
            return False
    
    def cleanup_old_alerts(self, retention_days: int = 30) -> int:
        """
        Clean up old alert records from database.
        
        Args:
            retention_days: Number of days to retain alert records
            
        Returns:
            int: Number of records cleaned up
        """
        try:
            cutoff_date = datetime.now() - timedelta(days=retention_days)
            
            # This would need to be implemented in the database service
            # For now, we'll just clean up in-memory tracking
            old_alerts = [
                key for key, timestamp in self.alert_history.items()
                if timestamp < cutoff_date
            ]
            
            for key in old_alerts:
                self.alert_history.pop(key, None)
                self.recent_alerts.discard(key)
            
            if old_alerts:
                logger.info(f"Cleaned up {len(old_alerts)} old alert history entries")
            
            return len(old_alerts)
            
        except Exception as e:
            logger.error(f"Error cleaning up old alerts: {e}")
            return 0
    
    def get_alert_stats(self) -> Dict[str, Any]:
        """
        Get alert service statistics and performance metrics.
        
        Returns:
            Dict containing alert service statistics
        """
        return {
            'service': 'rate_limit_alerts',
            'monitoring_enabled': self.monitoring_enabled,
            'monitoring_interval': self.monitoring_interval,
            'monitoring_cycles': self.monitoring_cycles,
            'last_monitoring_run': self.last_monitoring_run.isoformat() 
                                  if self.last_monitoring_run else None,
            'alerts_sent': self.alerts_sent,
            'alerts_failed': self.alerts_failed,
            'recent_alerts_tracked': len(self.recent_alerts),
            'alert_history_size': len(self.alert_history),
            'thresholds': {
                'warning': self.alert_thresholds.warning_percentage,
                'critical': self.alert_thresholds.critical_percentage,
                'exceeded': self.alert_thresholds.exceeded_percentage,
                'dedup_window': self.alert_thresholds.dedup_window
            },
            'monitoring_thread_alive': (self.monitoring_thread.is_alive() 
                                       if self.monitoring_thread else False)
        }
