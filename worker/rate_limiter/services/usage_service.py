
#!/usr/bin/env python3
"""
Rate Limiter - Usage Service

This service manages usage tracking and statistics for rate limiting:
- Real-time usage monitoring and aggregation
- Cross-service coordination between cache and database
- Usage analytics and trend analysis
- Quota management and threshold monitoring
- Performance-optimized usage operations

The service coordinates between Redis cache and database fallback to provide
reliable usage tracking with high performance and data consistency.
"""

import logging
import time
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class UsageStatistics:
    """
    Container for usage statistics with calculated metrics.
    
    This class provides a structured way to handle usage data
    with additional computed fields for monitoring and alerting.
    """
    # Basic usage counts
    hourly_count: int
    daily_count: int
    monthly_count: int
    
    # Metadata
    entity_type: str
    identifier: str
    direction: str
    
    # Computed fields
    hourly_limit: int = 0
    daily_limit: int = 0
    monthly_limit: int = 0
    
    # Percentages
    hourly_percentage: float = 0.0
    daily_percentage: float = 0.0
    monthly_percentage: float = 0.0
    
    # Timestamps
    first_request: Optional[datetime] = None
    last_request: Optional[datetime] = None
    last_updated: Optional[datetime] = None
    
    def calculate_percentages(self):
        """Calculate usage percentages based on limits"""
        if self.hourly_limit > 0:
            self.hourly_percentage = (self.hourly_count / self.hourly_limit) * 100
        if self.daily_limit > 0:
            self.daily_percentage = (self.daily_count / self.daily_limit) * 100
        if self.monthly_limit > 0:
            self.monthly_percentage = (self.monthly_count / self.monthly_limit) * 100
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses"""
        return {
            'entity_type': self.entity_type,
            'identifier': self.identifier,
            'direction': self.direction,
            'hourly_count': self.hourly_count,
            'daily_count': self.daily_count,
            'monthly_count': self.monthly_count,
            'hourly_limit': self.hourly_limit,
            'daily_limit': self.daily_limit,
            'monthly_limit': self.monthly_limit,
            'hourly_percentage': round(self.hourly_percentage, 2),
            'daily_percentage': round(self.daily_percentage, 2),
            'monthly_percentage': round(self.monthly_percentage, 2),
            'first_request': self.first_request.isoformat() if self.first_request else None,
            'last_request': self.last_request.isoformat() if self.last_request else None,
            'last_updated': self.last_updated.isoformat() if self.last_updated else None
        }

class RateLimitUsageService:
    """
    Service for managing usage tracking and statistics.
    
    This service coordinates between cache and database services to provide
    reliable usage tracking with optimal performance. It handles both 
    real-time operations and historical analysis.
    """
    
    def __init__(self, cache_service, database_service):
        """
        Initialize usage service with cache and database dependencies.
        
        Args:
            cache_service: RateLimitCacheService instance
            database_service: RateLimitDatabaseService instance
        """
        self.cache_service = cache_service
        self.database_service = database_service
        
        # Performance tracking
        self.operations_count = 0
        self.cache_hits = 0
        self.database_fallbacks = 0
        self.errors = 0
        
        logger.info("Rate limit usage service initialized")
    
    def get_current_usage(self, entity_type: str, identifier: str, direction: str) -> Dict[str, int]:
        """
        Get current usage counts for an entity with cache-first approach.
        
        This method first attempts to get usage from Redis cache for speed,
        then falls back to database if cache is unavailable.
        
        Args:
            entity_type: 'organization', 'domain', or 'mailbox'
            identifier: Entity identifier
            direction: 'inbound' or 'outbound'
            
        Returns:
            Dict containing current usage counts
        """
        self.operations_count += 1
        
        try:
            # Try cache first for best performance
            usage_counts = self.cache_service.get_current_counts(
                entity_type, identifier, direction
            )
            
            # Check if we got valid data from cache
            if (usage_counts and 
                any(count > 0 for count in usage_counts.values())):
                self.cache_hits += 1
                logger.debug(f"Cache hit for usage: {entity_type}:{identifier}:{direction}")
                return usage_counts
            
            # Fallback to database if cache is empty or unavailable
            logger.debug(f"Cache miss, falling back to database for {entity_type}:{identifier}:{direction}")
            self.database_fallbacks += 1
            
            usage_counts = self.database_service.get_usage_data(
                entity_type, identifier, direction
            )
            
            # Update cache with database data if we have a cache service
            if self.cache_service.redis_client and usage_counts:
                # Store in cache for future requests
                hour_key = datetime.now().strftime('%Y-%m-%d-%H')
                cache_key = f"rate:{entity_type}:{identifier}:{direction}:hour:{hour_key}"
                
                try:
                    self.cache_service.redis_client.setex(
                        cache_key, 3600, usage_counts.get('hourly_count', 0)
                    )
                except Exception as e:
                    logger.warning(f"Failed to warm cache: {e}")
            
            return usage_counts
            
        except Exception as e:
            self.errors += 1
            logger.error(f"Error getting current usage: {e}")
            return {'hourly_count': 0, 'daily_count': 0, 'monthly_count': 0}
    
    def increment_usage(self, entity_type: str, identifier: str, direction: str, 
                       amount: int = 1) -> bool:
        """
        Increment usage counters with dual-write to cache and database.
        
        This method updates both cache (for speed) and database (for persistence)
        to ensure data consistency and availability.
        
        Args:
            entity_type: 'organization', 'domain', or 'mailbox'
            identifier: Entity identifier
            direction: 'inbound' or 'outbound'
            amount: Amount to increment (default: 1)
            
        Returns:
            bool: True if increment was successful in at least one storage
        """
        self.operations_count += 1
        
        cache_success = False
        database_success = False
        
        try:
            # Attempt to increment in cache (primary storage)
            if self.cache_service.redis_client:
                try:
                    updated_counts = self.cache_service.increment_counter(
                        entity_type, identifier, direction, amount
                    )
                    
                    if updated_counts and any(count > 0 for count in updated_counts.values()):
                        cache_success = True
                        logger.debug(f"Cache increment successful for {entity_type}:{identifier}:{direction}")
                        
                        # Also store in database for persistence (async/background would be better)
                        self.database_service.store_usage_data(
                            entity_type, identifier, direction, updated_counts
                        )
                        
                except Exception as e:
                    logger.warning(f"Cache increment failed: {e}")
            
            # If cache failed, increment in database directly
            if not cache_success:
                logger.debug(f"Using database increment for {entity_type}:{identifier}:{direction}")
                self.database_fallbacks += 1
                
                updated_counts = self.database_service.increment_usage_data(
                    entity_type, identifier, direction, amount
                )
                
                if updated_counts and any(count > 0 for count in updated_counts.values()):
                    database_success = True
            else:
                database_success = True  # Cache success with background DB update
            
            success = cache_success or database_success
            
            if success:
                logger.debug(f"Usage incremented for {entity_type}:{identifier}:{direction} by {amount}")
            else:
                self.errors += 1
                logger.error(f"Failed to increment usage for {entity_type}:{identifier}:{direction}")
            
            return success
            
        except Exception as e:
            self.errors += 1
            logger.error(f"Error incrementing usage: {e}")
            return False
    
    def get_usage_statistics(self, entity_type: str, identifier: str, direction: str,
                           config_rule) -> UsageStatistics:
        """
        Get comprehensive usage statistics with computed metrics.
        
        Args:
            entity_type: 'organization', 'domain', or 'mailbox'
            identifier: Entity identifier
            direction: 'inbound' or 'outbound'
            config_rule: Rate limit configuration rule
            
        Returns:
            UsageStatistics: Comprehensive usage data with computed metrics
        """
        try:
            # Get current usage counts
            usage_counts = self.get_current_usage(entity_type, identifier, direction)
            
            # Create statistics object
            stats = UsageStatistics(
                entity_type=entity_type,
                identifier=identifier,
                direction=direction,
                hourly_count=usage_counts.get('hourly_count', 0),
                daily_count=usage_counts.get('daily_count', 0),
                monthly_count=usage_counts.get('monthly_count', 0),
                hourly_limit=config_rule.hourly_limit,
                daily_limit=config_rule.daily_limit,
                monthly_limit=config_rule.monthly_limit,
                last_updated=datetime.now()
            )
            
            # Calculate percentages
            stats.calculate_percentages()
            
            logger.debug(f"Generated usage statistics for {entity_type}:{identifier}:{direction}")
            return stats
            
        except Exception as e:
            logger.error(f"Error getting usage statistics: {e}")
            
            # Return empty statistics on error
            return UsageStatistics(
                entity_type=entity_type,
                identifier=identifier,
                direction=direction,
                hourly_count=0,
                daily_count=0,
                monthly_count=0
            )
    
    def get_bulk_usage_statistics(self, entities: List[Tuple[str, str, str]],
                                 config_service) -> Dict[str, UsageStatistics]:
        """
        Get usage statistics for multiple entities efficiently.
        
        Args:
            entities: List of (entity_type, identifier, direction) tuples
            config_service: Configuration service for rate limit rules
            
        Returns:
            Dict mapping entity keys to their usage statistics
        """
        try:
            # Get bulk counts from cache
            bulk_counts = self.cache_service.get_bulk_counts(entities)
            
            statistics = {}
            
            for entity_type, identifier, direction in entities:
                entity_key = f"{entity_type}:{identifier}:{direction}"
                
                try:
                    # Get configuration for this entity
                    config_rule = config_service.get_rate_limit_rule(
                        entity_type, identifier, direction
                    )
                    
                    # Get usage counts (from bulk or individual query)
                    if entity_key in bulk_counts:
                        usage_counts = bulk_counts[entity_key]
                    else:
                        usage_counts = self.get_current_usage(entity_type, identifier, direction)
                    
                    # Create statistics
                    stats = UsageStatistics(
                        entity_type=entity_type,
                        identifier=identifier,
                        direction=direction,
                        hourly_count=usage_counts.get('hourly_count', 0),
                        daily_count=usage_counts.get('daily_count', 0),
                        monthly_count=usage_counts.get('monthly_count', 0),
                        hourly_limit=config_rule.hourly_limit,
                        daily_limit=config_rule.daily_limit,
                        monthly_limit=config_rule.monthly_limit,
                        last_updated=datetime.now()
                    )
                    
                    stats.calculate_percentages()
                    statistics[entity_key] = stats
                    
                except Exception as e:
                    logger.warning(f"Error processing entity {entity_key}: {e}")
                    continue
            
            logger.debug(f"Generated bulk statistics for {len(statistics)} entities")
            return statistics
            
        except Exception as e:
            logger.error(f"Error getting bulk usage statistics: {e}")
            return {}
    
    def get_usage_trends(self, entity_type: str, identifier: str, direction: str,
                        days: int = 7) -> Dict[str, Any]:
        """
        Get usage trends and analytics for an entity.
        
        Args:
            entity_type: 'organization', 'domain', or 'mailbox'
            identifier: Entity identifier
            direction: 'inbound' or 'outbound'
            days: Number of days of history to analyze
            
        Returns:
            Dict containing trend analysis and metrics
        """
        try:
            # Get historical usage data
            history = self.database_service.get_usage_history(
                entity_type, identifier, direction, days
            )
            
            if not history:
                return {
                    'entity_type': entity_type,
                    'identifier': identifier,
                    'direction': direction,
                    'days_analyzed': 0,
                    'total_usage': 0,
                    'average_daily': 0,
                    'peak_daily': 0,
                    'trend': 'no_data'
                }
            
            # Calculate trend metrics
            daily_totals = {}
            for record in history:
                day = record['day_key']
                if day not in daily_totals:
                    daily_totals[day] = 0
                daily_totals[day] += record['daily_count']
            
            daily_values = list(daily_totals.values())
            total_usage = sum(daily_values)
            average_daily = total_usage / len(daily_values) if daily_values else 0
            peak_daily = max(daily_values) if daily_values else 0
            
            # Simple trend calculation (comparing first half vs second half)
            trend = 'stable'
            if len(daily_values) >= 4:
                mid_point = len(daily_values) // 2
                first_half_avg = sum(daily_values[:mid_point]) / mid_point
                second_half_avg = sum(daily_values[mid_point:]) / (len(daily_values) - mid_point)
                
                if second_half_avg > first_half_avg * 1.2:
                    trend = 'increasing'
                elif second_half_avg < first_half_avg * 0.8:
                    trend = 'decreasing'
            
            trend_data = {
                'entity_type': entity_type,
                'identifier': identifier,
                'direction': direction,
                'days_analyzed': len(daily_values),
                'total_usage': total_usage,
                'average_daily': round(average_daily, 2),
                'peak_daily': peak_daily,
                'trend': trend,
                'daily_breakdown': daily_totals,
                'analysis_period': {
                    'start_date': min(daily_totals.keys()) if daily_totals else None,
                    'end_date': max(daily_totals.keys()) if daily_totals else None
                }
            }
            
            logger.debug(f"Generated usage trends for {entity_type}:{identifier}:{direction}")
            return trend_data
            
        except Exception as e:
            logger.error(f"Error getting usage trends: {e}")
            return {
                'entity_type': entity_type,
                'identifier': identifier,
                'direction': direction,
                'error': str(e)
            }
    
    def reset_usage_counters(self, entity_type: str, identifier: str, direction: str) -> bool:
        """
        Reset usage counters for an entity (admin function).
        
        Args:
            entity_type: 'organization', 'domain', or 'mailbox'
            identifier: Entity identifier
            direction: 'inbound' or 'outbound'
            
        Returns:
            bool: True if reset was successful
        """
        try:
            cache_reset = False
            database_reset = False
            
            # Reset cache counters
            if self.cache_service.redis_client:
                cache_reset = self.cache_service.reset_counters(
                    entity_type, identifier, direction
                )
            
            # Note: We don't delete database records as they serve as audit trail
            # Instead, we could mark them as reset or create a reset event
            
            logger.info(f"Reset usage counters for {entity_type}:{identifier}:{direction}")
            return cache_reset
            
        except Exception as e:
            logger.error(f"Error resetting usage counters: {e}")
            return False
    
    def get_quota_status(self, entity_type: str, identifier: str, direction: str,
                        config_rule) -> Dict[str, Any]:
        """
        Get quota status and remaining allowance for an entity.
        
        Args:
            entity_type: 'organization', 'domain', or 'mailbox'
            identifier: Entity identifier
            direction: 'inbound' or 'outbound'
            config_rule: Rate limit configuration rule
            
        Returns:
            Dict containing quota status and remaining allowances
        """
        try:
            # Get current usage
            usage_counts = self.get_current_usage(entity_type, identifier, direction)
            
            # Calculate remaining quotas
            hourly_remaining = max(0, config_rule.hourly_limit - usage_counts.get('hourly_count', 0))
            daily_remaining = max(0, config_rule.daily_limit - usage_counts.get('daily_count', 0))
            monthly_remaining = max(0, config_rule.monthly_limit - usage_counts.get('monthly_count', 0))
            
            # Calculate percentages
            hourly_used_pct = (usage_counts.get('hourly_count', 0) / config_rule.hourly_limit * 100) if config_rule.hourly_limit > 0 else 0
            daily_used_pct = (usage_counts.get('daily_count', 0) / config_rule.daily_limit * 100) if config_rule.daily_limit > 0 else 0
            monthly_used_pct = (usage_counts.get('monthly_count', 0) / config_rule.monthly_limit * 100) if config_rule.monthly_limit > 0 else 0
            
            # Determine overall quota status
            max_percentage = max(hourly_used_pct, daily_used_pct, monthly_used_pct)
            
            if max_percentage >= 100:
                quota_status = 'exceeded'
            elif max_percentage >= config_rule.critical_threshold:
                quota_status = 'critical'
            elif max_percentage >= config_rule.warning_threshold:
                quota_status = 'warning'
            else:
                quota_status = 'normal'
            
            return {
                'entity_type': entity_type,
                'identifier': identifier,
                'direction': direction,
                'quota_status': quota_status,
                'usage': {
                    'hourly': usage_counts.get('hourly_count', 0),
                    'daily': usage_counts.get('daily_count', 0),
                    'monthly': usage_counts.get('monthly_count', 0)
                },
                'limits': {
                    'hourly': config_rule.hourly_limit,
                    'daily': config_rule.daily_limit,
                    'monthly': config_rule.monthly_limit
                },
                'remaining': {
                    'hourly': hourly_remaining,
                    'daily': daily_remaining,
                    'monthly': monthly_remaining
                },
                'percentages': {
                    'hourly': round(hourly_used_pct, 2),
                    'daily': round(daily_used_pct, 2),
                    'monthly': round(monthly_used_pct, 2)
                },
                'thresholds': {
                    'warning': config_rule.warning_threshold,
                    'critical': config_rule.critical_threshold
                },
                'timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error getting quota status: {e}")
            return {
                'entity_type': entity_type,
                'identifier': identifier,
                'direction': direction,
                'quota_status': 'error',
                'error': str(e)
            }
    
    def get_usage_stats(self) -> Dict[str, Any]:
        """
        Get usage service statistics and performance metrics.
        
        Returns:
            Dict containing service statistics
        """
        return {
            'service': 'rate_limit_usage',
            'operations_count': self.operations_count,
            'cache_hits': self.cache_hits,
            'database_fallbacks': self.database_fallbacks,
            'errors': self.errors,
            'cache_hit_ratio': (self.cache_hits / self.operations_count * 100) 
                              if self.operations_count > 0 else 0,
            'error_rate': (self.errors / self.operations_count * 100) 
                         if self.operations_count > 0 else 0,
            'fallback_rate': (self.database_fallbacks / self.operations_count * 100) 
                            if self.operations_count > 0 else 0
        }
