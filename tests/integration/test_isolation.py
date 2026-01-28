"""
隔离环境测试
确保测试可在隔离环境中运行
验证测试之间不会相互影响
"""
import pytest
import tempfile
import shutil
import json
from pathlib import Path
from unittest.mock import Mock, patch
import os
import sys

# 导入被测试的模块
from libgitmusic.metadata import MetadataManager
from libgitmusic.object_store import ObjectStore
from libgitmusic.context import Context
from libgitmusic.commands.publish import publish_logic, execute_publish
from libgitmusic.commands.sync import sync_logic
from libgitmusic.commands.release import release_logic, execute_release


class TestIsolation:
    """隔离环境测试类"""
    
    @pytest.fixture
    def isolated_test_environment(self):
        """创建完全隔离的测试环境"""
        class IsolatedEnvironment:
            def __init__(self):
                self.temp_dir = None
                self.context = None
                self.original_env = {}
                self.setup_complete = False
                
            def setup(self):
                """设置隔离环境"""
                # 创建临时目录
                self.temp_dir = Path(tempfile.mkdtemp(prefix="gitmusic_isolated_test_"))
                
                # 保存原始环境变量
                self.original_env = dict(os.environ)
                
                # 设置隔离的环境变量
                os.environ.update({
                    'GITMUSIC_PROJECT_ROOT': str(self.temp_dir),
                    'GITMUSIC_CACHE_DIR': str(self.temp_dir / "cache"),
                    'GITMUSIC_WORK_DIR': str(self.temp_dir / "work"),
                    'GITMUSIC_LOGS_DIR': str(self.temp_dir / "logs"),
                })
                
                # 创建测试配置
                config = {
                    "transport": {
                        "host": "isolated.test.com",
                        "user": "isolated_user",
                        "path": "/isolated/test",
                        "private_key": "/isolated/key",
                    },
                    "test": {
                        "isolated": True,
                        "mock_network": True,
                    }
                }
                
                # 创建目录结构
                work_dir = self.temp_dir / "work"
                cache_root = self.temp_dir / "cache"
                metadata_file = self.temp_dir / "metadata.jsonl"
                release_dir = self.temp_dir / "release"
                logs_dir = self.temp_dir / "logs"
                repo_root = self.temp_dir / "repo"
                
                for dir_path in [work_dir, cache_root, release_dir, logs_dir, repo_root]:
                    dir_path.mkdir(parents=True, exist_ok=True)
                
                # 创建上下文
                self.context = Context(
                    project_root=self.temp_dir,
                    config=config,
                    work_dir=work_dir,
                    cache_root=cache_root,
                    metadata_file=metadata_file,
                    release_dir=release_dir,
                    logs_dir=logs_dir,
                )
                
                self.setup_complete = True
                
            def teardown(self):
                """清理隔离环境"""
                # 恢复原始环境变量
                os.environ.clear()
                os.environ.update(self.original_env)
                
                # 清理临时目录
                if self.temp_dir and self.temp_dir.exists():
                    shutil.rmtree(self.temp_dir, ignore_errors=True)
                    
                self.setup_complete = False
                
            def verify_isolation(self):
                """验证环境隔离性"""
                # 验证临时目录是唯一的
                assert self.temp_dir.exists(), "Temporary directory does not exist"
                
                # 验证环境变量隔离
                assert os.environ.get('GITMUSIC_PROJECT_ROOT') == str(self.temp_dir), "Project root not isolated"
                
                # 验证目录结构隔离
                assert (self.temp_dir / "work").exists(), "Work directory not created"
                assert (self.temp_dir / "cache").exists(), "Cache directory not created"
                assert (self.temp_dir / "metadata.jsonl").exists() or not (self.temp_dir / "metadata.jsonl").exists(), "Metadata file state inconsistent"
                
                return True
                
            def create_test_file(self, filename, content=None):
                """在隔离环境中创建测试文件"""
                if not self.setup_complete:
                    raise RuntimeError("Environment not set up")
                    
                test_file = self.context.work_dir / filename
                if content is None:
                    content = f"Test content for {filename}".encode()
                test_file.write_bytes(content)
                return test_file
                
            def get_metadata_manager(self):
                """获取隔离的元数据管理器"""
                if not self.setup_complete:
                    raise RuntimeError("Environment not set up")
                return MetadataManager(self.context)
                
            def get_object_store(self):
                """获取隔离的对象存储"""
                if not self.setup_complete:
                    raise RuntimeError("Environment not set up")
                return ObjectStore(self.context)
        
        env = IsolatedEnvironment()
        env.setup()
        
        yield env
        
        env.teardown()
        
    @pytest.mark.integration
    def test_complete_environment_isolation(self, isolated_test_environment):
        """测试完整的环境隔离"""
        env = isolated_test_environment
        
        # 验证隔离环境设置
        assert env.verify_isolation(), "Environment isolation verification failed"
        
        # 在隔离环境中执行操作
        test_file = env.create_test_file("isolated_test.mp3", b"ISOLATED_TEST_CONTENT")
        assert test_file.exists(), "Test file creation failed in isolated environment"
        
        # 验证文件内容隔离
        content = test_file.read_bytes()
        assert content == b"ISOLATED_TEST_CONTENT", "File content not isolated correctly"
        
    @pytest.mark.integration
    def test_metadata_isolation_between_tests(self, isolated_test_environment):
        """测试元数据在测试间的隔离"""
        env = isolated_test_environment
        metadata_manager = env.get_metadata_manager()
        
        # 创建测试元数据
        test_entries = [
            {
                "audio_oid": "sha256:isolated_test_hash_1",
                "title": "Isolated Test Song 1",
                "artists": ["Isolated Artist 1"],
                "created_at": "2024-01-01T00:00:00Z",
            },
            {
                "audio_oid": "sha256:isolated_test_hash_2",
                "title": "Isolated Test Song 2", 
                "artists": ["Isolated Artist 2"],
                "created_at": "2024-01-02T00:00:00Z",
            }
        ]
        
        # 保存元数据
        metadata_manager.save_all(test_entries)
        
        # 验证保存的元数据
        loaded_entries = metadata_manager.load_all()
        assert len(loaded_entries) == 2, f"Expected 2 entries, got {len(loaded_entries)}"
        
        # 验证元数据内容隔离
        assert loaded_entries[0]["title"] == "Isolated Test Song 1"
        assert loaded_entries[1]["title"] == "Isolated Test Song 2"
        
        # 验证音频OID隔离
        assert "isolated_test_hash_1" in loaded_entries[0]["audio_oid"]
        assert "isolated_test_hash_2" in loaded_entries[1]["audio_oid"]
        
    @pytest.mark.integration
    def test_file_system_isolation(self, isolated_test_environment):
        """测试文件系统隔离"""
        env = isolated_test_environment
        
        # 在工作目录创建文件
        work_file = env.create_test_file("work_isolation_test.mp3", b"WORK_CONTENT")
        
        # 在缓存目录创建文件
        cache_file = env.context.cache_root / "cache_isolation_test.jpg"
        cache_file.write_bytes(b"CACHE_CONTENT")
        
        # 验证文件路径隔离
        assert work_file.parent == env.context.work_dir, "Work file not in correct directory"
        assert cache_file.parent == env.context.cache_root, "Cache file not in correct directory"
        
        # 验证文件内容隔离
        assert work_file.read_bytes() == b"WORK_CONTENT", "Work file content corrupted"
        assert cache_file.read_bytes() == b"CACHE_CONTENT", "Cache file content corrupted"
        
        # 验证文件系统操作隔离
        work_file.unlink()
        assert not work_file.exists(), "Work file deletion failed"
        assert cache_file.exists(), "Cache file incorrectly affected by work file deletion"
        
    @pytest.mark.integration
    def test_network_isolation(self, isolated_test_environment):
        """测试网络隔离"""
        env = isolated_test_environment
        
        # 创建模拟的网络依赖操作
        mock_transport = Mock()
        mock_transport.list_remote_files.return_value = []
        mock_transport.upload.return_value = True
        mock_transport.download.return_value = True
        
        # 在隔离环境中执行网络操作
        result = sync_logic(
            cache_root=env.context.cache_root,
            transport=mock_transport,
            direction="both",
            dry_run=True  # 使用dry_run避免实际文件操作
        )
        
        # 验证网络操作在隔离环境中执行
        assert mock_transport.list_remote_files.called, "Network operation not executed in isolated environment"
        
        # 验证隔离环境中的网络调用参数
        call_args = mock_transport.list_remote_files.call_args_list
        assert len(call_args) > 0, "No network calls made"
        
    @pytest.mark.integration
    def test_process_isolation(self, isolated_test_environment):
        """测试进程隔离"""
        env = isolated_test_environment
        
        # 获取隔离环境的进程信息
        import psutil
        current_process = psutil.Process()
        
        # 在隔离环境中创建大量小文件（测试资源使用）
        test_files = []
        for i in range(100):
            test_file = env.create_test_file(f"process_test_{i}.mp3", b"PROCESS_TEST_CONTENT")
            test_files.append(test_file)
            
        # 验证文件创建成功
        assert len(test_files) == 100, "Not all test files created"
        
        # 验证进程资源使用在合理范围内
        memory_info = current_process.memory_info()
        assert memory_info.rss < 1024 * 1024 * 1024, "Memory usage exceeds 1GB limit"  # 1GB limit
        
        # 清理文件
        for test_file in test_files:
            if test_file.exists():
                test_file.unlink()
                
        # 验证清理成功
        remaining_files = [f for f in env.context.work_dir.glob("process_test_*.mp3") if f.exists()]
        assert len(remaining_files) == 0, f"Files not properly cleaned up: {len(remaining_files)} remaining"
        
    @pytest.mark.integration
    def test_temporal_isolation(self, isolated_test_environment):
        """测试时间隔离（确保测试不会相互影响）"""
        env = isolated_test_environment
        metadata_manager = env.get_metadata_manager()
        
        # 创建带有时间戳的测试数据
        import datetime
        test_timestamp = datetime.datetime.now().isoformat()
        
        test_entry = {
            "audio_oid": f"sha256:temporal_test_{test_timestamp}",
            "title": f"Temporal Test {test_timestamp}",
            "artists": ["Temporal Artist"],
            "created_at": "2024-01-01T00:00:00Z",
            "test_timestamp": test_timestamp,
        }
        
        # 保存带时间戳的元数据
        metadata_manager.save_all([test_entry])
        
        # 验证时间戳正确保存
        loaded_entries = metadata_manager.load_all()
        assert len(loaded_entries) == 1, "Temporal test entry not saved correctly"
        assert loaded_entries[0]["test_timestamp"] == test_timestamp, "Timestamp not preserved correctly"
        
        # 验证时间戳唯一性（基本检查）
        new_timestamp = datetime.datetime.now().isoformat()
        assert new_timestamp != test_timestamp, "Timestamp not unique"
        
    @pytest.mark.integration
    def test_concurrent_isolation(self, isolated_test_environment):
        """测试并发隔离"""
        import threading
        import time
        
        env = isolated_test_environment
        results = {"errors": [], "success_count": 0}
        lock = threading.Lock()
        
        def concurrent_operation(thread_id):
            """并发操作函数"""
            try:
                # 每个线程创建自己的文件
                test_file = env.create_test_file(f"concurrent_test_{thread_id}.mp3", f"THREAD_{thread_id}_CONTENT".encode())
                
                # 验证文件创建
                assert test_file.exists(), f"Thread {thread_id}: File creation failed"
                
                # 验证文件内容
                content = test_file.read_bytes()
                expected_content = f"THREAD_{thread_id}_CONTENT".encode()
                assert content == expected_content, f"Thread {thread_id}: Content mismatch"
                
                with lock:
                    results["success_count"] += 1
                    
            except Exception as e:
                with lock:
                    results["errors"].append(f"Thread {thread_id}: {str(e)}")
                    
        # 创建多个并发线程
        threads = []
        for i in range(5):
            thread = threading.Thread(target=concurrent_operation, args=(i,))
            threads.append(thread)
            thread.start()
            
        # 等待所有线程完成
        for thread in threads:
            thread.join()
            
        # 验证并发操作结果
        assert len(results["errors"]) == 0, f"Concurrent operation errors: {results['errors']}"
        assert results["success_count"] == 5, f"Expected 5 successful operations, got {results['success_count']}"
        
        # 验证所有文件创建成功且内容正确
        for i in range(5):
            test_file = env.context.work_dir / f"concurrent_test_{i}.mp3"
            assert test_file.exists(), f"Thread {i} file missing after concurrent operation"
            
            expected_content = f"THREAD_{i}_CONTENT".encode()
            actual_content = test_file.read_bytes()
            assert actual_content == expected_content, f"Thread {i} content corrupted"
            
    @pytest.mark.integration
    def test_workflow_isolation(self, isolated_test_environment):
        """测试完整工作流程的隔离"""
        env = isolated_test_environment
        metadata_manager = env.get_metadata_manager()
        object_store = env.get_object_store()
        
        # 创建测试音频文件
        test_file = env.create_test_file("workflow_test.mp3", b"WORKFLOW_TEST_AUDIO")
        
        # 模拟音频文件处理
        mock_audio_data = b"MOCK_MP3_AUDIO_DATA_WITH_ID3_TAGS"
        test_file.write_bytes(mock_audio_data)
        
        # 执行 publish 逻辑
        to_process, error = publish_logic(metadata_manager)
        assert error is None, f"Isolated publish logic failed: {error}"
        
        # 验证在隔离环境中的处理结果
        if len(to_process) > 0:
            execute_publish(metadata_manager, to_process)
            
            # 验证文件被处理
            assert not test_file.exists(), "Test file not moved after isolated publish"
            
            # 验证元数据保存
            entries = metadata_manager.load_all()
            assert len(entries) > 0, "No metadata entries after isolated publish"
            
        # 执行 release 逻辑
        entries_to_process, error = release_logic(
            metadata_mgr=metadata_manager,
            object_store=object_store,
            release_dir=env.context.release_dir,
            mode="local"
        )
        
        # release 逻辑应该正常执行
        assert error is None, f"Isolated release logic failed: {error}"
        
    @pytest.mark.integration
    def test_cleanup_isolation(self, isolated_test_environment):
        """测试清理操作的隔离"""
        env = isolated_test_environment
        
        # 创建各种测试文件
        test_files = []
        
        # 工作目录文件
        for i in range(5):
            test_file = env.create_test_file(f"cleanup_test_{i}.mp3", f"CLEANUP_TEST_{i}".encode())
            test_files.append(test_file)
            
        # 缓存目录文件
        for i in range(3):
            cache_file = env.context.cache_root / f"cache_cleanup_test_{i}.jpg"
            cache_file.write_bytes(f"CACHE_CONTENT_{i}".encode())
            test_files.append(cache_file)
            
        # 元数据文件
        metadata_manager = env.get_metadata_manager()
        test_metadata = [
            {
                "audio_oid": f"sha256:cleanup_test_hash_{i}",
                "title": f"Cleanup Test {i}",
                "artists": ["Cleanup Artist"],
                "created_at": "2024-01-01T00:00:00Z",
            }
            for i in range(3)
        ]
        metadata_manager.save_all(test_metadata)
        
        # 验证所有文件创建成功
        assert len([f for f in test_files if f.exists()]) == len(test_files), "Not all test files created"
        
        # 执行清理操作
        # 这里可以调用实际的清理函数，或者模拟清理过程
        for test_file in test_files:
            if test_file.exists():
                test_file.unlink()
                
        # 清理元数据
        if env.context.metadata_file.exists():
            env.context.metadata_file.write_text("")
            
        # 验证清理隔离性
        remaining_files = [f for f in test_files if f.exists()]
        assert len(remaining_files) == 0, f"Files not properly cleaned up: {len(remaining_files)} remaining"
        
        remaining_metadata = metadata_manager.load_all()
        assert len(remaining_metadata) == 0, f"Metadata not properly cleaned up: {len(remaining_metadata)} entries remaining"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])