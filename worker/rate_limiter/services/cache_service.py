
#!/usr/bin/env python3
"""
Rate Limiter - Cache Service

This service manages Redis-based caching operations for high-performance rate limiting:
- Real-time usage counters with atomic operations
- Distributed rate limiting across multiple instances
- Cache warming and invalidation strategies
- Fallback mechanisms when Redis is unavailable
- Performance optimization with pipelining

The service provides Redis-backed counters for rate limiting while maintaining
data consistency and handling Redis failures gracefully.
"""

import logging
import json
import time
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timedelta
import redis
from redis.exceptions import RedisError, ConnectionError

from config import config

logger = logging.getLogger(__name__)

class RateLimitCacheService:
    """
    Redis-based cache service for rate limiting operations.
    
    This service provides high-performance Redis operations for rate limiting
    including atomic counter operations, distributed locking, and cache
    management with proper error handling and fallback strategies.
    """
    
    def __init__(self):
        """Initialize the cache service with Redis connection and configuration"""
        self.config = config
        self.redis_client = self._init_redis()
        
        # Cache configuration with spam prevention windows
        self.counter_ttl = {
            'second': 2,       # 1 second + 1 second buffer for anti-spam
            'minute': 70,      # 1 minute + 10 seconds buffer for burst protection
            'hourly': 3700,    # 1 hour + 100 seconds buffer
            'daily': 86500,    # 24 hours + 100 seconds buffer  
            'monthly': 2678500 # 31 days + 100 seconds buffer
        }
        
        # Performance tracking
        self.cache_hits = 0
        self.cache_misses = 0
        self.redis_errors = 0
        
        logger.info("Rate limit cache service initialized")
    
    def _init_redis(self) -> Optional[redis.Redis]:
        """
        Initialize Redis connection with connection pooling and error handling.
        
        Returns:
            Redis client or None if connection fails
        """
        try:
            redis_kwargs = self.config.get_redis_connection_kwargs()
            
            # Add connection pool configuration for better performance
            redis_kwargs.update({
                'max_connections': self.config.cache.redis_pool_size,
                'retry_on_timeout': True,
                'socket_keepalive': True,
                'socket_keepalive_options': {},
                'health_check_interval': 30
            })
            
            redis_client = redis.Redis(
                connection_pool=redis.ConnectionPool(**redis_kwargs)
            )
            
            # Test connection with ping
            redis_client.ping()
            logger.info("Redis cache connection established successfully")
            return redis_client
            
        except Exception as e:
            logger.error(f"Failed to initialize Redis cache: {e}")
            return None
    
    def increment_counter(self, entity_type: str, identifier: str, direction: str, 
                         amount: int = 1) -> Dict[str, int]:
        """
        Atomically increment usage counters for an entity.
        
        This method uses Redis pipeline for atomic operations across multiple
        time windows to ensure consistency and prevent race conditions.
        
        Args:
            entity_type: 'organization', 'domain', or 'mailbox'
            identifier: Entity identifier
            direction: 'inbound' or 'outbound'
            amount: Number to increment (default: 1)
            
        Returns:
            Dict containing current counts for all time windows
        """
        if not self.redis_client:
            logger.warning("Redis unavailable, cannot increment counters")
            return {'hourly_count': 0, 'daily_count': 0, 'monthly_count': 0}
        
        try:
            now = datetime.now()
            
            # Generate time-based keys for different windows
            second_key = f"rate:{entity_type}:{identifier}:{direction}:second:{now.strftime('%Y-%m-%d-%H-%M-%S')}"
            minute_key = f"rate:{entity_type}:{identifier}:{direction}:minute:{now.strftime('%Y-%m-%d-%H-%M')}"
            hour_key = f"rate:{entity_type}:{identifier}:{direction}:hour:{now.strftime('%Y-%m-%d-%H')}"
            day_key = f"rate:{entity_type}:{identifier}:{direction}:day:{now.strftime('%Y-%m-%d')}"
            month_key = f"rate:{entity_type}:{identifier}:{direction}:month:{now.strftime('%Y-%m')}"
            
            # Use pipeline for atomic operations
            pipe = self.redis_client.pipeline()
            
            # Increment all counters atomically (spam prevention first)
            pipe.incrby(second_key, amount)
            pipe.expire(second_key, self.counter_ttl['second'])
            pipe.incrby(minute_key, amount)
            pipe.expire(minute_key, self.counter_ttl['minute'])
            pipe.incrby(hour_key, amount)
            pipe.expire(hour_key, self.counter_ttl['hourly'])
            pipe.incrby(day_key, amount)
            pipe.expire(day_key, self.counter_ttl['daily'])
            pipe.incrby(month_key, amount)
            pipe.expire(month_key, self.counter_ttl['monthly'])
            
            # Execute pipeline
            results = pipe.execute()
            
            # Extract current counts
            current_counts = {
                'second_count': results[0],
                'minute_count': results[2],
                'hourly_count': results[4],
                'daily_count': results[6],
                'monthly_count': results[8]
            }
            
            logger.debug(f"Incremented counters for {entity_type}:{identifier}:{direction} by {amount}")
            return current_counts
            
        except RedisError as e:
            self.redis_errors += 1
            logger.error(f"Redis error incrementing counters: {e}")
            return {'hourly_count': 0, 'daily_count': 0, 'monthly_count': 0}
        except Exception as e:
            logger.error(f"Unexpected error incrementing counters: {e}")
            return {'hourly_count': 0, 'daily_count': 0, 'monthly_count': 0}
    
    def get_current_counts(self, entity_type: str, identifier: str, 
                          direction: str) -> Dict[str, int]:
        """
        Get current usage counts for an entity across all time windows.
        
        Args:
            entity_type: 'organization', 'domain', or 'mailbox'
            identifier: Entity identifier
            direction: 'inbound' or 'outbound'
            
        Returns:
            Dict containing current counts for all time windows
        """
        if not self.redis_client:
            logger.warning("Redis unavailable, returning zero counts")
            self.cache_misses += 1
            return {'hourly_count': 0, 'daily_count': 0, 'monthly_count': 0}
        
        try:
            now = datetime.now()
            
            # Generate time-based keys
            second_key = f"rate:{entity_type}:{identifier}:{direction}:second:{now.strftime('%Y-%m-%d-%H-%M-%S')}"
            minute_key = f"rate:{entity_type}:{identifier}:{direction}:minute:{now.strftime('%Y-%m-%d-%H-%M')}"
            hour_key = f"rate:{entity_type}:{identifier}:{direction}:hour:{now.strftime('%Y-%m-%d-%H')}"
            day_key = f"rate:{entity_type}:{identifier}:{direction}:day:{now.strftime('%Y-%m-%d')}"
            month_key = f"rate:{entity_type}:{identifier}:{direction}:month:{now.strftime('%Y-%m')}"
            
            # Get all counts in a single pipeline operation
            pipe = self.redis_client.pipeline()
            pipe.get(second_key)
            pipe.get(minute_key)
            pipe.get(hour_key)
            pipe.get(day_key)
            pipe.get(month_key)
            
            results = pipe.execute()
            
            # Convert results to integers (Redis returns bytes)
            counts = {
                'second_count': int(results[0]) if results[0] else 0,
                'minute_count': int(results[1]) if results[1] else 0,
                'hourly_count': int(results[2]) if results[2] else 0,
                'daily_count': int(results[3]) if results[3] else 0,
                'monthly_count': int(results[4]) if results[4] else 0
            }
            
            self.cache_hits += 1
            logger.debug(f"Retrieved counts for {entity_type}:{identifier}:{direction}: {counts}")
            return counts
            
        except RedisError as e:
            self.redis_errors += 1
            self.cache_misses += 1
            logger.error(f"Redis error getting counts: {e}")
            return {'hourly_count': 0, 'daily_count': 0, 'monthly_count': 0}
        except Exception as e:
            self.cache_misses += 1
            logger.error(f"Unexpected error getting counts: {e}")
            return {'hourly_count': 0, 'daily_count': 0, 'monthly_count': 0}
    
    def get_bulk_counts(self, entities: List[Tuple[str, str, str]]) -> Dict[str, Dict[str, int]]:
        """
        Get current counts for multiple entities in a single operation.
        
        Args:
            entities: List of (entity_type, identifier, direction) tuples
            
        Returns:
            Dict mapping entity keys to their current counts
        """
        if not self.redis_client or not entities:
            return {}
        
        try:
            now = datetime.now()
            pipe = self.redis_client.pipeline()
            
            # Build all keys and execute gets
            entity_keys = []
            for entity_type, identifier, direction in entities:
                entity_key = f"{entity_type}:{identifier}:{direction}"
                entity_keys.append(entity_key)
                
                hour_key = f"rate:{entity_type}:{identifier}:{direction}:hour:{now.strftime('%Y-%m-%d-%H')}"
                day_key = f"rate:{entity_type}:{identifier}:{direction}:day:{now.strftime('%Y-%m-%d')}"
                month_key = f"rate:{entity_type}:{identifier}:{direction}:month:{now.strftime('%Y-%m')}"
                
                pipe.get(hour_key)
                pipe.get(day_key)
                pipe.get(month_key)
            
            results = pipe.execute()
            
            # Parse results into entity-specific counts
            bulk_counts = {}
            for i, entity_key in enumerate(entity_keys):
                start_idx = i * 3
                bulk_counts[entity_key] = {
                    'hourly_count': int(results[start_idx]) if results[start_idx] else 0,
                    'daily_count': int(results[start_idx + 1]) if results[start_idx + 1] else 0,
                    'monthly_count': int(results[start_idx + 2]) if results[start_idx + 2] else 0
                }
            
            logger.debug(f"Retrieved bulk counts for {len(entities)} entities")
            return bulk_counts
            
        except Exception as e:
            logger.error(f"Error getting bulk counts: {e}")
            return {}
    
    def reset_counters(self, entity_type: str, identifier: str, direction: str) -> bool:
        """
        Reset all usage counters for an entity.
        
        Args:
            entity_type: 'organization', 'domain', or 'mailbox'
            identifier: Entity identifier
            direction: 'inbound' or 'outbound'
            
        Returns:
            bool: True if successful, False otherwise
        """
        if not self.redis_client:
            logger.warning("Redis unavailable, cannot reset counters")
            return False
        
        try:
            now = datetime.now()
            
            # Generate keys for current time windows
            patterns = [
                f"rate:{entity_type}:{identifier}:{direction}:hour:*",
                f"rate:{entity_type}:{identifier}:{direction}:day:*",
                f"rate:{entity_type}:{identifier}:{direction}:month:*"
            ]
            
            deleted_count = 0
            for pattern in patterns:
                keys = self.redis_client.keys(pattern)
                if keys:
                    deleted_count += self.redis_client.delete(*keys)
            
            logger.info(f"Reset {deleted_count} counters for {entity_type}:{identifier}:{direction}")
            return True
            
        except Exception as e:
            logger.error(f"Error resetting counters: {e}")
            return False
    
    def set_rate_limit_cache(self, cache_key: str, value: Any, ttl: int = None) -> bool:
        """
        Set a value in the rate limit cache with optional TTL.
        
        Args:
            cache_key: Cache key
            value: Value to cache (will be JSON encoded)
            ttl: Time to live in seconds
            
        Returns:
            bool: True if successful, False otherwise
        """
        if not self.redis_client:
            return False
        
        try:
            serialized_value = json.dumps(value, default=str)
            
            if ttl:
                self.redis_client.setex(cache_key, ttl, serialized_value)
            else:
                self.redis_client.set(cache_key, serialized_value)
            
            logger.debug(f"Cached value for key: {cache_key}")
            return True
            
        except Exception as e:
            logger.error(f"Error setting cache: {e}")
            return False
    
    def get_rate_limit_cache(self, cache_key: str) -> Optional[Any]:
        """
        Get a value from the rate limit cache.
        
        Args:
            cache_key: Cache key
            
        Returns:
            Cached value or None if not found
        """
        if not self.redis_client:
            return None
        
        try:
            cached_value = self.redis_client.get(cache_key)
            if cached_value:
                self.cache_hits += 1
                return json.loads(cached_value)
            else:
                self.cache_misses += 1
                return None
                
        except Exception as e:
            self.cache_misses += 1
            logger.error(f"Error getting cache: {e}")
            return None
    
    def delete_cache_key(self, cache_key: str) -> bool:
        """
        Delete a key from the cache.
        
        Args:
            cache_key: Cache key to delete
            
        Returns:
            bool: True if key was deleted, False otherwise
        """
        if not self.redis_client:
            return False
        
        try:
            deleted = self.redis_client.delete(cache_key)
            logger.debug(f"Deleted cache key: {cache_key}, result: {deleted}")
            return deleted > 0
            
        except Exception as e:
            logger.error(f"Error deleting cache key: {e}")
            return False
    
    def cleanup_expired_counters(self) -> int:
        """
        Clean up expired rate limit counters.
        
        This method removes old counter keys that may not have been
        automatically expired due to Redis configuration issues.
        
        Returns:
            int: Number of keys cleaned up
        """
        if not self.redis_client:
            return 0
        
        try:
            # Find old counter keys
            now = datetime.now()
            cutoff_time = now - timedelta(days=32)  # Older than monthly window
            
            patterns = [
                "rate:*:hour:*",
                "rate:*:day:*", 
                "rate:*:month:*"
            ]
            
            deleted_count = 0
            for pattern in patterns:
                keys = self.redis_client.keys(pattern)
                
                for key in keys:
                    # Extract timestamp from key and check if expired
                    try:
                        key_str = key.decode('utf-8') if isinstance(key, bytes) else key
                        parts = key_str.split(':')
                        
                        if len(parts) >= 6:
                            time_part = parts[-1]  # Last part should be timestamp
                            
                            # Parse different time formats
                            if len(time_part) == 13:  # YYYY-MM-DD-HH
                                key_time = datetime.strptime(time_part, '%Y-%m-%d-%H')
                            elif len(time_part) == 10:  # YYYY-MM-DD
                                key_time = datetime.strptime(time_part, '%Y-%m-%d')
                            elif len(time_part) == 7:   # YYYY-MM
                                key_time = datetime.strptime(time_part, '%Y-%m')
                            else:
                                continue
                            
                            # Delete if older than cutoff
                            if key_time < cutoff_time:
                                self.redis_client.delete(key)
                                deleted_count += 1
                                
                    except Exception as e:
                        logger.debug(f"Could not parse timestamp from key {key}: {e}")
                        continue
            
            if deleted_count > 0:
                logger.info(f"Cleaned up {deleted_count} expired counter keys")
            
            return deleted_count
            
        except Exception as e:
            logger.error(f"Error during counter cleanup: {e}")
            return 0
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get cache service statistics and health information.
        
        Returns:
            Dict containing cache statistics
        """
        stats = {
            'service': 'rate_limit_cache',
            'redis_available': self.redis_client is not None,
            'cache_hits': self.cache_hits,
            'cache_misses': self.cache_misses,
            'redis_errors': self.redis_errors,
            'hit_ratio': self.cache_hits / (self.cache_hits + self.cache_misses) * 100 
                        if (self.cache_hits + self.cache_misses) > 0 else 0
        }
        
        # Get Redis info if available
        if self.redis_client:
            try:
                redis_info = self.redis_client.info()
                stats['redis_info'] = {
                    'connected_clients': redis_info.get('connected_clients', 0),
                    'used_memory': redis_info.get('used_memory', 0),
                    'used_memory_human': redis_info.get('used_memory_human', '0B'),
                    'keyspace_hits': redis_info.get('keyspace_hits', 0),
                    'keyspace_misses': redis_info.get('keyspace_misses', 0),
                    'total_commands_processed': redis_info.get('total_commands_processed', 0),
                    'uptime_in_seconds': redis_info.get('uptime_in_seconds', 0)
                }
                
                # Calculate Redis hit ratio
                redis_hits = redis_info.get('keyspace_hits', 0)
                redis_misses = redis_info.get('keyspace_misses', 0)
                if redis_hits + redis_misses > 0:
                    stats['redis_info']['hit_ratio'] = redis_hits / (redis_hits + redis_misses) * 100
                
            except Exception as e:
                logger.warning(f"Could not get Redis info: {e}")
                stats['redis_info'] = {'error': str(e)}
        
        return stats
    
    def health_check(self) -> Dict[str, Any]:
        """
        Perform health check on the cache service.
        
        Returns:
            Dict containing health check results
        """
        health = {
            'service': 'rate_limit_cache',
            'status': 'healthy',
            'checks': {}
        }
        
        # Check Redis connectivity
        if self.redis_client:
            try:
                start_time = time.time()
                self.redis_client.ping()
                response_time = (time.time() - start_time) * 1000
                
                health['checks']['redis'] = {
                    'status': 'healthy',
                    'response_time_ms': round(response_time, 2)
                }
                
            except Exception as e:
                health['status'] = 'degraded'
                health['checks']['redis'] = {
                    'status': 'unhealthy',
                    'error': str(e)
                }
        else:
            health['status'] = 'degraded'
            health['checks']['redis'] = {
                'status': 'unavailable',
                'error': 'Redis client not initialized'
            }
        
        return health
