
#!/usr/bin/env python3
"""
Email Tracking Service - Core Tracking Logic

This service handles the core tracking functionality including:
- Tracking ID generation and validation
- Tracking pixel creation and injection
- Link rewriting for click tracking
- Organization-specific configuration management

All tracking operations are performed through this centralized service
to ensure consistency and maintainability.
"""

import os
import json
import hashlib
import hmac
import secrets
import base64
import time
import re
from datetime import datetime
from urllib.parse import quote, unquote, urlparse, parse_qs, urlencode
from typing import Dict, List, Optional, Any
import logging

from config import config_manager

logger = logging.getLogger(__name__)

class TrackingService:
    """
    Core tracking service for email tracking functionality.
    Handles organization-based tracking with configurable settings.
    """
    
    def __init__(self):
        """Initialize tracking service with configuration."""
        self.config = config_manager
        logger.info("Tracking service initialized")
    
    def generate_tracking_id(self, email_data: Dict[str, Any], organization_id: str) -> str:
        """
        Generate unique tracking ID for email.
        
        Args:
            email_data: Email metadata
            organization_id: Organization identifier
            
        Returns:
            Unique tracking ID
        """
        try:
            # Create unique data string
            data_string = f"{email_data.get('message_id', '')}{organization_id}{datetime.utcnow().isoformat()}{secrets.token_hex(8)}"
            
            # Generate hash
            tracking_id = hashlib.sha256(data_string.encode()).hexdigest()[:32]
            
            logger.debug(f"Generated tracking ID: {tracking_id}")
            return tracking_id
            
        except Exception as e:
            logger.error(f"Failed to generate tracking ID: {e}")
            return secrets.token_hex(16)
    
    def create_tracking_pixel_url(self, tracking_id: str, organization_id: str) -> str:
        """
        Create tracking pixel URL.
        
        Args:
            tracking_id: Unique tracking identifier
            organization_id: Organization identifier
            
        Returns:
            Tracking pixel URL
        """
        tracking_config = self.config.get_organization_config(organization_id)
        domain = tracking_config.get('tracking_domain', os.getenv('TRACKING_DOMAIN', 'yourdomain.com'))
        subdomain = tracking_config.get('tracking_subdomain', os.getenv('TRACKING_SUBDOMAIN', 'track'))
        protocol = tracking_config.get('tracking_protocol', os.getenv('TRACKING_PROTOCOL', 'https'))
        
        return f"{protocol}://{subdomain}.{domain}/track/open/{tracking_id}"
    
    def create_click_tracking_url(self, original_url: str, tracking_id: str, organization_id: str) -> str:
        """
        Create click tracking URL.
        
        Args:
            original_url: Original URL to track
            tracking_id: Unique tracking identifier
            organization_id: Organization identifier
            
        Returns:
            Click tracking URL
        """
        tracking_config = self.config.get_organization_config(organization_id)
        domain = tracking_config.get('tracking_domain', os.getenv('TRACKING_DOMAIN', 'yourdomain.com'))
        subdomain = tracking_config.get('tracking_subdomain', os.getenv('TRACKING_SUBDOMAIN', 'track'))
        protocol = tracking_config.get('tracking_protocol', os.getenv('TRACKING_PROTOCOL', 'https'))
        
        encoded_url = quote(original_url, safe='')
        return f"{protocol}://{subdomain}.{domain}/track/click/{tracking_id}?url={encoded_url}"
    
    def inject_tracking_into_html(self, html_content: str, tracking_data: Dict[str, Any]) -> str:
        """
        Inject tracking pixel and modify links in HTML content.
        
        Args:
            html_content: Original HTML content
            tracking_data: Tracking information
            
        Returns:
            Modified HTML with tracking
        """
        try:
            from bs4 import BeautifulSoup
            
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Inject tracking pixel
            if 'pixel_url' in tracking_data:
                tracking_pixel = soup.new_tag(
                    'img', 
                    src=tracking_data['pixel_url'],
                    width="1", 
                    height="1", 
                    style="display:none; border:0;"
                )
                
                if soup.body:
                    soup.body.append(tracking_pixel)
                else:
                    soup.append(tracking_pixel)
            
            # Modify links for click tracking
            organization_id = tracking_data.get('organization_id')
            tracking_id = tracking_data.get('tracking_id')
            
            if organization_id and tracking_id:
                for link in soup.find_all('a', href=True):
                    original_url = link['href']
                    
                    # Skip if already a tracking URL or special URLs
                    if self._should_skip_url(original_url, organization_id):
                        continue
                    
                    # Create tracking URL
                    tracking_url = self.create_click_tracking_url(original_url, tracking_id, organization_id)
                    link['href'] = tracking_url
                    
                    # Store original URL for reference
                    if 'click_urls' not in tracking_data:
                        tracking_data['click_urls'] = []
                    
                    tracking_data['click_urls'].append({
                        'original_url': original_url,
                        'tracking_url': tracking_url
                    })
            
            return str(soup)
            
        except ImportError:
            logger.warning("BeautifulSoup not available, basic tracking injection")
            # Basic tracking pixel injection
            if 'pixel_url' in tracking_data:
                pixel_tag = f'<img src="{tracking_data["pixel_url"]}" width="1" height="1" style="display:none; border:0;">'
                return html_content + pixel_tag
            return html_content
            
        except Exception as e:
            logger.error(f"Failed to inject HTML tracking: {e}")
            return html_content
    
    def _should_skip_url(self, url: str, organization_id: str) -> bool:
        """
        Check if URL should be skipped for tracking.
        
        Args:
            url: URL to check
            organization_id: Organization identifier
            
        Returns:
            True if URL should be skipped
        """
        # Skip tracking domain URLs
        tracking_config = self.config.get_organization_config(organization_id)
        tracking_domain = tracking_config.get('tracking_domain', os.getenv('TRACKING_DOMAIN', 'yourdomain.com'))
        
        if tracking_domain in url:
            return True
        
        # Skip special protocols
        skip_patterns = ['mailto:', 'tel:', 'ftp:', 'file:']
        for pattern in skip_patterns:
            if url.startswith(pattern):
                return True
        
        # Skip localhost and internal URLs
        skip_domains = ['localhost', '127.0.0.1']
        for domain in skip_domains:
            if domain in url:
                return True
        
        return False
    
    def validate_tracking_id(self, tracking_id: str) -> bool:
        """
        Validate tracking ID format.
        
        Args:
            tracking_id: Tracking ID to validate
            
        Returns:
            True if valid
        """
        if not tracking_id:
            return False
        
        # Check length and format
        if len(tracking_id) != 32:
            return False
        
        # Check if hexadecimal
        try:
            int(tracking_id, 16)
            return True
        except ValueError:
            return False

