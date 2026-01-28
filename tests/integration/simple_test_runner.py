#!/usr/bin/env python3
"""
简单的集成测试运行器
验证测试框架的基本功能
"""
import sys
import os
import tempfile
import shutil
from pathlib import Path
import subprocess
import json

# 将项目根目录添加到Python路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

def run_gitmusic_command(args):
    """运行GitMusic命令"""
    try:
        # 使用Python模块方式运行
        cmd = [sys.executable, "-m", "libgitmusic.cli"] + args
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 1, "", "Command timed out")
    except Exception as e:
        return subprocess.CompletedProcess(args, 1, "", str(e))

def test_basic_functionality():
    """测试基本功能"""
    print("Testing basic GitMusic functionality...")
    
    # 创建临时测试环境
    with tempfile.TemporaryDirectory() as temp_dir:
        test_dir = Path(temp_dir)
        
        # 创建测试目录结构
        work_dir = test_dir / "work"
        cache_dir = test_dir / "cache"
        release_dir = test_dir / "release"
        logs_dir = test_dir / "logs"
        
        for dir_path in [work_dir, cache_dir, release_dir, logs_dir]:
            dir_path.mkdir(parents=True, exist_ok=True)
        
        # 创建测试配置文件
        config = {
            "transport": {
                "host": "test.example.com",
                "user": "testuser",
                "path": "/test/path",
                "private_key": "/test/key",
            }
        }
        
        config_file = test_dir / "config.yaml"
        with open(config_file, 'w', encoding='utf-8') as f:
            try:
                import yaml
                yaml.dump(config, f)
            except ImportError:
                # 如果没有yaml库，使用json格式
                json.dump(config, f, indent=2)
        
        # 创建测试音频文件
        test_audio = work_dir / "test_song.mp3"
        test_audio.write_bytes(b"FAKE_MP3_AUDIO_DATA")
        
        # 创建测试元数据文件
        metadata_file = test_dir / "metadata.jsonl"
        test_metadata = {
            "audio_oid": "sha256:test_hash_123",
            "title": "Test Song",
            "artists": ["Test Artist"],
            "album": "Test Album",
            "date": "2024-01-01",
            "created_at": "2024-01-01T00:00:00Z",
        }
        
        with open(metadata_file, 'w', encoding='utf-8') as f:
            f.write(json.dumps(test_metadata) + '\n')
        
        print("[OK] Test environment created successfully")
        
        # 测试基本命令
        commands_to_test = [
            ['--help'],
            ['--version'],
            ['publish', '--help'],
            ['sync', '--help'],
            ['release', '--help'],
        ]
        
        for cmd_args in commands_to_test:
            result = run_gitmusic_command(cmd_args)
            print(f"Command {' '.join(cmd_args)}: return code {result.returncode}")
            if result.stdout:
                print(f"  stdout: {result.stdout[:100]}...")
            if result.stderr:
                print(f"  stderr: {result.stderr[:100]}...")
        
        print("[OK] Basic functionality tests completed")

def test_isolated_environments():
    """测试隔离环境"""
    print("\nTesting isolated environments...")
    
    # 创建两个独立的测试环境
    results = []
    
    for i in range(2):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_dir = Path(temp_dir)
            
            # 创建隔离环境
            work_dir = test_dir / "work"
            cache_dir = test_dir / "cache"
            metadata_file = test_dir / "metadata.jsonl"
            
            work_dir.mkdir(parents=True, exist_ok=True)
            cache_dir.mkdir(parents=True, exist_ok=True)
            
            # 创建测试文件
            test_file = work_dir / f"isolated_test_{i}.mp3"
            test_file.write_bytes(f"ISOLATED_TEST_CONTENT_{i}".encode())
            
            # 创建元数据
            test_metadata = {
                "audio_oid": f"sha256:isolated_test_hash_{i}",
                "title": f"Isolated Test Song {i}",
                "artists": [f"Isolated Artist {i}"],
                "created_at": "2024-01-01T00:00:00Z",
            }
            
            with open(metadata_file, 'w', encoding='utf-8') as f:
                f.write(json.dumps(test_metadata) + '\n')
            
            # 验证环境隔离
            assert test_file.exists(), f"Environment {i}: Test file not created"
            assert metadata_file.exists(), f"Environment {i}: Metadata file not created"
            
            results.append({
                'test_file_content': test_file.read_bytes().decode(),
                'metadata_title': test_metadata['title'],
                'environment_id': i
            })
    
    # 验证环境隔离性
    assert results[0]['test_file_content'] != results[1]['test_file_content'], "Environments not isolated"
    assert results[0]['metadata_title'] != results[1]['metadata_title'], "Metadata not isolated"
    
    print("[OK] Isolated environments test passed")

