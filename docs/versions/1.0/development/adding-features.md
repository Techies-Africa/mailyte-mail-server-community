
# Adding New Features to Mailyte Mail Server

This comprehensive guide walks you through adding new features to the Mailyte Mail Server, from planning to deployment.

## 🎯 **Feature Development Process**

### **1. Feature Planning**

#### **Define Requirements**
Before coding, clearly define:
- **Purpose**: What problem does this feature solve?
- **Scope**: What functionality will be included?
- **Users**: Who will use this feature?
- **Integration**: How does it fit with existing systems?
- **Performance**: What are the performance requirements?

#### **Architecture Design**
Consider:
- **Service Location**: Which module should contain this feature?
- **Data Storage**: What data needs to be stored?
- **API Design**: What endpoints are needed?
- **Dependencies**: What external services are required?
- **Security**: What security measures are needed?

### **2. Feature Types**

#### **Core Mail Features**
Features that enhance core email functionality:
- Email encryption/decryption
- Advanced spam filtering
- Email archiving
- Message routing rules

#### **API Features**
New endpoints for external integration:
- Domain management enhancements
- Mailbox analytics
- Bulk operations
- Webhook extensions

#### **Worker Services**
Background processing features:
- Email queue optimization
- Performance monitoring
- Data analytics
- Automated maintenance

#### **Infrastructure Features**
System-level enhancements:
- Security improvements
- Performance optimizations
- Monitoring capabilities
- Backup solutions

## 🏗️ **Creating a New Worker Service**

### **Step 1: Setup Service Structure**
```bash
# Create service directory
mkdir worker/your_feature
cd worker/your_feature

# Create basic files
touch app.py
touch config.py
touch requirements.txt
touch README.md

# Create services directory
mkdir services
touch services/__init__.py
```

### **Step 2: Implement Core Service**
Create `app.py` with the standard structure:

```python
"""
Your Feature Service
Detailed description of what this service does
"""
from flask import Flask, request, jsonify, current_app
import logging
import os
from datetime import datetime
from config import Config

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Health check endpoint (required for all services)
@app.route('/health', methods=['GET'])
def health_check():
    """
    Health check endpoint for monitoring
    Returns service status and basic metrics
    """
    try:
        # Perform basic health checks
        health_status = {
            'status': 'healthy',
            'service': 'your_feature',
            'version': '1.0.0',
            'timestamp': datetime.utcnow().isoformat(),
            'checks': {
                'database': check_database_connection(),
                'dependencies': check_dependencies(),
                'resources': check_resource_usage()
            }
        }
        
        # Return unhealthy if any check fails
        if not all(health_status['checks'].values()):
            health_status['status'] = 'unhealthy'
            return jsonify(health_status), 503
            
        return jsonify(health_status), 200
        
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return jsonify({
            'status': 'unhealthy',
            'error': str(e),
            'timestamp': datetime.utcnow().isoformat()
        }), 503

# Metrics endpoint (optional but recommended)
@app.route('/metrics', methods=['GET'])
def metrics():
    """
    Prometheus-compatible metrics endpoint
    Returns service metrics for monitoring
    """
    try:
        metrics_data = {
            'requests_total': get_request_count(),
            'requests_duration_seconds': get_average_response_time(),
            'errors_total': get_error_count(),
            'memory_usage_bytes': get_memory_usage(),
            'cpu_usage_percent': get_cpu_usage()
        }
        
        # Format as Prometheus metrics
        prometheus_format = []
        for metric, value in metrics_data.items():
            prometheus_format.append(f"{metric} {value}")
        
        return '\n'.join(prometheus_format), 200, {
            'Content-Type': 'text/plain; charset=utf-8'
        }
        
    except Exception as e:
        logger.error(f"Metrics collection failed: {e}")
        return "# Metrics collection failed", 500

# Main feature endpoints
@app.route('/api/v1/your-feature/action', methods=['POST'])
def your_feature_action():
    """
    Main feature endpoint
    Implements the core functionality of your feature
    """
    try:
        # Validate request
        if not request.is_json:
            return jsonify({'error': 'Content-Type must be application/json'}), 400
        
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['field1', 'field2']
        for field in required_fields:
            if field not in data:
                return jsonify({'error': f'Missing required field: {field}'}), 400
        
        # Process the request
        result = process_your_feature(data)
        
        # Log successful operation
        logger.info(f"Successfully processed your_feature_action: {data.get('id', 'unknown')}")
        
        return jsonify({
            'status': 'success',
            'data': result,
            'timestamp': datetime.utcnow().isoformat()
        }), 200
        
    except ValidationError as e:
        logger.warning(f"Validation error in your_feature_action: {e}")
        return jsonify({'error': f'Validation error: {e}'}), 400
        
    except ProcessingError as e:
        logger.error(f"Processing error in your_feature_action: {e}")
        return jsonify({'error': f'Processing failed: {e}'}), 500
        
    except Exception as e:
        logger.error(f"Unexpected error in your_feature_action: {e}")
        return jsonify({'error': 'Internal server error'}), 500

# Supporting functions
def check_database_connection():
    """Check if database is accessible"""
    try:
        # Implement database connectivity check
        return True
    except Exception:
        return False

def check_dependencies():
    """Check if external dependencies are available"""
    try:
        # Check external services, APIs, etc.
        return True
    except Exception:
        return False

def check_resource_usage():
    """Check system resource usage"""
    try:
        # Check memory, CPU, disk usage
        return True
    except Exception:
        return False

def process_your_feature(data):
    """
    Core business logic for your feature
    Implement the main functionality here
    """
    # Implement your feature logic
    result = {
        'processed': True,
        'data': data,
        'timestamp': datetime.utcnow().isoformat()
    }
    return result

# Custom exception classes
class ValidationError(Exception):
    pass

class ProcessingError(Exception):
    pass

# Run the application
if __name__ == '__main__':
    logger.info(f"Starting Your Feature Service on port {Config.PORT}")
    app.run(
        host='0.0.0.0', 
        port=Config.PORT, 
        debug=Config.DEBUG,
        threaded=True
    )
```

