# Rspamd Anti-Spam Module

Advanced spam filtering and content analysis engine with machine learning capabilities, ClamAV integration, and real-time threat detection.

## Overview

This module provides comprehensive email security through:

- **Machine Learning**: Bayesian classification and neural networks
- **Antivirus Protection**: ClamAV integration for malware detection
- **Real-time Analysis**: Multi-layer content inspection
- **Greylisting**: Intelligent sender verification
- **Performance**: High-throughput processing with minimal latency

## Key Features

### Spam Detection
- **Bayesian Classification**: Self-learning spam detection
- **Neural Networks**: Advanced pattern recognition
- **Rule-based Filtering**: Customizable detection rules
- **Reputation Systems**: Real-time sender reputation checking

### Content Analysis
- **MIME Parsing**: Complete email structure analysis
- **Attachment Scanning**: File type and content validation
- **URL Analysis**: Link reputation and safety checking
- **Header Validation**: SPF, DKIM, DMARC verification

## Configuration Files

### Core Configuration
- `config/local.d/antivirus.conf` - ClamAV integration settings
- `config/local.d/classifier-bayes.conf` - Bayesian classifier configuration
- `config/local.d/greylisting.conf` - Greylisting policy settings

## Processing Pipeline

### Analysis Flow
```
Email → Rspamd → Multiple Modules → Score Calculation → Action Decision
```

### Analysis Modules
1. **Content Analysis**: Text and HTML parsing
2. **Attachment Scanning**: File type and virus detection
3. **Header Validation**: Authentication verification
4. **Reputation Checking**: Sender and domain reputation
5. **Statistical Analysis**: Bayesian and neural network scoring

## Spam Scoring System

### Score Thresholds
```
Score < 5.0:    Accept (clean email)
Score 5.0-15.0: Soft reject (suspicious)
Score > 15.0:   Reject (spam/malware)
```

### Contributing Factors
- **Content Analysis**: Text patterns and structure
- **Sender Reputation**: Historical behavior analysis
- **Authentication**: SPF, DKIM, DMARC results
- **Network Analysis**: IP reputation and geolocation
- **Attachment Security**: File type and content scanning

## Antivirus Integration

### ClamAV Configuration
```conf
# Antivirus scanning with ClamAV
antivirus {
  clamav {
    action = "reject";
    symbol = "CLAM_VIRUS";
    type = "clamav";
    servers = "127.0.0.1:3310";
    patterns {
      CLAM_VIRUS_FOUND = "^.*: .* FOUND$";
    }
  }
}
```

### Threat Detection
- **Virus Scanning**: Complete malware detection
- **Suspicious Files**: Potentially unwanted programs
- **Encrypted Archives**: Password-protected file analysis
- **Real-time Updates**: Automatic signature updates

## Greylisting System

### Configuration
```conf
# Greylisting configuration
greylisting {
  timeout = 300;           # 5-minute initial delay
  expire = 86400;          # 24-hour whitelist duration
  key = "${clean_from_ip}.${clean_from}.${clean_rcpt}";
  whitelist_domains_url = "https://example.com/whitelist.txt";
}
```

### Benefits
- **Spam Reduction**: 80%+ spam elimination
- **Legitimate Mail**: Minimal impact on real senders
- **Resource Efficiency**: Reduced processing load
- **Adaptive Learning**: Automatic sender whitelisting

## Machine Learning Features

### Bayesian Classification
- **Self-Learning**: Automatic model updates
- **Ham/Spam Training**: Continuous improvement
- **False Positive Reduction**: Adaptive thresholds
- **Multi-language Support**: International content analysis

### Neural Networks
- **Deep Learning**: Advanced pattern recognition
- **Feature Extraction**: Automatic characteristic identification
- **Real-time Processing**: Low-latency classification
- **Model Updates**: Periodic training improvements

## Performance Optimization

### High-Throughput Configuration
```
# Optimized for 10,000+ emails/hour
worker "normal" {
  bind_socket = "*:11333";
  count = 4;              # Worker processes
  max_files = 10000;      # File descriptor limit
  max_core = 2G;          # Core dump size limit
}
```

### Resource Management
- **Memory Efficiency**: Optimized for large volumes
- **CPU Optimization**: Multi-core processing
- **I/O Management**: Efficient file operations
- **Cache Management**: Smart caching strategies

## Monitoring & Statistics

### Real-time Metrics
- **Processing Rate**: Emails per second
- **Detection Accuracy**: Spam/ham classification rates
- **Performance Stats**: Latency and throughput
- **Error Tracking**: Processing failures and recoveries

### Web Interface
- Administrative dashboard for configuration
- Real-time statistics and monitoring
- Historical data analysis
- Manual message review and training

## Integration Features

### Postfix Integration
```
# Postfix main.cf integration
smtpd_milters = inet:localhost:11332
non_smtpd_milters = inet:localhost:11332
milter_protocol = 6
milter_mail_macros = i {mail_addr} {client_addr} {client_name} {auth_authen}
```

### Database Integration
- **Statistics Storage**: MySQL-based metrics
- **Learning Data**: Training set management
- **User Preferences**: Per-user filtering settings
- **Audit Logging**: Comprehensive activity tracking

## Security Features

### Email Authentication
- **SPF Validation**: Sender IP verification
- **DKIM Verification**: Digital signature validation
- **DMARC Processing**: Policy compliance checking
- **Authentication Results**: Header injection for downstream processing

### Content Security
- **HTML Analysis**: JavaScript and embedded content scanning
- **URL Validation**: Link safety and reputation checking
- **Attachment Security**: File type and content validation
- **Data Loss Prevention**: Sensitive information detection

## Troubleshooting

### Common Issues
1. **High False Positives**: Adjust scoring thresholds and training
2. **Performance Problems**: Review worker configuration and resources
3. **ClamAV Issues**: Check antivirus service connectivity
4. **Greylisting Problems**: Verify database connectivity and settings

### Log Analysis
```bash
# Check Rspamd logs
tail -f /var/log/rspamd/rspamd.log

# Review statistics
rspamc stat

# Test message processing
rspamc < test_message.eml
```

### Diagnostic Tools
- **rspamc**: Command-line client for testing and management
- **Web Interface**: Browser-based administration
- **Log Analysis**: Structured logging for troubleshooting
- **Performance Monitoring**: Real-time metrics dashboard

## Dependencies

- **ClamAV**: Antivirus scanning engine
- **MySQL**: Statistics and configuration storage
- **Redis**: Caching and temporary data storage
- **Postfix**: SMTP server integration via milter protocol

## Integration Points

- **Postfix**: Primary integration via milter interface
- **Health Monitor**: Service status and performance tracking
- **Database**: Shared configuration and statistics storage
- **Webhook System**: Real-time event notifications for security incidents

This module provides production-grade email security with advanced threat detection, machine learning capabilities, and comprehensive content analysis essential for protecting against modern email-based attacks.