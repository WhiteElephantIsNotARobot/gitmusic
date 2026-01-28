#!/usr/bin/env python3
"""
集成测试运行器
由于环境中缺少pytest，使用简单的测试运行器
"""
import sys
import os
import traceback
import importlib.util
from pathlib import Path

# 将项目根目录添加到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

def run_test_function(test_func, test_name):
    """运行单个测试函数"""
    try:
        print(f"Running {test_name}...")
        test_func()
        print(f"✓ {test_name} PASSED")
        return True
    except Exception as e:
        print(f"✗ {test_name} FAILED: {e}")
        traceback.print_exc()
        return False

def run_test_class(test_class, class_name):
    """运行测试类中的所有测试方法"""
    print(f"\n=== Running tests in {class_name} ===")
    
    # 获取所有测试方法（以test_开头）
    test_methods = [method for method in dir(test_class) if method.startswith('test_')]
    
    passed = 0
    failed = 0
    
    for method_name in test_methods:
        method = getattr(test_class, method_name)
        if callable(method):
            # 创建测试类实例
            try:
                test_instance = test_class()
                if hasattr(test_instance, 'setup_method'):
                    test_instance.setup_method()
                
                success = run_test_function(method, f"{class_name}.{method_name}")
                
                if hasattr(test_instance, 'teardown_method'):
                    test_instance.teardown_method()
                    
                if success:
                    passed += 1
                else:
                    failed += 1
                    
            except Exception as e:
                print(f"✗ {class_name}.{method_name} SETUP FAILED: {e}")
                traceback.print_exc()
                failed += 1
    
    print(f"\n{class_name}: {passed} passed, {failed} failed")
    return passed, failed

def import_test_module(module_path):
    """导入测试模块"""
    try:
        spec = importlib.util.spec_from_file_location("test_module", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception as e:
        print(f"Failed to import {module_path}: {e}")
        return None

def main():
    """主函数"""
    print("GitMusic Integration Test Runner")
    print("=" * 50)
    
    # 测试配置
    test_modules = [
        "tests/integration/test_command_chains.py",
        "tests/integration/test_fault_injection.py",
        "tests/integration/test_isolation.py",
        "tests/integration/test_performance.py",
        "tests/integration/test_recovery_scenarios.py",
        "tests/integration/test_edge_cases.py",
    ]
    
    total_passed = 0
    total_failed = 0
    
    for module_path in test_modules:
        module_file = project_root / module_path
        if not module_file.exists():
            print(f"Warning: {module_path} not found, skipping")
            continue
            
        print(f"\nLoading {module_path}...")
        
        # 导入测试模块
        module = import_test_module(module_file)
        if not module:
            continue
        
        # 查找测试类
        test_classes = []
        for name in dir(module):
            obj = getattr(module, name)
            if (isinstance(obj, type) and 
                name.startswith('Test') and 
                hasattr(obj, '__module__')):
                test_classes.append((name, obj))
        
        # 运行测试类
        for class_name, test_class in test_classes:
            passed, failed = run_test_class(test_class, class_name)
            total_passed += passed
            total_failed += failed
    
    # 总结结果
    print("\n" + "=" * 50)
    print(f"Test Summary: {total_passed} passed, {total_failed} failed")
    
    if total_failed > 0:
        print("Some tests failed!")
        return 1
    else:
        print("All tests passed!")
        return 0

if __name__ == "__main__":
    sys.exit(main())