### **Step 3: Configuration Management**
Create `config.py`:

```python
"""
Configuration management for Your Feature Service
"""
import os
from typing import Optional

class Config:
    """Configuration class with environment variable support"""
    
    # Server Configuration
    PORT = int(os.getenv('YOUR_FEATURE_PORT', 8090))
    DEBUG = os.getenv('DEBUG_MODE', 'false').lower() == 'true'
    HOST = os.getenv('YOUR_FEATURE_HOST', '0.0.0.0')
    
    # Database Configuration
    DB_HOST = os.getenv('DB_HOST', 'localhost')
    DB_PORT = int(os.getenv('DB_PORT', 3306))
    DB_USER = os.getenv('DB_USER', 'root')
    DB_PASSWORD = os.getenv('DB_PASSWORD', '')
    DB_NAME = os.getenv('DB_NAME', 'mailserver')
    
    # Database Connection Pool
    DB_POOL_SIZE = int(os.getenv('DB_POOL_SIZE', 10))
    DB_POOL_TIMEOUT = int(os.getenv('DB_POOL_TIMEOUT', 30))
    
    # Redis Configuration (for caching)
    REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
    REDIS_PORT = int(os.getenv('REDIS_PORT', 6379))
    REDIS_PASSWORD = os.getenv('REDIS_PASSWORD', '')
    REDIS_DB = int(os.getenv('REDIS_DB', 0))
    
    # Feature-specific Configuration
    YOUR_FEATURE_ENABLED = os.getenv('YOUR_FEATURE_ENABLED', 'true').lower() == 'true'
    YOUR_FEATURE_MAX_REQUESTS = int(os.getenv('YOUR_FEATURE_MAX_REQUESTS', 1000))
    YOUR_FEATURE_TIMEOUT = int(os.getenv('YOUR_FEATURE_TIMEOUT', 30))
    
    # External Service Configuration
    WEBHOOK_URL = os.getenv('WEBHOOK_URL', '')
    WEBHOOK_TIMEOUT = int(os.getenv('WEBHOOK_TIMEOUT', 10))
    WEBHOOK_RETRIES = int(os.getenv('WEBHOOK_RETRIES', 3))
    
    # Security Configuration
    API_KEY_HEADER = os.getenv('API_KEY_HEADER', 'X-API-Key')
    RATE_LIMIT_ENABLED = os.getenv('RATE_LIMIT_ENABLED', 'true').lower() == 'true'
    RATE_LIMIT_REQUESTS = int(os.getenv('RATE_LIMIT_REQUESTS', 100))
    RATE_LIMIT_WINDOW = int(os.getenv('RATE_LIMIT_WINDOW', 3600))
    
    # Logging Configuration
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    LOG_FORMAT = os.getenv('LOG_FORMAT', '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    @classmethod
    def validate_config(cls) -> bool:
        """
        Validate configuration settings
        Returns True if configuration is valid
        """
        required_settings = [
            ('DB_HOST', cls.DB_HOST),
            ('DB_USER', cls.DB_USER),
            ('DB_NAME', cls.DB_NAME),
        ]
        
        for setting_name, setting_value in required_settings:
            if not setting_value:
                raise ValueError(f"Required setting {setting_name} is not configured")
        
        return True
    
    @classmethod
    def get_database_url(cls) -> str:
        """Get formatted database connection URL"""
        return f"mysql://{cls.DB_USER}:{cls.DB_PASSWORD}@{cls.DB_HOST}:{cls.DB_PORT}/{cls.DB_NAME}"
    
    @classmethod
    def get_redis_url(cls) -> str:
        """Get formatted Redis connection URL"""
        if cls.REDIS_PASSWORD:
            return f"redis://:{cls.REDIS_PASSWORD}@{cls.REDIS_HOST}:{cls.REDIS_PORT}/{cls.REDIS_DB}"
        return f"redis://{cls.REDIS_HOST}:{cls.REDIS_PORT}/{cls.REDIS_DB}"
```

