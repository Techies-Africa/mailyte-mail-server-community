#!/usr/bin/env python3
"""
Load Balancer Configuration for Enterprise Mail Server
Handles SMTP/IMAP load balancing across multiple instances
"""

import os


class LoadBalancerConfig:
    """Configure HAProxy or Nginx for mail server load balancing"""

    def __init__(self):
        self.servers = os.getenv("MAIL_SERVERS", "mail1:25,mail2:25").split(",")
        self.health_check_interval = int(os.getenv("HEALTH_CHECK_INTERVAL", "30"))

    def generate_haproxy_config(self) -> str:
        """Generate HAProxy configuration for mail load balancing"""
        config = """
global
    daemon
    maxconn 4096
    log stdout local0
    
defaults
    mode tcp
    timeout connect 5000ms
    timeout client 50000ms
    timeout server 50000ms
    
# SMTP Load Balancing
frontend smtp_frontend
    bind *:25
    mode tcp
    default_backend smtp_servers
    
backend smtp_servers
    mode tcp
    balance roundrobin
    option tcp-check
"""

        for i, server in enumerate(self.servers, 1):
            host, port = server.split(":")
            config += (
                f"    server smtp{i} {host}:{port} check inter {self.health_check_interval}s\n"
            )

        return config

    def generate_nginx_config(self) -> str:
        """Generate Nginx stream configuration for mail"""
        config = """
stream {
    upstream smtp_backend {
        least_conn;
"""
        for server in self.servers:
            host, port = server.split(":")
            config += f"        server {host}:{port} max_fails=3 fail_timeout=30s;\n"

        config += """    }
    
    server {
        listen 25;
        proxy_pass smtp_backend;
        proxy_timeout 1s;
        proxy_responses 1;
    }
}"""
        return config


if __name__ == "__main__":
    lb = LoadBalancerConfig()
    print("HAProxy Config:")
    print(lb.generate_haproxy_config())
