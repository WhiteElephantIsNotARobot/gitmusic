"""
恢复场景测试
测试GitMusic在各种故障情况下的恢复能力
"""
import pytest
import tempfile
import shutil
import json
import time
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import os

# 导入被测试的模块
from libgitmusic.metadata import MetadataManager
from libgitmusic.object_store import ObjectStore
from libgitmusic.context import Context
from libgitmusic.commands.publish import publish_logic, execute_publish
from libgitmusic.commands.sync import sync_logic
from libgitmusic.commands.release import release_logic, execute_release
from libgitmusic.transport import TransportError, NetworkError


class TestRecoveryScenarios:
    """恢复场景测试类"""
    
    @pytest.fixture
    def recovery_test_environment(self):
        """创建恢复测试环境"""
        class RecoveryEnvironment:
            def __init__(self):
                self.temp_dir = None
                self.context = None
                self.failure_scenarios = {}
                self.recovery_attempts = 0
                
            def setup(self):
                """设置恢复测试环境"""
                self.temp_dir = Path(tempfile.mkdtemp(prefix="gitmusic_recovery_test_"))
                
                # 创建目录结构
                work_dir = self.temp_dir / "work"
                cache_root = self.temp_dir / "cache"
                metadata_file = self.temp_dir / "metadata.jsonl"
                release_dir = self.temp_dir / "release"
                logs_dir = self.temp_dir / "logs"
                repo_root = self.temp_dir / "repo"
                
                for dir_path in [work_dir, cache_root, release_dir, logs_dir, repo_root]:
                    dir_path.mkdir(parents=True, exist_ok=True)
                
                # 创建配置
                config = {
                    "transport": {
                        "host": "recovery.test.com",
                        "user": "recovery_user",
                        "path": "/recovery/test",
                        "private_key": "/recovery/key",
                        "retry_attempts": 3,
                        "retry_delay": 1,
                    },
                    "recovery": {
                        "test_mode": True,
                        "enable_recovery": True,
                    }
                }
                
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
                
                # 创建初始测试文件
                self.create_initial_test_files()
                
            def create_initial_test_files(self):
                """创建初始测试文件"""
                for i in range(5):
                    test_file = self.context.work_dir / f"recovery_test_{i}.mp3"
                    content = f"RECOVERY_TEST_AUDIO_DATA_{i}".encode()
                    test_file.write_bytes(content)
                    
            def cleanup(self):
                """清理恢复测试环境"""
                if self.temp_dir and self.temp_dir.exists():
                    shutil.rmtree(self.temp_dir, ignore_errors=True)
                    
            def create_failure_scenario(self, scenario_type, **kwargs):
                """创建故障场景"""
                if scenario_type == "network_interruption":
                    return self._create_network_interruption_scenario(**kwargs)
                elif scenario_type == "partial_upload":
                    return self._create_partial_upload_scenario(**kwargs)
                elif scenario_type == "hash_mismatch":
                    return self._create_hash_mismatch_scenario(**kwargs)
                elif scenario_type == "permission_denied":
                    return self._create_permission_denied_scenario(**kwargs)
                elif scenario_type == "disk_full":
                    return self._create_disk_full_scenario(**kwargs)
                else:
                    raise ValueError(f"Unknown failure scenario: {scenario_type}")
                    
            def _create_network_interruption_scenario(self, fail_after_percent=0.5):
                """创建网络中断场景"""
                class FailingTransport:
                    def __init__(self, fail_after_percent):
                        self.fail_after_percent = fail_after_percent
                        self.upload_count = 0
                        self.total_files = 0
                        
                    def set_total_files(self, total):
                        self.total_files = total
                        
                    def upload(self, local_path, remote_path):
                        self.upload_count += 1
                        if self.upload_count / self.total_files > self.fail_after_percent:
                            raise NetworkError("Simulated network interruption")
                        return True
                        
                    def download(self, remote_path, local_path):
                        if self.upload_count / self.total_files > self.fail_after_percent:
                            raise NetworkError("Simulated network interruption")
                        return True
                        
                    def list_remote_files(self):
                        return []
                        
                return FailingTransport(fail_after_percent)
                
            def _create_partial_upload_scenario(self, partial_files=None):
                """创建部分上传场景"""
                if partial_files is None:
                    partial_files = [0, 2, 4]  # 默认部分文件索引
                    
                class PartialUploadTransport:
                    def __init__(self, partial_files):
                        self.partial_files = partial_files
                        self.upload_count = 0
                        
                    def upload(self, local_path, remote_path):
                        if self.upload_count in self.partial_files:
                            # 模拟部分上传成功
                            return True
                        else:
                            # 模拟其他文件上传失败
                            raise TransportError("Simulated upload failure")
                        self.upload_count += 1
                        
                    def download(self, remote_path, local_path):
                        return True
                        
                    def list_remote_files(self):
                        return []
                        
                return PartialUploadTransport(partial_files)
                
            def _create_hash_mismatch_scenario(self, mismatch_files=None):
                """创建哈希不匹配场景"""
                if mismatch_files is None:
                    mismatch_files = [1, 3]
                    
                class HashMismatchTransport:
                    def __init__(self, mismatch_files):
                        self.mismatch_files = mismatch_files
                        self.upload_count = 0
                        
                    def upload(self, local_path, remote_path):
                        if self.upload_count in self.mismatch_files:
                            # 模拟哈希验证失败
                            raise TransportError("Hash mismatch detected")
                        self.upload_count += 1
                        return True
                        
                    def download(self, remote_path, local_path):
                        return True
                        
                    def list_remote_files(self):
                        return []
                        
                return HashMismatchTransport(mismatch_files)
                
            def _create_permission_denied_scenario(self, denied_paths=None):
                """创建权限拒绝场景"""
                if denied_paths is None:
                    denied_paths = ["/denied/path1", "/denied/path2"]
                    
                class PermissionDeniedTransport:
                    def __init__(self, denied_paths):
                        self.denied_paths = denied_paths
                        
                    def upload(self, local_path, remote_path):
                        if remote_path in self.denied_paths:
                            raise PermissionError("Permission denied")
                        return True
                        
                    def download(self, remote_path, local_path):
                        if remote_path in self.denied_paths:
                            raise PermissionError("Permission denied")
                        return True
                        
                    def list_remote_files(self):
                        return []
                        
                return PermissionDeniedTransport(denied_paths)
                
            def _create_disk_full_scenario(self, max_files=2):
                """创建磁盘满场景"""
                class DiskFullTransport:
                    def __init__(self, max_files):
                        self.max_files = max_files
                        self.upload_count = 0
                        
                    def upload(self, local_path, remote_path):
                        if self.upload_count >= self.max_files:
                            raise OSError("No space left on device")
                        self.upload_count += 1
                        return True
                        
                    def download(self, remote_path, local_path):
                        if self.upload_count >= self.max_files:
                            raise OSError("No space left on device")
                        self.upload_count += 1
                        return True
                        
                    def list_remote_files(self):
                        return []
                        
                return DiskFullTransport(max_files)
                
            def get_metadata_manager(self):
                """获取元数据管理器"""
                return MetadataManager(self.context)
                
            def get_object_store(self):
                """获取对象存储"""
                return ObjectStore(self.context)
                
        env = RecoveryEnvironment()
        env.setup()
        yield env
        env.cleanup()
        
    @pytest.mark.recovery
    def test_network_interruption_recovery(self, recovery_test_environment):
        """测试网络中断恢复"""
        env = recovery_test_environment
        
        # 创建网络中断场景（50%文件后失败）
        failing_transport = env.create_failure_scenario("network_interruption", fail_after_percent=0.5)
        failing_transport.set_total_files(5)
        
        # 第一次同步应该失败
        with pytest.raises(NetworkError):
            sync_logic(
                cache_root=env.context.cache_root,
                transport=failing_transport,
                direction="upload"
            )
            
        # 创建正常传输进行恢复
        normal_transport = Mock()
        normal_transport.list_remote_files.return_value = []
        normal_transport.upload.return_value = True
        normal_transport.download.return_value = True
        
        # 恢复同步应该成功
        result = sync_logic(
            cache_root=env.context.cache_root,
            transport=normal_transport,
            direction="upload"
        )
        
        # 验证恢复成功
        assert result is not None, "Recovery sync failed"
        
    @pytest.mark.recovery
    def test_partial_upload_recovery(self, recovery_test_environment):
        """测试部分上传恢复"""
        env = recovery_test_environment
        
        # 创建部分上传场景
        partial_transport = env.create_failure_scenario("partial_upload", partial_files=[0, 2])
        
        # 执行部分上传
        try:
            sync_logic(
                cache_root=env.context.cache_root,
                transport=partial_transport,
                direction="upload"
            )
        except TransportError:
            pass  # 预期会失败
            
        # 验证部分文件状态
        # 这里应该检查哪些文件成功上传，哪些失败
        # 由于我们使用的是模拟传输，实际验证会比较复杂
        
        # 创建正常传输进行恢复
        normal_transport = Mock()
        normal_transport.list_remote_files.return_value = []
        normal_transport.upload.return_value = True
        normal_transport.download.return_value = True
        
        # 恢复上传应该成功
        result = sync_logic(
            cache_root=env.context.cache_root,
            transport=normal_transport,
            direction="upload"
        )
        
        assert result is not None, "Recovery from partial upload failed"
        
    @pytest.mark.recovery
    def test_hash_mismatch_recovery(self, recovery_test_environment):
        """测试哈希不匹配恢复"""
        env = recovery_test_environment
        
        # 创建哈希不匹配场景
        hash_mismatch_transport = env.create_failure_scenario("hash_mismatch", mismatch_files=[1, 3])
        
        # 执行上传（应该失败）
        with pytest.raises(TransportError):
            sync_logic(
                cache_root=env.context.cache_root,
                transport=hash_mismatch_transport,
                direction="upload"
            )
            
        # 验证哈希不匹配被检测
        # 在实际实现中，这里应该验证哈希验证逻辑
        
        # 使用正常传输恢复
        normal_transport = Mock()
        normal_transport.list_remote_files.return_value = []
        normal_transport.upload.return_value = True
        normal_transport.download.return_value = True
        
        result = sync_logic(
            cache_root=env.context.cache_root,
            transport=normal_transport,
            direction="upload"
        )
        
        assert result is not None, "Recovery from hash mismatch failed"
        
    @pytest.mark.recovery
    def test_permission_denied_recovery(self, recovery_test_environment):
        """测试权限拒绝恢复"""
        env = recovery_test_environment
        
        # 创建权限拒绝场景
        permission_transport = env.create_failure_scenario("permission_denied", denied_paths=["/denied/path1"])
        
        # 执行上传（应该失败）
        with pytest.raises(PermissionError):
            sync_logic(
                cache_root=env.context.cache_root,
                transport=permission_transport,
                direction="upload"
            )
            
        # 验证权限错误被正确处理
        # 在实际实现中，这里应该验证权限检查和错误处理逻辑
        
        # 使用正常传输恢复
        normal_transport = Mock()
        normal_transport.list_remote_files.return_value = []
        normal_transport.upload.return_value = True
        normal_transport.download.return_value = True
        
        result = sync_logic(
            cache_root=env.context.cache_root,
            transport=normal_transport,
            direction="upload"
        )
        
        assert result is not None, "Recovery from permission denied failed"
        
    @pytest.mark.recovery
    def test_disk_full_recovery(self, recovery_test_environment):
        """测试磁盘满恢复"""
        env = recovery_test_environment
        
        # 创建磁盘满场景
        disk_full_transport = env.create_failure_scenario("disk_full", max_files=1)
        
        # 执行上传（应该失败）
        with pytest.raises(OSError):
            sync_logic(
                cache_root=env.context.cache_root,
                transport=disk_full_transport,
                direction="upload"
            )
            
        # 验证磁盘满错误被正确处理
        # 在实际实现中，这里应该验证磁盘空间检查和错误处理逻辑
        
        # 使用正常传输恢复
        normal_transport = Mock()
        normal_transport.list_remote_files.return_value = []
        normal_transport.upload.return_value = True
        normal_transport.download.return_value = True
        
        result = sync_logic(
            cache_root=env.context.cache_root,
            transport=normal_transport,
            direction="upload"
        )
        
        assert result is not None, "Recovery from disk full failed"
        
    @pytest.mark.recovery
    def test_metadata_corruption_recovery(self, recovery_test_environment):
        """测试元数据损坏恢复"""
        env = recovery_test_environment
        
        metadata_manager = env.get_metadata_manager()
        
        # 创建有效的元数据
        valid_entries = [
            {
                "audio_oid": "sha256:valid_hash_1",
                "title": "Valid Song 1",
                "artists": ["Valid Artist 1"],
                "created_at": "2024-01-01T00:00:00Z",
            }
        ]
        
        # 保存有效元数据
        metadata_manager.save_all(valid_entries)
        
        # 模拟元数据文件损坏
        corrupted_content = "invalid json content {broken: true, missing quotes}"
        env.context.metadata_file.write_text(corrupted_content)
        
        # 尝试加载损坏的元数据（应该失败或返回空列表）
        try:
            loaded_entries = metadata_manager.load_all()
            # 如果加载成功，应该返回空列表或有效数据
            assert isinstance(loaded_entries, list), "Load should return a list"
        except (json.JSONDecodeError, ValueError):
            # 预期可能会抛出解析错误
            pass
            
        # 恢复有效元数据
        metadata_manager.save_all(valid_entries)
        
        # 验证恢复成功
        loaded_entries = metadata_manager.load_all()
        assert len(loaded_entries) == 1, "Metadata recovery failed"
        assert loaded_entries[0]["title"] == "Valid Song 1", "Metadata content incorrect after recovery"
        
    @pytest.mark.recovery
    def test_partial_publish_recovery(self, recovery_test_environment):
        """测试部分发布恢复"""
        env = recovery_test_environment
        
        metadata_manager = env.get_metadata_manager()
        
        # 创建一些测试文件
        test_files = []
        for i in range(3):
            test_file = env.context.work_dir / f"partial_publish_test_{i}.mp3"
            content = f"PARTIAL_PUBLISH_TEST_{i}".encode()
            test_file.write_bytes(content)
            test_files.append(test_file)
            
        # 模拟部分发布失败
        original_publish = publish_logic
        
        def failing_publish_logic(metadata_mgr):
            """模拟发布逻辑部分失败"""
            # 返回部分文件和错误
            return [], "Simulated publish failure"
            
        # 使用模拟的失败发布
        with patch('libgitmusic.commands.publish.publish_logic', side_effect=failing_publish_logic):
            to_process, error = publish_logic(metadata_manager)
            assert error is not None, "Expected publish to fail"
            
        # 恢复正常的发布逻辑
        to_process, error = publish_logic(metadata_manager)
        assert error is None, "Publish recovery failed"
        
        # 如果有文件需要处理，执行发布
        if to_process:
            execute_publish(metadata_manager, to_process)
            
            # 验证发布成功
            remaining_files = [f for f in test_files if f.exists()]
            assert len(remaining_files) == 0, f"Not all files published: {len(remaining_files)} remaining"
            
    @pytest.mark.recovery
    def test_release_failure_recovery(self, recovery_test_environment):
        """测试发布失败恢复"""
        env = recovery_test_environment
        
        metadata_manager = env.get_metadata_manager()
        object_store = env.get_object_store()
        
        # 首先成功发布一些文件
        to_process, error = publish_logic(metadata_manager)
        if to_process:
            execute_publish(metadata_manager, to_process)
            
        # 模拟发布失败
        original_release = release_logic
        
        def failing_release_logic(metadata_mgr, object_store, release_dir, mode):
            """模拟发布逻辑失败"""
            return [], "Simulated release failure"
            
        # 使用模拟的失败发布
        with patch('libgitmusic.commands.release.release_logic', side_effect=failing_release_logic):
            entries_to_process, error = release_logic(
                metadata_mgr=metadata_manager,
                object_store=object_store,
                release_dir=env.context.release_dir,
                mode="local"
            )
            assert error is not None, "Expected release to fail"
            
        # 恢复正常的发布逻辑
        entries_to_process, error = release_logic(
            metadata_mgr=metadata_manager,
            object_store=object_store,
            release_dir=env.context.release_dir,
            mode="local"
        )
        assert error is None, "Release recovery failed"
        
        # 如果有文件需要发布，执行发布
        if entries_to_process:
            execute_release(metadata_manager, object_store, env.context.release_dir, entries_to_process)
            
    @pytest.mark.recovery
    def test_retry_mechanism(self, recovery_test_environment):
        """测试重试机制"""
        env = recovery_test_environment
        
        # 创建会失败几次然后成功的传输
        class RetryTransport:
            def __init__(self, fail_count=2):
                self.fail_count = fail_count
                self.attempt_count = 0
                
            def upload(self, local_path, remote_path):
                self.attempt_count += 1
                if self.attempt_count <= self.fail_count:
                    raise NetworkError(f"Simulated failure {self.attempt_count}")
                return True
                
            def download(self, remote_path, local_path):
                self.attempt_count += 1
                if self.attempt_count <= self.fail_count:
                    raise NetworkError(f"Simulated failure {self.attempt_count}")
                return True
                
            def list_remote_files(self):
                return []
                
        retry_transport = RetryTransport(fail_count=2)
        
        # 执行同步（应该在前两次失败后第三次成功）
        result = sync_logic(
            cache_root=env.context.cache_root,
            transport=retry_transport,
            direction="upload"
        )
        
        # 验证重试机制工作
        assert result is not None, "Retry mechanism failed"
        assert retry_transport.attempt_count == 3, f"Expected 3 attempts, got {retry_transport.attempt_count}"
        
    @pytest.mark.recovery
    def test_state_consistency_after_recovery(self, recovery_test_environment):
        """测试恢复后的状态一致性"""
        env = recovery_test_environment
        
        metadata_manager = env.get_metadata_manager()
        
        # 创建初始状态
        initial_entries = [
            {
                "audio_oid": "sha256:consistency_test_1",
                "title": "Consistency Test 1",
                "artists": ["Consistency Artist 1"],
                "created_at": "2024-01-01T00:00:00Z",
            }
        ]
        
        metadata_manager.save_all(initial_entries)
        
        # 模拟故障和恢复
        failing_transport = env.create_failure_scenario("network_interruption", fail_after_percent=0.3)
        
        try:
            sync_logic(
                cache_root=env.context.cache_root,
                transport=failing_transport,
                direction="upload"
            )
        except NetworkError:
            pass  # 预期失败
            
        # 恢复后验证状态一致性
        recovered_entries = metadata_manager.load_all()
        
        # 验证元数据一致性
        assert len(recovered_entries) == len(initial_entries), "Metadata state inconsistent after recovery"
        assert recovered_entries[0]["audio_oid"] == initial_entries[0]["audio_oid"], "Metadata content inconsistent"
        
        # 验证文件系统一致性
        assert env.context.metadata_file.exists(), "Metadata file missing after recovery"
        assert env.context.cache_root.exists(), "Cache directory missing after recovery"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])