### **Step 4: Service Implementation**
Create business logic in `services/your_feature_service.py`:

```python
"""
Core business logic for Your Feature
"""
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime
import json

logger = logging.getLogger(__name__)

class YourFeatureService:
    """
    Service class implementing your feature's business logic
    """
    
    def __init__(self, database_service, cache_service=None):
        self.db = database_service
        self.cache = cache_service
        self.logger = logging.getLogger(__name__)
    
    def process_request(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a feature request
        
        Args:
            data: Request data dictionary
            
        Returns:
            Processing result dictionary
            
        Raises:
            ValidationError: If input data is invalid
            ProcessingError: If processing fails
        """
        try:
            # Validate input data
            self._validate_input(data)
            
            # Process the request
            result = self._execute_core_logic(data)
            
            # Store result if needed
            if result.get('store_result', False):
                self._store_result(result)
            
            # Send notifications if configured
            if result.get('notify', False):
                self._send_notifications(result)
            
            return result
            
        except Exception as e:
            self.logger.error(f"Failed to process request: {e}")
            raise
    
    def _validate_input(self, data: Dict[str, Any]) -> None:
        """
        Validate input data
        
        Args:
            data: Input data to validate
            
        Raises:
            ValidationError: If validation fails
        """
        required_fields = ['id', 'type', 'parameters']
        
        for field in required_fields:
            if field not in data:
                raise ValidationError(f"Missing required field: {field}")
        
        # Type-specific validation
        if data['type'] not in ['type1', 'type2', 'type3']:
            raise ValidationError(f"Invalid type: {data['type']}")
        
        # Parameter validation
        parameters = data.get('parameters', {})
        if not isinstance(parameters, dict):
            raise ValidationError("Parameters must be a dictionary")
    
    def _execute_core_logic(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute the core feature logic
        
        Args:
            data: Validated input data
            
        Returns:
            Processing result
        """
        # Implement your core feature logic here
        result = {
            'id': data['id'],
            'type': data['type'],
            'status': 'processed',
            'timestamp': datetime.utcnow().isoformat(),
            'result': self._perform_feature_action(data)
        }
        
        return result
    
    def _perform_feature_action(self, data: Dict[str, Any]) -> Any:
        """
        Perform the specific feature action
        
        Args:
            data: Input data
            
        Returns:
            Action result
        """
        # This is where your specific feature logic goes
        # Examples:
        # - Process email data
        # - Analyze patterns
        # - Generate reports
        # - Integrate with external services
        
        action_type = data['type']
        parameters = data['parameters']
        
        if action_type == 'type1':
            return self._handle_type1(parameters)
        elif action_type == 'type2':
            return self._handle_type2(parameters)
        elif action_type == 'type3':
            return self._handle_type3(parameters)
        else:
            raise ProcessingError(f"Unknown action type: {action_type}")
    
    def _handle_type1(self, parameters: Dict[str, Any]) -> Any:
        """Handle type1 actions"""
        # Implement type1 specific logic
        return {'action': 'type1', 'result': 'success'}
    
    def _handle_type2(self, parameters: Dict[str, Any]) -> Any:
        """Handle type2 actions"""
        # Implement type2 specific logic
        return {'action': 'type2', 'result': 'success'}
    
    def _handle_type3(self, parameters: Dict[str, Any]) -> Any:
        """Handle type3 actions"""
        # Implement type3 specific logic
        return {'action': 'type3', 'result': 'success'}
    
    def _store_result(self, result: Dict[str, Any]) -> None:
        """
        Store processing result in database
        
        Args:
            result: Result to store
        """
        try:
            self.db.store_feature_result(result)
            self.logger.info(f"Stored result for ID: {result['id']}")
        except Exception as e:
            self.logger.error(f"Failed to store result: {e}")
            raise
    
    def _send_notifications(self, result: Dict[str, Any]) -> None:
        """
        Send notifications about processing result
        
        Args:
            result: Result to notify about
        """
        try:
            # Send webhook notifications
            # Send email notifications
            # Update dashboards
            self.logger.info(f"Sent notifications for ID: {result['id']}")
        except Exception as e:
            self.logger.warning(f"Failed to send notifications: {e}")
            # Don't raise - notifications are not critical

class ValidationError(Exception):
    """Raised when input validation fails"""
    pass

class ProcessingError(Exception):
    """Raised when processing fails"""
    pass
```

