
#!/usr/bin/env python3
"""
Mail Log Analyzer

Advanced log analysis for mail server operations including:
- Bounce rate analysis
- Delivery statistics
- Spam detection rates
- Performance metrics
- Security incident reports
"""

import os
import re
import sys
import json
import time
import logging
from datetime import datetime, timedelta
from collections import defaultdict, Counter
import mysql.connector
from pathlib import Path
import geoip2.database
import requests

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.append(str(project_root))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MailLogAnalyzer:
    """Advanced mail log analysis and reporting"""
    
    def __init__(self):
        self.db_config = {
            'host': os.getenv('DB_HOST', 'localhost'),
            'port': int(os.getenv('DB_PORT', 3306)),
            'database': os.getenv('DB_NAME', 'mailserver'),
            'user': os.getenv('DB_USER', 'root'),
            'password': os.getenv('DB_PASSWORD', '')
        }
        
        # Log file paths
        self.postfix_log = '/var/log/mail.log'
        self.dovecot_log = '/var/log/dovecot.log'
        self.rspamd_log = '/var/log/rspamd/rspamd.log'
        
        # GeoIP database (optional)
        self.geoip_db = os.getenv('GEOIP_DB_PATH', '/usr/share/GeoIP/GeoLite2-Country.mmdb')
        self.geoip_reader = None
        
        if os.path.exists(self.geoip_db):
            try:
                self.geoip_reader = geoip2.database.Reader(self.geoip_db)
            except Exception as e:
                logger.warning(f"Could not load GeoIP database: {e}")
        
        # Webhook configuration
        self.webhook_url = os.getenv('WEBHOOK_URLS', '')
        
        # Analysis patterns
        self.patterns = {
            'postfix_sent': re.compile(r'postfix/smtp\[\d+\]: ([A-F0-9]+): to=<([^>]+)>, relay=([^,]+), .* status=sent'),
            'postfix_bounced': re.compile(r'postfix/smtp\[\d+\]: ([A-F0-9]+): to=<([^>]+)>, relay=([^,]+), .* status=bounced'),
            'postfix_deferred': re.compile(r'postfix/smtp\[\d+\]: ([A-F0-9]+): to=<([^>]+)>, relay=([^,]+), .* status=deferred'),
            'postfix_rejected': re.compile(r'postfix/smtpd\[\d+\]: NOQUEUE: reject: .* from ([^[]+)\[([^\]]+)\]'),
            'dovecot_login': re.compile(r'dovecot: imap-login: Login: user=<([^>]+)>, method=\w+, rip=([^,]+)'),
            'dovecot_failed': re.compile(r'dovecot: auth-worker\(\d+\): sql\([^,]+,[^,]+\): Password mismatch for ([^,]+)'),
            'rspamd_spam': re.compile(r'rspamd\[\d+\]: <[^>]+>; task; spam: \w+ \[([0-9.]+)/15.00\]'),
            'rspamd_ham': re.compile(r'rspamd\[\d+\]: <[^>]+>; task; ham: \w+ \[([0-9.-]+)/15.00\]')
        }
        
        logger.info("Mail Log Analyzer initialized")
    
    def get_database_connection(self):
        """Get database connection"""
        try:
            return mysql.connector.connect(**self.db_config)
        except Exception as e:
            logger.error(f"Database connection failed: {e}")
            return None
    
    def analyze_postfix_logs(self, hours=24):
        """Analyze Postfix logs for delivery statistics"""
        stats = {
            'sent': 0,
            'bounced': 0,
            'deferred': 0,
            'rejected': 0,
            'recipients': Counter(),
            'domains': Counter(),
            'reject_reasons': Counter(),
            'bounce_reasons': Counter(),
            'countries': Counter(),
            'hourly_volume': defaultdict(int)
        }
        
        cutoff_time = datetime.now() - timedelta(hours=hours)
        
        try:
            with open(self.postfix_log, 'r') as f:
                for line in f:
                    # Parse timestamp
                    try:
                        log_time = datetime.strptime(line[:15], '%b %d %H:%M:%S')
                        log_time = log_time.replace(year=datetime.now().year)
                        
                        if log_time < cutoff_time:
                            continue
                    except:
                        continue
                    
                    # Count hourly volume
                    hour_key = log_time.strftime('%Y-%m-%d %H:00')
                    
                    # Analyze sent messages
                    match = self.patterns['postfix_sent'].search(line)
                    if match:
                        queue_id, recipient, relay = match.groups()
                        stats['sent'] += 1
                        stats['hourly_volume'][hour_key] += 1
                        
                        domain = recipient.split('@')[1] if '@' in recipient else 'unknown'
                        stats['domains'][domain] += 1
                        stats['recipients'][recipient] += 1
                    
                    # Analyze bounced messages
                    match = self.patterns['postfix_bounced'].search(line)
                    if match:
                        queue_id, recipient, relay = match.groups()
                        stats['bounced'] += 1
                        
                        # Extract bounce reason
                        if 'reason=' in line:
                            reason = line.split('reason=')[1].split(',')[0]
                            stats['bounce_reasons'][reason] += 1
                    
                    # Analyze deferred messages
                    match = self.patterns['postfix_deferred'].search(line)
                    if match:
                        stats['deferred'] += 1
                    
                    # Analyze rejected messages
                    match = self.patterns['postfix_rejected'].search(line)
                    if match:
                        hostname, ip = match.groups()
                        stats['rejected'] += 1
                        
                        # GeoIP lookup
                        if self.geoip_reader:
                            try:
                                response = self.geoip_reader.country(ip)
                                stats['countries'][response.country.iso_code] += 1
                            except:
                                stats['countries']['UNKNOWN'] += 1
                        
                        # Extract rejection reason
                        if 'reject:' in line:
                            reason = line.split('reject:')[1].split(';')[0].strip()
                            stats['reject_reasons'][reason] += 1
        
        except FileNotFoundError:
            logger.warning(f"Postfix log file not found: {self.postfix_log}")
        except Exception as e:
            logger.error(f"Error analyzing Postfix logs: {e}")
        
        return stats
    
    def analyze_rspamd_logs(self, hours=24):
        """Analyze Rspamd logs for spam detection statistics"""
        stats = {
            'total_processed': 0,
            'spam_detected': 0,
            'ham_detected': 0,
            'spam_scores': [],
            'ham_scores': [],
            'average_spam_score': 0,
            'average_ham_score': 0,
            'false_positive_risk': 0
        }
        
        cutoff_time = datetime.now() - timedelta(hours=hours)
        
        try:
            with open(self.rspamd_log, 'r') as f:
                for line in f:
                    # Parse timestamp
                    try:
                        timestamp_str = line.split()[0] + ' ' + line.split()[1]
                        log_time = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
                        
                        if log_time < cutoff_time:
                            continue
                    except:
                        continue
                    
                    stats['total_processed'] += 1
                    
                    # Analyze spam detection
                    match = self.patterns['rspamd_spam'].search(line)
                    if match:
                        score = float(match.group(1))
                        stats['spam_detected'] += 1
                        stats['spam_scores'].append(score)
                    
                    # Analyze ham detection
                    match = self.patterns['rspamd_ham'].search(line)
                    if match:
                        score = float(match.group(1))
                        stats['ham_detected'] += 1
                        stats['ham_scores'].append(score)
        
        except FileNotFoundError:
            logger.warning(f"Rspamd log file not found: {self.rspamd_log}")
        except Exception as e:
            logger.error(f"Error analyzing Rspamd logs: {e}")
        
        # Calculate averages
        if stats['spam_scores']:
            stats['average_spam_score'] = sum(stats['spam_scores']) / len(stats['spam_scores'])
        
        if stats['ham_scores']:
            stats['average_ham_score'] = sum(stats['ham_scores']) / len(stats['ham_scores'])
        
        # Calculate false positive risk (ham emails with score > 5)
        high_scoring_ham = [s for s in stats['ham_scores'] if s > 5.0]
        if stats['ham_scores']:
            stats['false_positive_risk'] = len(high_scoring_ham) / len(stats['ham_scores']) * 100
        
        return stats
    
    def generate_summary_report(self, hours=24):
        """Generate comprehensive mail server summary report"""
        logger.info(f"Generating summary report for last {hours} hours")
        
        postfix_stats = self.analyze_postfix_logs(hours)
        rspamd_stats = self.analyze_rspamd_logs(hours)
        
        # Calculate delivery rates
        total_attempts = postfix_stats['sent'] + postfix_stats['bounced'] + postfix_stats['deferred']
        delivery_rate = (postfix_stats['sent'] / total_attempts * 100) if total_attempts > 0 else 0
        bounce_rate = (postfix_stats['bounced'] / total_attempts * 100) if total_attempts > 0 else 0
        
        # Calculate spam detection rate
        total_processed = rspamd_stats['spam_detected'] + rspamd_stats['ham_detected']
        spam_rate = (rspamd_stats['spam_detected'] / total_processed * 100) if total_processed > 0 else 0
        
        report = {
            'timestamp': datetime.now().isoformat(),
            'period_hours': hours,
            'delivery_statistics': {
                'total_sent': postfix_stats['sent'],
                'total_bounced': postfix_stats['bounced'],
                'total_deferred': postfix_stats['deferred'],
                'total_rejected': postfix_stats['rejected'],
                'delivery_rate_percent': round(delivery_rate, 2),
                'bounce_rate_percent': round(bounce_rate, 2),
                'top_recipient_domains': dict(postfix_stats['domains'].most_common(10)),
                'top_bounce_reasons': dict(postfix_stats['bounce_reasons'].most_common(5)),
                'top_reject_reasons': dict(postfix_stats['reject_reasons'].most_common(5)),
                'hourly_volume': dict(postfix_stats['hourly_volume'])
            },
            'spam_statistics': {
                'total_processed': rspamd_stats['total_processed'],
                'spam_detected': rspamd_stats['spam_detected'],
                'ham_detected': rspamd_stats['ham_detected'],
                'spam_rate_percent': round(spam_rate, 2),
                'average_spam_score': round(rspamd_stats['average_spam_score'], 2),
                'average_ham_score': round(rspamd_stats['average_ham_score'], 2),
                'false_positive_risk_percent': round(rspamd_stats['false_positive_risk'], 2)
            },
            'security_statistics': {
                'rejected_by_country': dict(postfix_stats['countries'].most_common(10)),
                'total_auth_failures': 0  # Will be filled by Dovecot analysis
            },
            'recommendations': self.generate_recommendations(postfix_stats, rspamd_stats)
        }
        
        # Store report in database
        self.store_report(report)
        
        # Send webhook notification if configured
        if self.webhook_url:
            self.send_report_webhook(report)
        
        return report
    
    def generate_recommendations(self, postfix_stats, rspamd_stats):
        """Generate actionable recommendations based on analysis"""
        recommendations = []
        
        # Delivery rate recommendations
        total_attempts = postfix_stats['sent'] + postfix_stats['bounced'] + postfix_stats['deferred']
        if total_attempts > 0:
            bounce_rate = postfix_stats['bounced'] / total_attempts * 100
            
            if bounce_rate > 5:
                recommendations.append({
                    'type': 'warning',
                    'category': 'deliverability',
                    'message': f'High bounce rate ({bounce_rate:.1f}%). Review recipient lists and sending practices.',
                    'action': 'Check bounce reasons and implement list hygiene'
                })
            
            if bounce_rate > 10:
                recommendations.append({
                    'type': 'critical',
                    'category': 'deliverability', 
                    'message': f'Critical bounce rate ({bounce_rate:.1f}%). Immediate action required.',
                    'action': 'Pause sending and investigate bounce causes'
                })
        
        # Spam detection recommendations
        if rspamd_stats['false_positive_risk'] > 5:
            recommendations.append({
                'type': 'warning',
                'category': 'spam_filtering',
                'message': f'High false positive risk ({rspamd_stats["false_positive_risk"]:.1f}%).',
                'action': 'Review and adjust Rspamd scoring thresholds'
            })
        
        # Security recommendations
        if postfix_stats['rejected'] > 1000:
            recommendations.append({
                'type': 'info',
                'category': 'security',
                'message': f'High rejection rate ({postfix_stats["rejected"]} in period). Security filters working.',
                'action': 'Monitor for unusual patterns in rejected sources'
            })
        
        return recommendations
    
    def store_report(self, report):
        """Store analysis report in database"""
        conn = self.get_database_connection()
        if not conn:
            return
        
        try:
            cursor = conn.cursor()
            
            # Create table if not exists
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS mail_analysis_reports (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    period_hours INT,
                    report_data JSON,
                    delivery_rate DECIMAL(5,2),
                    bounce_rate DECIMAL(5,2),
                    spam_rate DECIMAL(5,2),
                    INDEX idx_timestamp (timestamp)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            
            # Insert report
            cursor.execute("""
                INSERT INTO mail_analysis_reports 
                (period_hours, report_data, delivery_rate, bounce_rate, spam_rate)
                VALUES (%s, %s, %s, %s, %s)
            """, (
                report['period_hours'],
                json.dumps(report),
                report['delivery_statistics']['delivery_rate_percent'],
                report['delivery_statistics']['bounce_rate_percent'],
                report['spam_statistics']['spam_rate_percent']
            ))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.info("Analysis report stored in database")
            
        except Exception as e:
            logger.error(f"Error storing report: {e}")
    
    def send_report_webhook(self, report):
        """Send report via webhook"""
        try:
            webhook_data = {
                'event_type': 'mail.analysis_report',
                'timestamp': report['timestamp'],
                'summary': {
                    'delivery_rate': report['delivery_statistics']['delivery_rate_percent'],
                    'bounce_rate': report['delivery_statistics']['bounce_rate_percent'],
                    'spam_rate': report['spam_statistics']['spam_rate_percent'],
                    'total_sent': report['delivery_statistics']['total_sent'],
                    'recommendations_count': len(report['recommendations'])
                },
                'recommendations': report['recommendations']
            }
            
            response = requests.post(
                self.webhook_url,
                json=webhook_data,
                timeout=30,
                headers={'Content-Type': 'application/json'}
            )
            
            if response.status_code == 200:
                logger.info("Analysis report webhook sent successfully")
            else:
                logger.warning(f"Webhook failed: {response.status_code}")
                
        except Exception as e:
            logger.error(f"Error sending webhook: {e}")

def main():
    """Main entry point for log analyzer"""
    analyzer = MailLogAnalyzer()
    
    # Generate reports every hour
    while True:
        try:
            # Generate 24-hour summary report
            report = analyzer.generate_summary_report(hours=24)
            
            # Print summary to console
            print(f"\n=== Mail Server Analysis Report ===")
            print(f"Period: {report['period_hours']} hours")
            print(f"Delivery Rate: {report['delivery_statistics']['delivery_rate_percent']}%")
            print(f"Bounce Rate: {report['delivery_statistics']['bounce_rate_percent']}%")
            print(f"Spam Rate: {report['spam_statistics']['spam_rate_percent']}%")
            print(f"Total Sent: {report['delivery_statistics']['total_sent']}")
            print(f"Recommendations: {len(report['recommendations'])}")
            
            # Wait 1 hour before next analysis
            time.sleep(3600)
            
        except KeyboardInterrupt:
            logger.info("Log analyzer stopped")
            break
        except Exception as e:
            logger.error(f"Error in analysis cycle: {e}")
            time.sleep(300)  # Wait 5 minutes on error

if __name__ == "__main__":
    main()
