
#!/usr/bin/env python3
"""
Quick Test Runner for Development
Provides easy commands for testing various aspects of the system
"""

import subprocess
import sys
import os
import time
import requests

def run_unit_tests():
    """Run unit tests"""
    print("🧪 Running Unit Tests...")
    try:
        result = subprocess.run([
            'python3', '-m', 'pytest', 'tests/', '-v', '--tb=short'
        ], capture_output=True, text=True)
        
        print(result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr)
            
        return result.returncode == 0
    except FileNotFoundError:
        print("❌ pytest not found, running with unittest")
        try:
            result = subprocess.run([
                'python3', '-m', 'unittest', 'discover', 'tests/', '-v'
            ], capture_output=True, text=True)
            
            print(result.stdout)
            if result.stderr:
                print("STDERR:", result.stderr)
                
            return result.returncode == 0
        except Exception as e:
            print(f"❌ Error running tests: {e}")
            return False

def run_integration_tests():
    """Run integration tests"""
    print("🔗 Running Integration Tests...")
    try:
        result = subprocess.run([
            'python3', 'tests/integration_test.py'
        ], capture_output=True, text=True)
        
        print(result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr)
            
        return result.returncode == 0
    except Exception as e:
        print(f"❌ Error running integration tests: {e}")
        return False

def test_api_endpoints():
    """Quick test of API endpoints"""
    print("🔌 Testing API Endpoints...")
    
    endpoints = [
        ('http://0.0.0.0:5000/health', 'API Health'),
        ('http://0.0.0.0:8081/health', 'Webhook Health'),
        ('http://0.0.0.0:8083/health', 'Tracking Health'),
        ('http://0.0.0.0:8082/health', 'Rate Limiter Health'),
        ('http://0.0.0.0:8084/health', 'Storage Health'),
        ('http://0.0.0.0:8080/health', 'Health Monitor'),
        ('http://0.0.0.0:8085/health', 'Dashboard Health'),
    ]
    
    results = []
    for url, name in endpoints:
        try:
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                print(f"✅ {name}: OK")
                results.append(True)
            else:
                print(f"❌ {name}: HTTP {response.status_code}")
                results.append(False)
        except Exception as e:
            print(f"❌ {name}: {e}")
            results.append(False)
    
    success_rate = (sum(results) / len(results)) * 100
    print(f"\n📊 API Health: {success_rate:.1f}% ({sum(results)}/{len(results)} services)")
    return success_rate >= 80

def lint_code():
    """Run code linting"""
    print("🧹 Running Code Linting...")
    
    # Try flake8 first
    try:
        result = subprocess.run([
            'python3', '-m', 'flake8', 'worker/', 'health_monitor/', 'database/', 
            '--max-line-length=100', '--ignore=E501,W503'
        ], capture_output=True, text=True)
        
        if result.stdout:
            print("Flake8 Issues:")
            print(result.stdout)
        else:
            print("✅ No linting issues found")
            
        return len(result.stdout.strip()) == 0
        
    except FileNotFoundError:
        print("⚠️  flake8 not found, skipping linting")
        return True

def check_dependencies():
    """Check if all dependencies are available"""
    print("📦 Checking Dependencies...")
    
    required_packages = [
        'flask', 'mysql-connector-python', 'requests', 
        'qdrant-client', 'sentence-transformers'
    ]
    
    missing = []
    for package in required_packages:
        try:
            __import__(package.replace('-', '_'))
            print(f"✅ {package}")
        except ImportError:
            print(f"❌ {package} - Missing")
            missing.append(package)
    
    if missing:
        print(f"\n📥 Install missing packages with:")
        print(f"pip install {' '.join(missing)}")
        return False
    
    print("✅ All dependencies available")
    return True

def main():
    """Main test runner"""
    if len(sys.argv) < 2:
        print("""
🧪 Mailyte Mail Server Test Runner

Usage: python3 test_runner.py [command]

Commands:
    unit         - Run unit tests
    integration  - Run integration tests  
    api          - Quick API endpoint tests
    lint         - Run code linting
    deps         - Check dependencies
    all          - Run all tests
    quick        - Run API tests + unit tests (fast)

Examples:
    python3 test_runner.py unit
    python3 test_runner.py all
    python3 test_runner.py quick
        """)
        return
    
    command = sys.argv[1].lower()
    
    if command == 'unit':
        success = run_unit_tests()
        sys.exit(0 if success else 1)
        
    elif command == 'integration':
        success = run_integration_tests()
        sys.exit(0 if success else 1)
        
    elif command == 'api':
        success = test_api_endpoints()
        sys.exit(0 if success else 1)
        
    elif command == 'lint':
        success = lint_code()
        sys.exit(0 if success else 1)
        
    elif command == 'deps':
        success = check_dependencies()
        sys.exit(0 if success else 1)
        
    elif command == 'quick':
        print("🚀 Running Quick Tests...")
        api_ok = test_api_endpoints()
        unit_ok = run_unit_tests()
        
        if api_ok and unit_ok:
            print("🎉 Quick tests passed!")
            sys.exit(0)
        else:
            print("❌ Some quick tests failed")
            sys.exit(1)
            
    elif command == 'all':
        print("🔥 Running All Tests...")
        
        deps_ok = check_dependencies()
        lint_ok = lint_code()
        api_ok = test_api_endpoints()
        unit_ok = run_unit_tests()
        integration_ok = run_integration_tests()
        
        total_tests = 5
        passed_tests = sum([deps_ok, lint_ok, api_ok, unit_ok, integration_ok])
        
        print(f"\n📊 Overall Results: {passed_tests}/{total_tests} test suites passed")
        
        if passed_tests == total_tests:
            print("🎉 All tests passed! System is ready for deployment.")
            sys.exit(0)
        else:
            print("❌ Some tests failed. Review the output above.")
            sys.exit(1)
    
    else:
        print(f"❌ Unknown command: {command}")
        sys.exit(1)

if __name__ == '__main__':
    main()