## 🔌 **API Integration**

### **Step 1: Create API Route**
Create `worker/api/routes/your_feature.py`:

```python
"""
API routes for Your Feature
"""
from flask import Blueprint, request, jsonify, current_app
import requests
import logging
from typing import Dict, Any
from ..utils.auth import require_api_key
from ..utils.database import get_db_connection

your_feature_bp = Blueprint('your_feature', __name__)
logger = logging.getLogger(__name__)

@your_feature_bp.route('/api/v1/your-feature/process', methods=['POST'])
@require_api_key
def process_feature_request():
    """
    Process a feature request through the API gateway
    
    This endpoint acts as a proxy to the feature service,
    providing authentication and request validation.
    """
    try:
        # Validate request content type
        if not request.is_json:
            return jsonify({
                'error': 'Content-Type must be application/json'
            }), 400
        
        data = request.get_json()
        
        # Add API metadata
        data['api_key_id'] = getattr(request, 'api_key_id', None)
        data['request_timestamp'] = datetime.utcnow().isoformat()
        
        # Forward to feature service
        service_url = f"http://localhost:8090/api/v1/your-feature/action"
        
        response = requests.post(
            service_url,
            json=data,
            timeout=30,
            headers={'Content-Type': 'application/json'}
        )
        
        # Log the request
        logger.info(f"Feature request processed: {response.status_code}")
        
        # Return response from service
        if response.status_code == 200:
            return jsonify(response.json()), 200
        else:
            return jsonify(response.json()), response.status_code
            
    except requests.RequestException as e:
        logger.error(f"Failed to connect to feature service: {e}")
        return jsonify({
            'error': 'Feature service unavailable',
            'details': str(e)
        }), 503
        
    except Exception as e:
        logger.error(f"Unexpected error in process_feature_request: {e}")
        return jsonify({
            'error': 'Internal server error',
            'details': str(e)
        }), 500

@your_feature_bp.route('/api/v1/your-feature/status/<request_id>', methods=['GET'])
@require_api_key
def get_feature_status(request_id):
    """
    Get the status of a feature request
    
    Args:
        request_id: ID of the request to check
    """
    try:
        # Query database for request status
        with get_db_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("""
                SELECT * FROM feature_requests 
                WHERE id = %s AND api_key_id = %s
            """, (request_id, getattr(request, 'api_key_id', None)))
            
            result = cursor.fetchone()
            
            if not result:
                return jsonify({
                    'error': 'Request not found'
                }), 404
            
            return jsonify({
                'status': 'success',
                'data': result
            }), 200
            
    except Exception as e:
        logger.error(f"Failed to get feature status: {e}")
        return jsonify({
            'error': 'Internal server error'
        }), 500

@your_feature_bp.route('/api/v1/your-feature/stats', methods=['GET'])
@require_api_key
def get_feature_stats():
    """
    Get statistics for your feature
    """
    try:
        # Query service for statistics
        service_url = "http://localhost:8090/metrics"
        response = requests.get(service_url, timeout=10)
        
        if response.status_code == 200:
            return jsonify({
                'status': 'success',
                'stats': parse_prometheus_metrics(response.text)
            }), 200
        else:
            return jsonify({
                'error': 'Failed to get statistics'
            }), 503
            
    except Exception as e:
        logger.error(f"Failed to get feature stats: {e}")
        return jsonify({
            'error': 'Internal server error'
        }), 500

def parse_prometheus_metrics(metrics_text: str) -> Dict[str, Any]:
    """
    Parse Prometheus metrics format into JSON
    
    Args:
        metrics_text: Raw Prometheus metrics
        
    Returns:
        Parsed metrics dictionary
    """
    metrics = {}
    for line in metrics_text.split('\n'):
        if line and not line.startswith('#'):
            parts = line.split(' ')
            if len(parts) == 2:
                metrics[parts[0]] = float(parts[1])
    return metrics
```

