
# Development Getting Started Guide

This guide helps developers set up a local development environment for the Enterprise Mail Server and explains how to add new features, test changes, and contribute to the project.

## 🚀 **Quick Development Setup**

### **Prerequisites**
- Python 3.9 or higher
- MySQL 8.0+ (can be remote)
- Git
- Text editor/IDE (VS Code recommended)

### **1. Clone and Setup**
```bash
# Clone the repository
git clone https://github.com/your-org/enterprise-mail-server.git
cd enterprise-mail-server

# Copy environment configuration
cp .env.example .env.development
```

### **2. Configure Development Environment**
Edit `.env.development` with your development settings:

```bash
# Development Mode
DEVELOPMENT_MODE=true
DEBUG_MODE=true
LOG_LEVEL=DEBUG

# Basic Configuration
HOSTNAME=localhost
DOMAIN=localhost
ADMIN_EMAIL=admin@localhost

# Database (use remote MySQL for development)
DB_HOST=your-dev-mysql-host.com
DB_USER=dev_mail_user
DB_PASSWORD=dev_password
DB_NAME=dev_mailserver

# Simplified Settings for Development
ENABLE_TLS=false
ACME_STAGING=true
WEBHOOK_URLS=http://localhost:5000/webhook

# Reduced Limits for Testing
DB_POOL_SIZE=5
WEBHOOK_BATCH_SIZE=10
TRACKING_CACHE_TTL=60
```

### **3. Start Development Server**
```bash
# Use the development runner
python dev_runner.py

# Or run specific components
python main.py --dev
```

### **4. Verify Setup**
```bash
# Check health monitor
curl http://localhost:8080/health

# Check API
curl http://localhost:5000/api/v1/health

# Check documentation
curl http://localhost:8000
```

## 🏗️ **Development Architecture**

### **Project Structure**
```
enterprise-mail-server/
├── mailer/                 # Core mail infrastructure
│   ├── postfix/           # SMTP server
│   ├── dovecot/           # IMAP/POP3 server
│   ├── rspamd/            # Anti-spam system
│   └── cert_manager/      # SSL management
├── worker/                # Processing services
│   ├── api/              # REST API gateway
│   ├── tracking/         # Email tracking
│   ├── webhooks/         # Event notifications
│   ├── rag/              # AI search system
│   └── rate_limiter/     # Abuse prevention
├── docs/                 # Documentation
├── tests/                # Test suites
└── dev_runner.py         # Development runner
```

### **Service Architecture**
The system uses a microservices architecture where each component runs independently:

1. **Core Mail Services**: Postfix, Dovecot, Rspamd
2. **Worker Services**: API, tracking, webhooks, etc.
3. **Supporting Services**: Health monitor, documentation
4. **Storage**: Database, file storage, cloud sync

## 🔧 **Development Workflow**

### **Adding New Features**

#### **1. Create Feature Branch**
```bash
git checkout -b feature/your-feature-name
```

#### **2. Choose Module Type**
Determine where your feature belongs:

- **Core Mail Feature**: Add to `mailer/` directory
- **API Feature**: Add to `worker/api/routes/`
- **Processing Feature**: Create new service in `worker/`
- **Infrastructure**: Add to appropriate config directory

#### **3. Create Module Structure**
For a new worker service:
```bash
mkdir worker/your_service
cd worker/your_service

# Create basic structure
touch app.py
touch config.py
touch requirements.txt
mkdir services
touch services/__init__.py
```

#### **4. Implement Service**
Create `app.py` with standard structure:
```python
from flask import Flask, request, jsonify
import logging
from config import Config

app = Flask(__name__)
logger = logging.getLogger(__name__)


@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint"""
    return jsonify({"status": "healthy", "service": "your_service", "version": "1.0.0"})


@app.route("/api/v1/your-endpoint", methods=["POST"])
def your_endpoint():
    """Your feature endpoint"""
    try:
        data = request.get_json()
        # Implement your logic here
        return jsonify({"status": "success", "data": data})
    except Exception as e:
        logger.error(f"Error in your_endpoint: {e}")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=Config.PORT, debug=Config.DEBUG)
```

