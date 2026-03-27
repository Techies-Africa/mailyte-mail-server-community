
#!/usr/bin/env python3
"""
Documentation SSL Certificate Manager

Automated SSL certificate management for the documentation site using Let's Encrypt.
Handles certificate generation, renewal, and nginx configuration updates.
"""

import os
import sys
import time
import logging
import subprocess
import requests
from datetime import datetime, timedelta
from pathlib import Path
import json

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/docs-ssl/ssl-manager.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class DocsSSLManager:
    """SSL Certificate Manager for documentation site"""
    
    def __init__(self):
        """Initialize SSL manager with configuration"""
        self.docs_domain = os.getenv('DOCS_DOMAIN', 'docs.mailyte.com')
        self.acme_email = os.getenv('ACME_EMAIL', 'admin@mailyte.com')
        self.staging = os.getenv('ACME_STAGING', 'false').lower() == 'true'
        self.webhook_url = os.getenv('WEBHOOK_URLS', '')
        self.webhook_secret = os.getenv('WEBHOOK_SECRET', '')
        
        # Certificate paths
        self.cert_path = f"/etc/letsencrypt/live/{self.docs_domain}"
        self.nginx_cert_path = "/etc/ssl/docs"
        
        # Renewal settings
        self.renewal_days = int(os.getenv('CERT_RENEWAL_DAYS', '30'))
        self.check_interval = int(os.getenv('CERT_CHECK_INTERVAL', '21600'))  # 6 hours
        
        # Ensure directories exist
        os.makedirs('/var/log/docs-ssl', exist_ok=True)
        os.makedirs(self.nginx_cert_path, exist_ok=True)
        
        logger.info(f"SSL Manager initialized for domain: {self.docs_domain}")
        logger.info(f"ACME email: {self.acme_email}")
        logger.info(f"Staging mode: {self.staging}")
    
    def check_certificate_exists(self):
        """Check if certificate exists and is valid"""
        cert_file = f"{self.cert_path}/fullchain.pem"
        
        if not os.path.exists(cert_file):
            logger.info(f"Certificate for {self.docs_domain} does not exist")
            return False
        
        try:
            # Check certificate expiration
            result = subprocess.run([
                'openssl', 'x509', '-in', cert_file, '-noout', '-enddate'
            ], capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                expiry_line = result.stdout.strip()
                if '=' in expiry_line:
                    expiry_str = expiry_line.split('=')[1]
                    try:
                        expiry_date = datetime.strptime(expiry_str, '%b %d %H:%M:%S %Y %Z')
                    except ValueError:
                        expiry_date = datetime.strptime(expiry_str, '%b %d %H:%M:%S %Y GMT')
                    
                    days_until_expiry = (expiry_date - datetime.now()).days
                    
                    if days_until_expiry <= self.renewal_days:
                        logger.info(f"Certificate expires in {days_until_expiry} days, needs renewal")
                        return False
                    else:
                        logger.info(f"Certificate valid for {days_until_expiry} more days")
                        return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error checking certificate: {e}")
            return False
    
    def obtain_certificate(self):
        """Obtain SSL certificate using Let's Encrypt"""
        try:
            logger.info(f"Obtaining certificate for {self.docs_domain}")
            
            # Build certbot command
            cmd = [
                'certbot', 'certonly',
                '--nginx',
                '--non-interactive',
                '--agree-tos',
                '--email', self.acme_email,
                '-d', self.docs_domain
            ]
            
            if self.staging:
                cmd.append('--staging')
                logger.info("Using Let's Encrypt staging environment")
            
            # Execute certbot command
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            
            if result.returncode == 0:
                logger.info(f"Successfully obtained certificate for {self.docs_domain}")
                
                # Copy certificates to nginx directory
                self._copy_certificates()
                
                # Update nginx configuration
                self._update_nginx_config()
                
                # Reload nginx
                self._reload_nginx()
                
                # Send webhook notification
                self._send_webhook_notification('certificate_obtained', True)
                
                return True
            else:
                error_msg = result.stderr or result.stdout
                logger.error(f"Failed to obtain certificate: {error_msg}")
                self._send_webhook_notification('certificate_failed', False, error_msg)
                return False
                
        except subprocess.TimeoutExpired:
            error_msg = f"Certificate generation timeout for {self.docs_domain}"
            logger.error(error_msg)
            return False
        except Exception as e:
            error_msg = f"Error obtaining certificate: {e}"
            logger.error(error_msg)
            return False
    
    def _copy_certificates(self):
        """Copy certificates to nginx directory"""
        try:
            # Copy fullchain certificate
            subprocess.run([
                'cp', f"{self.cert_path}/fullchain.pem", 
                f"{self.nginx_cert_path}/docs.crt"
            ], check=True)
            
            # Copy private key
            subprocess.run([
                'cp', f"{self.cert_path}/privkey.pem", 
                f"{self.nginx_cert_path}/docs.key"
            ], check=True)
            
            # Set proper permissions
            subprocess.run(['chmod', '644', f"{self.nginx_cert_path}/docs.crt"], check=True)
            subprocess.run(['chmod', '600', f"{self.nginx_cert_path}/docs.key"], check=True)
            
            logger.info("Certificates copied to nginx directory")
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to copy certificates: {e}")
            raise
    
    def _update_nginx_config(self):
        """Update nginx configuration for SSL"""
        config_content = f"""
server {{
    listen 80;
    server_name {self.docs_domain};
    return 301 https://$server_name$request_uri;
}}

server {{
    listen 443 ssl http2;
    server_name {self.docs_domain};
    
    ssl_certificate {self.nginx_cert_path}/docs.crt;
    ssl_certificate_key {self.nginx_cert_path}/docs.key;
    
    # SSL configuration
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-RSA-AES128-GCM-SHA256:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-RSA-AES128-SHA256:ECDHE-RSA-AES256-SHA384:ECDHE-RSA-AES128-SHA:ECDHE-RSA-AES256-SHA:DHE-RSA-AES128-SHA256:DHE-RSA-AES256-SHA256:DHE-RSA-AES128-SHA:DHE-RSA-AES256-SHA:ECDHE-RSA-DES-CBC3-SHA:EDH-RSA-DES-CBC3-SHA:AES128-GCM-SHA256:AES256-GCM-SHA384:AES128-SHA256:AES256-SHA256:AES128-SHA:AES256-SHA:DES-CBC3-SHA:HIGH:!aNULL:!eNULL:!EXPORT:!DES:!MD5:!PSK:!RC4;
    ssl_prefer_server_ciphers on;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;
    
    # Security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "no-referrer-when-downgrade" always;
    add_header Content-Security-Policy "default-src 'self' http: https: data: blob: 'unsafe-inline'" always;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    
    # Gzip compression
    gzip on;
    gzip_vary on;
    gzip_min_length 1024;
    gzip_proxied expired no-cache no-store private must-revalidate auth;
    gzip_types text/plain text/css text/xml text/javascript application/x-javascript application/xml+rss application/javascript;
    
    root /app/site;
    index index.html;
    
    location / {{
        try_files $uri $uri/ =404;
    }}
    
    # Cache static assets
    location ~* \.(css|js|png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot)$ {{
        expires 1y;
        add_header Cache-Control "public, immutable";
    }}
    
    # Security
    location ~ /\.ht {{
        deny all;
    }}
    
    # Health check
    location /health {{
        access_log off;
        return 200 "healthy\\n";
        add_header Content-Type text/plain;
    }}
}}
"""
        
        try:
            with open('/etc/nginx/sites-enabled/docs.conf', 'w') as f:
                f.write(config_content)
            
            logger.info("Updated nginx configuration")
            
        except Exception as e:
            logger.error(f"Failed to update nginx config: {e}")
            raise
    
    def _reload_nginx(self):
        """Reload nginx configuration"""
        try:
            # Test nginx configuration
            result = subprocess.run(['nginx', '-t'], capture_output=True, text=True)
            if result.returncode != 0:
                logger.error(f"Nginx configuration test failed: {result.stderr}")
                return False
            
            # Reload nginx
            subprocess.run(['nginx', '-s', 'reload'], check=True)
            logger.info("Nginx reloaded successfully")
            return True
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to reload nginx: {e}")
            return False
    
    def _send_webhook_notification(self, event_type, success, error_message=None):
        """Send webhook notification for SSL events"""
        if not self.webhook_url:
            return
        
        try:
            webhook_data = {
                'event_type': f'docs.ssl.{event_type}',
                'domain': self.docs_domain,
                'success': success,
                'timestamp': datetime.now().isoformat(),
                'service': 'docs_ssl_manager'
            }
            
            if error_message:
                webhook_data['error_message'] = error_message
            
            payload = json.dumps(webhook_data)
            headers = {
                'Content-Type': 'application/json',
                'User-Agent': 'DocsSSLManager/1.0'
            }
            
            if self.webhook_secret:
                import hmac
                import hashlib
                signature = hmac.new(
                    self.webhook_secret.encode(),
                    payload.encode(),
                    hashlib.sha256
                ).hexdigest()
                headers['X-Webhook-Signature'] = f'sha256={signature}'
            
            response = requests.post(
                self.webhook_url,
                data=payload,
                headers=headers,
                timeout=30
            )
            
            if response.status_code == 200:
                logger.debug(f"Webhook notification sent for {event_type}")
            else:
                logger.warning(f"Webhook notification failed: {response.status_code}")
                
        except Exception as e:
            logger.error(f"Failed to send webhook notification: {e}")
    
    def setup_initial_certificate(self):
        """Setup initial certificate and configuration"""
        try:
            # Create initial nginx config without SSL
            initial_config = f"""
server {{
    listen 80;
    server_name {self.docs_domain};
    
    root /app/site;
    index index.html;
    
    location / {{
        try_files $uri $uri/ =404;
    }}
    
    # Let's Encrypt challenge
    location /.well-known/acme-challenge/ {{
        root /var/www/html;
    }}
}}
"""
            
            os.makedirs('/var/www/html', exist_ok=True)
            
            with open('/etc/nginx/sites-enabled/docs.conf', 'w') as f:
                f.write(initial_config)
            
            # Start nginx
            subprocess.run(['nginx'], check=True)
            
            # Wait a moment for nginx to start
            time.sleep(2)
            
            # Obtain certificate
            if self.obtain_certificate():
                logger.info("Initial SSL setup completed successfully")
                return True
            else:
                logger.error("Failed to obtain initial certificate")
                return False
                
        except Exception as e:
            logger.error(f"Failed to setup initial certificate: {e}")
            return False
    
    def run_renewal_check(self):
        """Run certificate renewal check"""
        logger.info("Running certificate renewal check")
        
        if not self.check_certificate_exists():
            logger.info("Certificate needs renewal or doesn't exist")
            return self.obtain_certificate()
        else:
            logger.info("Certificate is still valid")
            return True
    
    def run_continuous_monitoring(self):
        """Run continuous certificate monitoring"""
        logger.info(f"Starting continuous SSL monitoring (check every {self.check_interval} seconds)")
        
        while True:
            try:
                self.run_renewal_check()
                logger.info(f"SSL check complete, sleeping for {self.check_interval} seconds")
                time.sleep(self.check_interval)
                
            except KeyboardInterrupt:
                logger.info("SSL monitoring stopped by user")
                break
            except Exception as e:
                logger.error(f"Error in SSL monitoring cycle: {e}")
                time.sleep(300)  # Wait 5 minutes before retrying

def main():
    """Main entry point"""
    ssl_manager = DocsSSLManager()
    
    if len(sys.argv) > 1:
        command = sys.argv[1]
        
        if command == 'setup':
            ssl_manager.setup_initial_certificate()
        elif command == 'renew':
            ssl_manager.run_renewal_check()
        elif command == 'monitor':
            ssl_manager.run_continuous_monitoring()
        else:
            print(f"Unknown command: {command}")
            sys.exit(1)
    else:
        # Default to monitoring
        ssl_manager.run_continuous_monitoring()

if __name__ == "__main__":
    main()