### **Step 2: Register API Routes**
Update `worker/api/routes/__init__.py`:

```python
"""
Route registration for API gateway
"""
from .domains import domains_bp
from .mailboxes import mailboxes_bp
from .aliases import aliases_bp
from .tracking import tracking_bp
from .webhooks import webhooks_bp
from .rate_limiter import rate_limiter_bp
from .storage import storage_bp
from .rag import rag_bp
from .queue import queue_bp
from .analytics import analytics_bp
from .your_feature import your_feature_bp  # Add your feature

def register_blueprints(app):
    """Register all route blueprints with the Flask app"""
    blueprints = [
        domains_bp,
        mailboxes_bp,
        aliases_bp,
        tracking_bp,
        webhooks_bp,
        rate_limiter_bp,
        storage_bp,
        rag_bp,
        queue_bp,
        analytics_bp,
        your_feature_bp  # Register your feature
    ]
    
    for blueprint in blueprints:
        app.register_blueprint(blueprint)
```

## 🧪 **Testing Your Feature**

### **Step 1: Unit Tests**
Create `tests/test_your_feature.py`:

```python
"""
Unit tests for Your Feature
"""
import unittest
import json
import requests
import time
from datetime import datetime

class TestYourFeature(unittest.TestCase):
    """Test cases for Your Feature service"""
    
    def setUp(self):
        """Set up test environment"""
        self.base_url = "http://localhost:8090"
        self.api_url = "http://localhost:5000"
        self.test_data = {
            'id': 'test-123',
            'type': 'type1',
            'parameters': {
                'param1': 'value1',
                'param2': 'value2'
            }
        }
        
        # Wait for services to start
        time.sleep(2)
    
    def test_health_endpoint(self):
        """Test service health endpoint"""
        response = requests.get(f"{self.base_url}/health")
        self.assertEqual(response.status_code, 200)
        
        data = response.json()
        self.assertEqual(data['status'], 'healthy')
        self.assertEqual(data['service'], 'your_feature')
        self.assertIn('checks', data)
    
    def test_metrics_endpoint(self):
        """Test metrics endpoint"""
        response = requests.get(f"{self.base_url}/metrics")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Content-Type'], 'text/plain; charset=utf-8')
    
    def test_feature_processing(self):
        """Test core feature processing"""
        response = requests.post(
            f"{self.base_url}/api/v1/your-feature/action",
            json=self.test_data,
            headers={'Content-Type': 'application/json'}
        )
        
        self.assertEqual(response.status_code, 200)
        
        data = response.json()
        self.assertEqual(data['status'], 'success')
        self.assertIn('data', data)
        self.assertIn('timestamp', data)
    
    def test_validation_errors(self):
        """Test input validation"""
        # Test missing required field
        invalid_data = {'type': 'type1'}  # Missing 'id' and 'parameters'
        
        response = requests.post(
            f"{self.base_url}/api/v1/your-feature/action",
            json=invalid_data,
            headers={'Content-Type': 'application/json'}
        )
        
        self.assertEqual(response.status_code, 400)
        
        data = response.json()
        self.assertIn('error', data)
        self.assertIn('Missing required field', data['error'])
    
    def test_api_gateway_integration(self):
        """Test API gateway integration"""
        # This test requires valid API key
        headers = {
            'X-API-Key': 'test-api-key',  # Use valid test API key
            'Content-Type': 'application/json'
        }
        
        response = requests.post(
            f"{self.api_url}/api/v1/your-feature/process",
            json=self.test_data,
            headers=headers
        )
        
        # Should return 200 if API key is valid, 401 if not
        self.assertIn(response.status_code, [200, 401])
    
    def test_concurrent_requests(self):
        """Test handling of concurrent requests"""
        import threading
        import queue
        
        results = queue.Queue()
        
        def make_request():
            try:
                response = requests.post(
                    f"{self.base_url}/api/v1/your-feature/action",
                    json=self.test_data,
                    headers={'Content-Type': 'application/json'},
                    timeout=10
                )
                results.put(response.status_code)
            except Exception as e:
                results.put(str(e))
        
        # Start multiple concurrent requests
        threads = []
        for i in range(10):
            thread = threading.Thread(target=make_request)
            threads.append(thread)
            thread.start()
        
        # Wait for all threads to complete
        for thread in threads:
            thread.join()
        
        # Check results
        success_count = 0
        while not results.empty():
            result = results.get()
            if result == 200:
                success_count += 1
        
        # At least 80% of requests should succeed
        self.assertGreaterEqual(success_count, 8)

if __name__ == '__main__':
    unittest.main()
```