#### **5. Add Configuration**
Create `config.py`:
```python
import os


class Config:
    PORT = int(os.getenv("YOUR_SERVICE_PORT", 8090))
    DEBUG = os.getenv("DEBUG_MODE", "false").lower() == "true"

    # Database
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_USER = os.getenv("DB_USER", "root")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "")
    DB_NAME = os.getenv("DB_NAME", "mailserver")

    # Your service specific config
    YOUR_SETTING = os.getenv("YOUR_SETTING", "default_value")
```

#### **6. Add to Main Deployment**
Update `main.py` to include your service:
```python
# In main.py, add your service to the deployment
def deploy_worker_services():
    """Deploy all worker services"""
    services = [
        # ... existing services
        {
            "name": "your_service",
            "path": "worker/your_service",
            "port": 8090,
            "health_endpoint": "/health",
        }
    ]
    # ... rest of deployment logic
```

### **Testing Your Changes**

#### **1. Unit Tests**
Create tests in `tests/test_your_service.py`:
```python
import unittest
import requests
import time


class TestYourService(unittest.TestCase):
    def setUp(self):
        self.base_url = "http://localhost:8090"
        # Wait for service to start
        time.sleep(2)

    def test_health_endpoint(self):
        response = requests.get(f"{self.base_url}/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "healthy")

    def test_your_endpoint(self):
        data = {"test": "data"}
        response = requests.post(f"{self.base_url}/api/v1/your-endpoint", json=data)
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
```

#### **2. Integration Tests**
Run the full test suite:
```bash
python test_runner.py
```

#### **3. Manual Testing**
```bash
# Start your service
cd worker/your_service
python app.py

# Test in another terminal
curl http://localhost:8090/health
curl -X POST -H "Content-Type: application/json" \
     -d '{"test": "data"}' \
     http://localhost:8090/api/v1/your-endpoint
```

### **API Integration**
If your service needs to be exposed via the main API:

#### **1. Create API Route**
Create `worker/api/routes/your_service.py`:
```python
from flask import Blueprint, request, jsonify, current_app
import requests
from ..utils.auth import require_api_key

your_service_bp = Blueprint("your_service", __name__)


@your_service_bp.route("/api/v1/your-service/<action>", methods=["POST"])
@require_api_key
def your_service_proxy(action):
    """Proxy requests to your service"""
    try:
        # Forward request to your service
        service_url = f"http://localhost:8090/api/v1/{action}"
        response = requests.post(service_url, json=request.get_json())
        return jsonify(response.json()), response.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500
```

#### **2. Register Blueprint**
In `worker/api/routes/__init__.py`:
```python
from .your_service import your_service_bp


def register_blueprints(app):
    """Register all route blueprints"""
    # ... existing blueprints
    app.register_blueprint(your_service_bp)
```

## 🧪 **Testing Framework**

### **Running Tests**
```bash
# Run all tests
python test_runner.py

# Run specific test
python -m unittest tests.test_your_service

# Run with coverage
pip install coverage
coverage run test_runner.py
coverage report
```

### **Test Categories**

#### **1. Unit Tests**
Test individual components in isolation:
```python
# Example: Testing rate limiter logic
def test_rate_limit_exceeded():
    limiter = RateLimiter(max_requests=10, window=60)
    # Simulate 11 requests
    for i in range(11):
        result = limiter.check_limit("test@example.com")
        if i < 10:
            assert result is True
        else:
            assert result is False
```

#### **2. Integration Tests**
Test service interactions:
```python
# Example: Testing email tracking flow
def test_email_tracking_flow():
    # Send email with tracking
    response = self.send_email_with_tracking()
    tracking_id = response["tracking_id"]

    # Simulate email open
    open_response = self.simulate_email_open(tracking_id)
    assert open_response.status_code == 200

    # Check tracking stats
    stats = self.get_tracking_stats(tracking_id)
    assert stats["opens"] == 1
```

#### **3. End-to-End Tests**
Test complete workflows:
```python
# Example: Complete email delivery test
def test_complete_email_delivery():
    # Create domain
    domain_response = self.api_create_domain("test.com")
    
    # Create mailbox
    mailbox_response = self.api_create_mailbox("user@test.com")
    
    # Send email
    email_response = self.send_email("user@test.com", "Test Subject")
    
    # Verify delivery
    delivered = self.check_email_delivered("user@test.com")
    assert delivered is True
```

