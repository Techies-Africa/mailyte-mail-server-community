
#!/usr/bin/env python3
"""
Unit tests for Dashboard Service
"""

import unittest
import json
import os
import sys
from unittest.mock import patch, MagicMock

# Add the parent directory to the path so we can import our modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from worker.dashboard.app import app, DashboardService

class TestDashboardService(unittest.TestCase):
    def setUp(self):
        """Set up test environment"""
        self.app = app
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()
        
        # Mock database
        self.mock_db_config = {
            'host': 'localhost',
            'port': 3306,
            'user': 'test',
            'password': 'test',
            'database': 'test_db'
        }
    
    def test_health_endpoint(self):
        """Test health check endpoint"""
        response = self.client.get('/health')
        self.assertEqual(response.status_code, 200)
        
        data = json.loads(response.data)
        self.assertEqual(data['status'], 'healthy')
        self.assertEqual(data['service'], 'dashboard')
    
    @patch('worker.dashboard.app.connection_pool')
    def test_stats_overview_success(self, mock_pool):
        """Test successful stats overview retrieval"""
        # Mock database connection
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        
        mock_pool.get_connection.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        
        # Mock database results
        mock_cursor.fetchall.return_value = [
            {'date': '2024-01-15', 'sent_count': 100, 'delivered_count': 95, 'bounced_count': 5}
        ]
        mock_cursor.fetchone.return_value = {
            'tracked_emails': 50, 'total_opens': 30, 'unique_opens': 25,
            'total_clicks': 10, 'unique_clicks': 8
        }
        
        response = self.client.get('/api/stats/overview')
        self.assertEqual(response.status_code, 200)
        
        data = json.loads(response.data)
        self.assertTrue(data['success'])
        self.assertIn('data', data)
    
    @patch('worker.dashboard.app.connection_pool')
    def test_stats_overview_database_error(self, mock_pool):
        """Test stats overview with database error"""
        mock_pool.get_connection.side_effect = Exception("Database connection failed")
        
        response = self.client.get('/api/stats/overview?days=7')
        self.assertEqual(response.status_code, 500)
        
        data = json.loads(response.data)
        self.assertIn('error', data)
    
    @patch('worker.dashboard.app.connection_pool', None)
    def test_stats_overview_no_database(self):
        """Test stats overview with no database connection"""
        response = self.client.get('/api/stats/overview')
        self.assertEqual(response.status_code, 503)
        
        data = json.loads(response.data)
        self.assertEqual(data['error'], 'Database not available')
    
    def test_dashboard_html_render(self):
        """Test dashboard HTML rendering"""
        response = self.client.get('/dashboard')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Email Analytics Dashboard', response.data)
        self.assertIn(b'Overview Statistics', response.data)

class TestDashboardIntegration(unittest.TestCase):
    """Integration tests for dashboard functionality"""
    
    def setUp(self):
        """Set up integration test environment"""
        self.app = app
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()
    
    def test_api_endpoints_exist(self):
        """Test that all expected API endpoints exist"""
        endpoints = [
            '/health',
            '/api/stats/overview',
            '/api/stats/domains',
            '/dashboard'
        ]
        
        for endpoint in endpoints:
            response = self.client.get(endpoint)
            # Should not return 404 (Not Found)
            self.assertNotEqual(response.status_code, 404, f"Endpoint {endpoint} not found")

if __name__ == '__main__':
    # Set up test environment variables
    os.environ['DEVELOPMENT_MODE'] = 'true'
    os.environ['DEBUG_MODE'] = 'true'
    os.environ['DB_HOST'] = 'localhost'
    os.environ['DB_NAME'] = 'test_db'
    
    unittest.main()