### **Step 2: Integration Tests**
Add integration tests to `tests/integration_test.py`:

```python
def test_your_feature_integration(self):
    """Test your feature integration with the full system"""
    try:
        # Test service startup
        self.check_service_health('your_feature', 8090)
        
        # Test API gateway integration
        response = self.make_api_request('/api/v1/your-feature/process', {
            'id': 'integration-test',
            'type': 'type1',
            'parameters': {'test': True}
        })
        
        self.assertEqual(response.status_code, 200)
        
        # Test database integration
        # Test webhook integration
        # Test monitoring integration
        
        print("✅ Your Feature integration test passed")
        
    except Exception as e:
        print(f"❌ Your Feature integration test failed: {e}")
        raise
```

## 📚 **Documentation**

### **Step 1: Feature Documentation**
Create `docs/versions/1.0/features/your-feature.md`:

```markdown
# Your Feature

## Overview
Brief description of what your feature does and why it's useful.

## Use Cases
- Use case 1: Description
- Use case 2: Description  
- Use case 3: Description

## Configuration
Required environment variables and configuration options.

### Environment Variables
```bash
YOUR_FEATURE_ENABLED=true
YOUR_FEATURE_MAX_REQUESTS=1000
YOUR_FEATURE_TIMEOUT=30
```

## API Endpoints

### Process Feature Request
**POST** `/api/v1/your-feature/process`

Process a request using your feature.

**Headers:**
- `X-API-Key`: Your API key
- `Content-Type`: application/json

**Request Body:**
```json
{
  "id": "unique-request-id",
  "type": "type1",
  "parameters": {
    "param1": "value1",
    "param2": "value2"
  }
}
```

**Response:**
```json
{
  "status": "success",
  "data": {
    "id": "unique-request-id",
    "result": "processing result",
    "timestamp": "2024-01-01T12:00:00Z"
  }
}
```

## Examples

### Python Example
```python
import requests