## 📚 **Documentation**

### **Adding Documentation**
For every new feature, add documentation:

#### **1. API Documentation**
Update `docs/versions/1.0/api/your-service.md`:
```markdown
# Your Service API

## Overview
Description of your service and its purpose.

## Endpoints

### POST /api/v1/your-service/action
Description of the endpoint.

**Request:**
```json
{
  "parameter": "value"
}
```

**Response:**
```json
{
  "status": "success",
  "data": {}
}
```
```

#### **2. Feature Documentation**
Update `docs/versions/1.0/features/your-feature.md`:
```markdown
# Your Feature

## What it does
Explanation of the feature.

## How to use it
Step-by-step usage guide.

## Configuration
Required configuration options.

## Examples
Real-world usage examples.
```

#### **3. Update Navigation**
Add your documentation to `docs/mkdocs.yml`:
```yaml
nav:
  # ... existing navigation
  - Features:
    - features/your-feature.md
  - API Documentation:
    - api/your-service.md
```

## 🔄 **Development Best Practices**

### **Code Style**
- Use Python PEP 8 style guidelines
- Add type hints where appropriate
- Include comprehensive docstrings
- Follow existing patterns in the codebase

### **Error Handling**
```python
import logging

logger = logging.getLogger(__name__)


def your_function():
    try:
        # Your logic here
        result = process_data()
        return result
    except SpecificException as e:
        logger.error(f"Specific error in your_function: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error in your_function: {e}")
        raise RuntimeError(f"Processing failed: {e}")
```

### **Configuration Management**
- Use environment variables for all configuration
- Provide sensible defaults
- Document all configuration options
- Use type conversion for environment variables

### **Logging**
```python
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)

# Use appropriate log levels
logger.debug("Detailed debug information")
logger.info("General information")
logger.warning("Warning message")
logger.error("Error occurred")
logger.critical("Critical error")
```

## 🚀 **Contribution Guidelines**

### **Before Contributing**
1. Check existing issues and pull requests
2. Create an issue to discuss major changes
3. Follow the established code style
4. Ensure all tests pass
5. Update documentation

### **Pull Request Process**
1. Create feature branch from main
2. Make your changes with tests
3. Update documentation
4. Run full test suite
5. Create pull request with description
6. Address review feedback

### **Code Review Checklist**
- [ ] Code follows style guidelines
- [ ] Tests are included and passing
- [ ] Documentation is updated
- [ ] No security vulnerabilities
- [ ] Performance impact considered
- [ ] Backward compatibility maintained

## 🔧 **Development Tools**

### **Recommended IDE Setup**
**VS Code Extensions:**
- Python
- Docker
- GitLens
- REST Client
- YAML

**VS Code Settings:**
```json
{
    "python.formatting.provider": "black",
    "python.linting.enabled": true,
    "python.linting.pylintEnabled": true,
    "files.autoSave": "afterDelay",
    "editor.formatOnSave": true
}
```

### **Debugging**
```python
# Add to any service for debugging
import pdb

pdb.set_trace()

# Or use logging for non-blocking debugging
logger.debug(f"Variable value: {variable}")
```

## 📊 **Performance Considerations**

### **Optimization Guidelines**
- Use database indexes appropriately
- Implement caching where beneficial
- Avoid N+1 queries
- Use connection pooling
- Monitor memory usage

### **Profiling**
```python
import cProfile
import pstats


def profile_function():
    pr = cProfile.Profile()
    pr.enable()

    # Your code here

    pr.disable()
    stats = pstats.Stats(pr)
    stats.sort_stats("cumulative")
    stats.print_stats()
```

## 🔐 **Security Considerations**

### **Security Guidelines**
- Validate all input data
- Use parameterized queries
- Implement proper authentication
- Log security events
- Follow principle of least privilege

### **Common Security Patterns**
```python
# Input validation
def validate_email(email):
    import re

    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return re.match(pattern, email) is not None


# SQL injection prevention
def safe_database_query(email):
    cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
    return cursor.fetchall()
```

---

This guide provides everything you need to start developing with the Enterprise Mail Server. Follow these patterns and practices to maintain code quality and system reliability.