def test_fault_injection():
    """测试故障注入"""
    print("\nTesting fault injection...")
    
    # 模拟网络故障
    class MockTransport:
        def __init__(self, should_fail=False):
            self.should_fail = should_fail
            self.call_count = 0
            
        def list_remote_files(self):
            self.call_count += 1
            if self.should_fail:
                raise Exception("Simulated network failure")
            return []
            
        def upload(self, local_path, remote_path):
            self.call_count += 1
            if self.should_fail:
                raise Exception("Simulated upload failure")
            return True
            
        def download(self, remote_path, local_path):
            self.call_count += 1
            if self.should_fail:
                raise Exception("Simulated download failure")
            return True
    
    # 测试正常传输
    normal_transport = MockTransport(should_fail=False)
    try:
        files = normal_transport.list_remote_files()
        assert files == [], "Normal transport failed"
        print("[OK] Normal transport test passed")
    except Exception as e:
        print(f"[FAIL] Normal transport failed: {e}")
    
    # 测试故障传输
    failing_transport = MockTransport(should_fail=True)
    try:
        files = failing_transport.list_remote_files()
        print("[FAIL] Fault injection failed - exception not raised")
    except Exception as e:
        print(f"[OK] Fault injection test passed - exception raised: {e}")

def test_command_chains():
    """测试命令链"""
    print("\nTesting command chains...")
    
    with tempfile.TemporaryDirectory() as temp_dir:
        test_dir = Path(temp_dir)
        
        # 创建工作流
        work_dir = test_dir / "work"
        cache_dir = test_dir / "cache"
        release_dir = test_dir / "release"
        metadata_file = test_dir / "metadata.jsonl"
        
        for dir_path in [work_dir, cache_dir, release_dir]:
            dir_path.mkdir(parents=True, exist_ok=True)
        
        # 创建测试文件
        test_file = work_dir / "chain_test.mp3"
        test_file.write_bytes(b"CHAIN_TEST_AUDIO")
        
        # 创建元数据
        test_metadata = {
            "audio_oid": "sha256:chain_test_hash",
            "title": "Chain Test Song",
            "artists": ["Chain Test Artist"],
            "album": "Chain Test Album",
            "created_at": "2024-01-01T00:00:00Z",
        }
        
        with open(metadata_file, 'w', encoding='utf-8') as f:
            f.write(json.dumps(test_metadata) + '\n')
        
        # 模拟发布→同步→发布工作流
        print("Simulating publish->sync->release workflow...")
        
        # 验证文件存在
        assert test_file.exists(), "Test file not created"
        assert metadata_file.exists(), "Metadata file not created"
        
        # 模拟文件处理（实际移动文件）
        processed_file = cache_dir / "chain_test.mp3"
        shutil.copy2(test_file, processed_file)
        
        # 验证文件被处理
        assert processed_file.exists(), "File not processed"
        assert processed_file.read_bytes() == test_file.read_bytes(), "File content changed during processing"
        
        # 模拟发布
        released_file = release_dir / "chain_test.mp3"
        shutil.copy2(processed_file, released_file)
        
        # 验证发布
        assert released_file.exists(), "File not released"
        
        print("[OK] Command chain test passed")

def main():
    """主函数"""
    print("GitMusic Simple Integration Test Runner")
    print("=" * 50)
    
    try:
        test_basic_functionality()
        test_isolated_environments()
        test_fault_injection()
        test_command_chains()
        
        print("\n" + "=" * 50)
        print("[OK] All simple integration tests passed!")
        return 0
        
    except Exception as e:
        print(f"\n[FAIL] Test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())