response = requests.post('http://your-server:5000/api/v1/your-feature/process', 
    headers={'X-API-Key': 'your-api-key'},
    json={
        'id': 'example-1',
        'type': 'type1',
        'parameters': {'example': True}
    }
)

print(response.json())
```

### cURL Example
```bash
curl -X POST \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"id":"example-1","type":"type1","parameters":{"example":true}}' \
  http://your-server:5000/api/v1/your-feature/process
```

## Monitoring
Your feature includes built-in monitoring and metrics collection.

### Health Check
```bash
curl http://your-server:8090/health
```

### Metrics
```bash
curl http://your-server:8090/metrics
```

## Troubleshooting
Common issues and their solutions.
```

### **Step 2: API Documentation**
Create `docs/versions/1.0/api/your-feature.md`:

```markdown
# Your Feature API Reference

Complete API reference for your feature endpoints.

## Authentication
All endpoints require API key authentication.

## Endpoints

### Process Request
Process a feature request.

### Get Status  
Check the status of a request.

### Get Statistics
Retrieve feature usage statistics.

## Error Codes
List of possible error codes and their meanings.

## Rate Limits
API rate limiting information.

## Examples
Code examples in multiple languages.
```

### **Step 3: Update Navigation**
Update `docs/mkdocs.yml` to include your documentation:

```yaml
nav:
  - Features:
    - features/your-feature.md
  - API Documentation:
    - api/your-feature.md
```

## 🚀 **Deployment Integration**

### **Step 1: Update Main Deployment**
Add your service to `main.py`:

```python
def deploy_worker_services():
    """Deploy all worker services including new features"""
    worker_services = [
        # ... existing services
        {
            'name': 'your_feature',
            'path': 'worker/your_feature',
            'port': 8090,
            'health_endpoint': '/health',
            'required': True
        }
    ]
    
    for service in worker_services:
        deploy_service(service)
```

### **Step 2: Environment Configuration**
Add configuration to `.env.example`:

```bash
# Your Feature Configuration
YOUR_FEATURE_ENABLED=true
YOUR_FEATURE_PORT=8090
YOUR_FEATURE_MAX_REQUESTS=1000
YOUR_FEATURE_TIMEOUT=30
```

### **Step 3: Health Monitoring**
Your feature will automatically be included in health monitoring through the `/health` endpoint.

## ✅ **Feature Checklist**

Before submitting your feature:

### **Code Quality**
- [ ] Code follows project style guidelines
- [ ] All functions have docstrings
- [ ] Error handling is comprehensive
- [ ] Logging is implemented appropriately
- [ ] Configuration is externalized

### **Testing**
- [ ] Unit tests written and passing
- [ ] Integration tests added
- [ ] Performance testing completed
- [ ] Error scenarios tested
- [ ] Concurrent usage tested

### **Documentation**
- [ ] Feature documentation written
- [ ] API documentation updated
- [ ] Configuration documented
- [ ] Examples provided
- [ ] Troubleshooting guide included

### **Security**
- [ ] Input validation implemented
- [ ] Authentication required where appropriate
- [ ] SQL injection prevention
- [ ] Rate limiting considered
- [ ] Sensitive data protection

### **Operations**
- [ ] Health check endpoint implemented
- [ ] Metrics collection added
- [ ] Logging configuration
- [ ] Monitoring integration
- [ ] Deployment integration

This comprehensive guide provides everything needed to add robust, production-ready features to the Mailyte Mail Server. Follow these patterns to maintain consistency and quality across the codebase.