class TrackingService:
    """
    Core tracking service that handles all tracking-related operations.
    
    This service is responsible for:
    - Generating secure tracking IDs
    - Creating tracking pixels and click tracking URLs
    - Injecting tracking into email content
    - Managing tenant-specific configurations
    """
    
    def __init__(self):
        """Initialize the tracking service with configuration."""
        self.config = config_manager.config
        logger.info("TrackingService initialized")
    
    def generate_tracking_id(self, email_id: str, recipient: str, tenant_id: str, domain_id: str) -> str:
        """
        Generate a unique, secure tracking ID for an email.
        
        The tracking ID contains the email metadata encrypted with HMAC
        to prevent tampering and ensure data integrity.
        
        Args:
            email_id: Unique identifier for the email
            recipient: Email address of the recipient
            tenant_id: Tenant/organization identifier
            domain_id: Domain identifier
            
        Returns:
            str: Base64-encoded tracking ID with signature
        """
        timestamp = str(int(time.time()))
        nonce = secrets.token_urlsafe(8)
        data = f"{email_id}:{recipient}:{tenant_id}:{domain_id}:{timestamp}:{nonce}"
        
        # Create HMAC signature for security
        secret = config_manager.get_webhook_config()['secret']
        signature = hmac.new(
            secret.encode('utf-8'),
            data.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()[:16]  # Use first 16 chars of signature
        
        # Encode with signature
        signed_data = f"{data}:{signature}"
        tracking_id = base64.urlsafe_b64encode(signed_data.encode()).decode().rstrip('=')
        
        logger.debug(f"Generated tracking ID for email {email_id}")
        return tracking_id
    
    def decode_tracking_id(self, tracking_id: str) -> Optional[Dict[str, str]]:
        """
        Decode and verify a tracking ID.
        
        Validates the tracking ID signature and extracts the metadata.
        
        Args:
            tracking_id: Base64-encoded tracking ID to decode
            
        Returns:
            Optional[Dict[str, str]]: Decoded tracking data or None if invalid
        """
        try:
            # Add padding if needed for base64 decoding
            missing_padding = len(tracking_id) % 4
            if missing_padding:
                tracking_id += '=' * (4 - missing_padding)
            
            decoded = base64.urlsafe_b64decode(tracking_id).decode()
            parts = decoded.split(':')
            
            if len(parts) < 7:  # email_id:recipient:tenant_id:domain_id:timestamp:nonce:signature
                logger.warning("Invalid tracking ID format - insufficient parts")
                return None
            
            # Verify signature
            data = ':'.join(parts[:-1])
            provided_signature = parts[-1]
            
            secret = config_manager.get_webhook_config()['secret']
            expected_signature = hmac.new(
                secret.encode('utf-8'),
                data.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()[:16]
            
            if not hmac.compare_digest(provided_signature, expected_signature):
                logger.warning("Invalid tracking ID signature")
                return None
            
            return {
                'email_id': parts[0],
                'recipient': parts[1],
                'tenant_id': parts[2],
                'domain_id': parts[3],
                'timestamp': parts[4],
                'nonce': parts[5]
            }
            
        except Exception as e:
            logger.error(f"Failed to decode tracking ID: {e}")
            return None
    
    def create_tracking_pixel_url(self, email_id: str, recipient: str, tenant_id: str, domain_id: str) -> str:
        """
        Create a tracking pixel URL for open tracking.
        
        Args:
            email_id: Unique identifier for the email
            recipient: Email address of the recipient
            tenant_id: Tenant/organization identifier
            domain_id: Domain identifier
            
        Returns:
            str: Complete URL for the tracking pixel
        """
        tracking_id = self.generate_tracking_id(email_id, recipient, tenant_id, domain_id)
        base_url = config_manager.get_tracking_url_base()
        return f"{base_url}/track/open/{tracking_id}"
    
    def create_click_tracking_url(self, original_url: str, email_id: str, recipient: str, tenant_id: str, domain_id: str) -> str:
        """
        Create a click tracking URL that preserves the original URL.
        
        Args:
            original_url: The original URL to be tracked
            email_id: Unique identifier for the email
            recipient: Email address of the recipient
            tenant_id: Tenant/organization identifier
            domain_id: Domain identifier
            
        Returns:
            str: Complete click tracking URL
        """
        if not config_manager.should_track_link(original_url):
            return original_url
        
        tracking_id = self.generate_tracking_id(email_id, recipient, tenant_id, domain_id)
        base_url = config_manager.get_tracking_url_base()
        
        # Safely encode the original URL
        encoded_url = quote(original_url, safe='')
        
        tracking_url = f"{base_url}/track/click/{tracking_id}?url={encoded_url}"
        
        # Preserve UTM parameters if configured
        if self.config.preserve_utm_params:
            parsed_url = urlparse(original_url)
            if parsed_url.query:
                query_params = parse_qs(parsed_url.query)
                utm_params = {k: v for k, v in query_params.items() if k.startswith('utm_')}
                if utm_params:
                    utm_encoded = urlencode(utm_params, doseq=True)
                    tracking_url += f"&{utm_encoded}"
        
        return tracking_url
    
    def inject_tracking_pixel(self, html_content: str, tracking_url: str) -> str:
        """
        Inject a tracking pixel into HTML email content.
        
        The pixel is injected at the optimal location in the HTML structure
        with fallback positions to ensure maximum compatibility.
        
        Args:
            html_content: Original HTML email content
            tracking_url: URL for the tracking pixel
            
        Returns:
            str: HTML content with tracking pixel injected
        """
        pixel_size = self.config.pixel_size.split('x')
        width, height = pixel_size[0], pixel_size[1] if len(pixel_size) > 1 else pixel_size[0]
        
        tracking_pixel = (
            f'<img src="{tracking_url}" '
            f'width="{width}" height="{height}" '
            f'style="display:none;visibility:hidden;opacity:0;width:0;height:0;border:none;outline:none;" '
            f'alt="" />'
        )
        
        # Try multiple injection points for maximum compatibility
        injection_points = [
            ('</body>', f'{tracking_pixel}</body>'),
            ('</html>', f'{tracking_pixel}</html>'),
            # Fallback: inject after first closing div
            ('</div>', lambda content: content.replace('</div>', f'{tracking_pixel}</div>', 1))
        ]
        
        for search_pattern, replacement in injection_points:
            if search_pattern in html_content:
                if callable(replacement):
                    html_content = replacement(html_content)
                else:
                    html_content = html_content.replace(search_pattern, replacement)
                logger.debug(f"Tracking pixel injected at {search_pattern}")
                return html_content
        
        # If no suitable injection point found, append to end
        html_content += tracking_pixel
        logger.debug("Tracking pixel appended to end of content")
        return html_content
    
    def rewrite_links_for_tracking(self, html_content: str, email_id: str, recipient: str, tenant_id: str, domain_id: str) -> tuple[str, int]:
        """
        Rewrite all trackable links in HTML content for click tracking.
        
        Args:
            html_content: Original HTML email content
            email_id: Unique identifier for the email
            recipient: Email address of the recipient
            tenant_id: Tenant/organization identifier
            domain_id: Domain identifier
            
        Returns:
            tuple[str, int]: Modified HTML content and number of links rewritten
        """
        links_rewritten = 0
        
        def replace_link(match):
            nonlocal links_rewritten
            original_url = match.group(1)
            
            # Skip URLs that shouldn't be tracked
            if not config_manager.should_track_link(original_url):
                return match.group(0)
            
            # Skip relative URLs and anchors
            if original_url.startswith('#') or original_url.startswith('/'):
                return match.group(0)
            
            # Skip if URL is already a tracking URL
            tracking_base = config_manager.get_tracking_url_base()
            if tracking_base in original_url:
                return match.group(0)
            
            tracking_url = self.create_click_tracking_url(original_url, email_id, recipient, tenant_id, domain_id)
            links_rewritten += 1
            return f'href="{tracking_url}"'
        
        # Enhanced regex patterns to handle various href formats
        patterns = [
            r'href="([^"]+)"',  # href="url"
            r"href='([^']+)'",  # href='url'
            r'href=([^\s>]+)'   # href=url (no quotes)
        ]
        
        for pattern in patterns:
            html_content = re.sub(pattern, replace_link, html_content)
        
        logger.debug(f"Rewritten {links_rewritten} links for tracking")
        return html_content, links_rewritten
    
    def get_tenant_config(self, tenant_id: str, domain_id: str = None) -> Dict[str, Any]:
        """
        Get tenant-specific tracking configuration.
        
        This method provides tenant-specific settings that can override
        global configuration. It supports:
        - Per-tenant tracking preferences
        - Domain-specific overrides
        - Feature flags per organization
        - Custom tracking parameters
        
        Args:
            tenant_id: Tenant/organization identifier
            domain_id: Optional domain identifier
            
        Returns:
            Dict[str, Any]: Tenant-specific configuration with defaults
        """
        try:
            # Get tenant-specific config from config manager
            tenant_config = config_manager.get_tenant_tracking_config(tenant_id, domain_id)
            
            # Provide sensible defaults if tenant config is not found
            default_config = {
                'open_tracking_enabled': True,
                'click_tracking_enabled': True,
                'track_device_info': True,
                'track_geolocation': False,  # Privacy-conscious default
                'track_user_agent': True,
                'track_ip_address': True,
                'track_referrer': True,
                'webhook_enabled': True,
                'rate_limit_enabled': True,
                'custom_domain_enabled': False,
                'data_retention_days': 365
            }
            
            # Merge tenant config with defaults
            final_config = {**default_config, **tenant_config}
            
            logger.debug(f"Retrieved tenant config for {tenant_id}/{domain_id}: {final_config}")
            return final_config
            
        except Exception as e:
            logger.error(f"Error getting tenant config for {tenant_id}: {e}")
            # Return safe defaults on error
            return {
                'open_tracking_enabled': True,
                'click_tracking_enabled': True,
                'track_device_info': False,
                'track_geolocation': False,
                'track_user_agent': False,
                'track_ip_address': False,
                'track_referrer': False,
                'webhook_enabled': False,
                'rate_limit_enabled': True,
                'custom_domain_enabled': False,
                'data_retention_days': 30
            }
