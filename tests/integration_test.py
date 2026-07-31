#!/usr/bin/env python3
"""
Integration Tests for Mailyte Mail Server
Tests inter-service communication and API endpoints
"""

import requests
import json
import time
import os
import sys
from concurrent.futures import ThreadPoolExecutor
import threading


class IntegrationTester:
    def __init__(self):
        self.base_url = "http://0.0.0.0"
        self.services = {
            "api": 5000,
            "webhooks": 8081,
            "tracking": 8083,
            "rate_limiter": 8082,
            "storage": 8084,
            "health_monitor": 8080,
            "dashboard": 8085,
        }
        self.results = {}

    def test_service_health(self, service_name, port):
        """Test health endpoint for a service"""
        try:
            url = f"{self.base_url}:{port}/health"
            response = requests.get(url, timeout=10)

            if response.status_code == 200:
                data = response.json()
                return {
                    "status": "pass",
                    "response_time": response.elapsed.total_seconds(),
                    "data": data,
                }
            else:
                return {
                    "status": "fail",
                    "error": f"HTTP {response.status_code}",
                    "response_time": response.elapsed.total_seconds(),
                }

        except Exception as e:
            return {"status": "fail", "error": str(e), "response_time": None}

    def test_api_functionality(self):
        """Test API service functionality"""
        api_url = f"{self.base_url}:5000"
        tests = []

        # Test API endpoints
        endpoints = [
            {"path": "/api/v1/health", "method": "GET"},
            {"path": "/api/v1/domains", "method": "GET"},
            {"path": "/api/v1/mailboxes", "method": "GET"},
        ]

        for endpoint in endpoints:
            try:
                url = f"{api_url}{endpoint['path']}"
                if endpoint["method"] == "GET":
                    response = requests.get(url, timeout=10)
                    tests.append(
                        {
                            "endpoint": endpoint["path"],
                            "status": "pass" if response.status_code in [200, 401] else "fail",
                            "status_code": response.status_code,
                            "response_time": response.elapsed.total_seconds(),
                        }
                    )
            except Exception as e:
                tests.append({"endpoint": endpoint["path"], "status": "fail", "error": str(e)})

        return tests

    def test_webhook_functionality(self):
        """Test webhook service functionality"""
        webhook_url = f"{self.base_url}:8081"

        try:
            # Test webhook health
            response = requests.get(f"{webhook_url}/health", timeout=10)
            if response.status_code == 200:
                return {"status": "pass", "message": "Webhook service responsive"}
            else:
                return {"status": "fail", "error": f"HTTP {response.status_code}"}
        except Exception as e:
            return {"status": "fail", "error": str(e)}

    def test_tracking_functionality(self):
        """Test email tracking functionality"""
        tracking_url = f"{self.base_url}:8083"

        try:
            # Test tracking pixel endpoint
            response = requests.get(f"{tracking_url}/track/pixel/test-message-id", timeout=10)

            # Should return a 1x1 pixel image
            if response.status_code == 200 and "image" in response.headers.get("content-type", ""):
                return {"status": "pass", "message": "Tracking pixel working"}
            else:
                return {
                    "status": "partial",
                    "message": "Tracking endpoint responsive but unexpected content",
                }

        except Exception as e:
            return {"status": "fail", "error": str(e)}

    def test_cross_service_communication(self):
        """Test communication between services"""
        tests = []

        # Test API -> Rate Limiter communication
        try:
            # This would test if API service can reach rate limiter
            api_health = requests.get(f"{self.base_url}:5000/health", timeout=5)
            rate_limiter_health = requests.get(f"{self.base_url}:8082/health", timeout=5)

            if api_health.status_code == 200 and rate_limiter_health.status_code == 200:
                tests.append(
                    {
                        "test": "API <-> Rate Limiter",
                        "status": "pass",
                        "message": "Both services responsive",
                    }
                )
            else:
                tests.append(
                    {
                        "test": "API <-> Rate Limiter",
                        "status": "fail",
                        "message": "One or both services not responsive",
                    }
                )
        except Exception as e:
            tests.append({"test": "API <-> Rate Limiter", "status": "fail", "error": str(e)})

        return tests

    def run_all_tests(self):
        """Run all integration tests"""
        print("🧪 Starting Integration Tests for Mailyte Mail Server")
        print("=" * 60)

        # Test 1: Service Health Checks
        print("\n1️⃣  Testing Service Health...")
        health_results = {}

        with ThreadPoolExecutor(max_workers=len(self.services)) as executor:
            future_to_service = {
                executor.submit(self.test_service_health, name, port): name
                for name, port in self.services.items()
            }

            for future in future_to_service:
                service_name = future_to_service[future]
                try:
                    result = future.result()
                    health_results[service_name] = result

                    status_emoji = "✅" if result["status"] == "pass" else "❌"
                    print(f"   {status_emoji} {service_name}: {result['status']}")

                except Exception as e:
                    health_results[service_name] = {"status": "error", "error": str(e)}
                    print(f"   ❌ {service_name}: error - {e}")

        # Test 2: API Functionality
        print("\n2️⃣  Testing API Functionality...")
        api_tests = self.test_api_functionality()
        for test in api_tests:
            status_emoji = "✅" if test["status"] == "pass" else "❌"
            print(f"   {status_emoji} {test['endpoint']}: {test['status']}")

        # Test 3: Webhook Functionality
        print("\n3️⃣  Testing Webhook Functionality...")
        webhook_test = self.test_webhook_functionality()
        status_emoji = "✅" if webhook_test["status"] == "pass" else "❌"
        print(f"   {status_emoji} Webhook Service: {webhook_test['status']}")

        # Test 4: Tracking Functionality
        print("\n4️⃣  Testing Tracking Functionality...")
        tracking_test = self.test_tracking_functionality()
        status_emoji = "✅" if tracking_test["status"] == "pass" else "❌"
        print(f"   {status_emoji} Tracking Service: {tracking_test['status']}")

        # Test 5: Cross-Service Communication
        print("\n5️⃣  Testing Cross-Service Communication...")
        comm_tests = self.test_cross_service_communication()
        for test in comm_tests:
            status_emoji = "✅" if test["status"] == "pass" else "❌"
            print(f"   {status_emoji} {test['test']}: {test['status']}")

        # Summary
        print("\n📊 Test Summary")
        print("-" * 30)

        total_services = len(self.services)
        healthy_services = sum(
            1 for result in health_results.values() if result["status"] == "pass"
        )

        print(f"Service Health: {healthy_services}/{total_services} services healthy")
        print(
            f"API Tests: {sum(1 for test in api_tests if test['status'] == 'pass')}/{len(api_tests)} passed"
        )
        print(f"Webhook Test: {'Pass' if webhook_test['status'] == 'pass' else 'Fail'}")
        print(f"Tracking Test: {'Pass' if tracking_test['status'] == 'pass' else 'Fail'}")
        print(
            f"Communication Tests: {sum(1 for test in comm_tests if test['status'] == 'pass')}/{len(comm_tests)} passed"
        )

        overall_health = (healthy_services / total_services) * 100
        if overall_health >= 80:
            print(f"\n🎉 Overall System Health: {overall_health:.1f}% - Good!")
        elif overall_health >= 60:
            print(f"\n⚠️  Overall System Health: {overall_health:.1f}% - Needs Attention")
        else:
            print(f"\n🚨 Overall System Health: {overall_health:.1f}% - Critical Issues")

        return {
            "health_results": health_results,
            "api_tests": api_tests,
            "webhook_test": webhook_test,
            "tracking_test": tracking_test,
            "communication_tests": comm_tests,
            "overall_health": overall_health,
        }


if __name__ == "__main__":
    print("🚀 Mailyte Mail Server Integration Tester")
    print("Make sure all services are running before starting tests")

    if len(sys.argv) > 1 and sys.argv[1] == "--wait":
        print("⏳ Waiting 10 seconds for services to start...")
        time.sleep(10)

    tester = IntegrationTester()
    results = tester.run_all_tests()

    # Save results to file
    with open("integration_test_results.json", "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n💾 Test results saved to: integration_test_results.json")
