"""
使用模拟音频文件的集成测试
用于验证任务4.1的测试框架功能
"""
import pytest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

# 导入被测试的模块
from libgitmusic.metadata import MetadataManager
from libgitmusic.object_store import ObjectStore
from libgitmusic.transport import TransportAdapter


class TestMockIntegration:
    """使用模拟的集成测试类"""
    
    @pytest.fixture
    def mock_audio_file(self):
        """创建模拟音频文件"""
        def _create_mock_audio(filename="test_song.mp3"):
            # 创建模拟MP3文件内容（包含基本的MPEG帧头）
            # 这是最小的有效MP3数据
            mp3_header = bytes([
                0xFF, 0xFB, 0x90, 0x00,  # MPEG Layer III, 128kbps, 44.1kHz, Stereo
                0x00, 0x00, 0x00, 0x00,  # 一些空数据
                0x00, 0x00, 0x00, 0x00,
                0x00, 0x00, 0x00, 0x00,
                0x00, 0x00, 0x00, 0x00,
            ])
            return mp3_header
        return _create_mock_audio
    
    @pytest.fixture
    def integration_test_env_mock(self, test_context, mock_transport):
        """集成测试环境（带模拟）"""
        class IntegrationTestEnvironment:
            def __init__(self, context, transport):
                self.context = context
                self.transport = transport
                self.original_functions = {}
                
            def setup(self):
                """设置测试环境"""
                # 模拟音频处理函数
                self._mock_audio_functions()
                
            def teardown(self):
                """清理测试环境"""
                # 恢复原始函数
                self._restore_original_functions()
                
            def _mock_audio_functions(self):
                """模拟音频相关函数"""
                # 模拟音频哈希计算
                self.audio_hash_patcher = patch('libgitmusic.audio.AudioIO.get_audio_hash')
                self.mock_audio_hash = self.audio_hash_patcher.start()
                self.mock_audio_hash.return_value = "sha256:2cf24dba4f21d4288094e9b9eb389f15e76c1e5b09e940a0c8fb9c37c6a0a8f0"
                
                # 模拟元数据提取
                self.metadata_patcher = patch('libgitmusic.commands.publish.extract_metadata_from_file')
                self.mock_metadata = self.metadata_patcher.start()
                self.mock_metadata.return_value = {
                    "title": "Test Song",
                    "artists": ["Test Artist"],
                    "album": "Test Album",
                    "date": "2024-01-01",
                }
                
                # 模拟文件移动
                self.move_patcher = patch('shutil.move')
                self.mock_move = self.move_patcher.start()
                self.mock_move.return_value = None
                
            def _restore_original_functions(self):
                """恢复原始函数"""
                self.audio_hash_patcher.stop()
                self.metadata_patcher.stop()
                self.move_patcher.stop()
                
            def create_test_scenario(self, scenario_name, **kwargs):
                """创建测试场景"""
                scenarios = {
                    "network_interruption": {
                        "network_conditions": {
                            "intermittent_failure": True,
                            "packet_loss": 0.1,
                        },
                    },
                    "hash_mismatch": {
                        "hash_scenarios": {
                            "wrong_hash": "sha256:wrong_hash_value",
                        },
                    },
                    "file_conflict": {
                        "setup_files": [
                            {"path": "work/existing_song.mp3", "content": b"existing_file"},
                        ],
                    },
                }
                
                scenario = scenarios.get(scenario_name, {})
                
                # 应用哈希场景
                if "hash_scenarios" in scenario:
                    for name, hash_value in scenario["hash_scenarios"].items():
                        if name == "wrong_hash":
                            self.mock_audio_hash.return_value = hash_value
        
        env = IntegrationTestEnvironment(test_context, mock_transport)
        env.setup()
        
        yield env
        
        env.teardown()
    
    def test_basic_workflow_mock(self, integration_test_env_mock, mock_audio_file):
        """测试基本工作流程（使用模拟）"""
        env = integration_test_env_mock
        context = env.context
        metadata_manager = MetadataManager(context)
        
        # 1. 创建模拟音频文件
        work_dir = context.work_dir
        audio_content = mock_audio_file("test_workflow.mp3")
        test_file = work_dir / "test_workflow.mp3"
        test_file.write_bytes(audio_content)
        
        # 2. 执行发布逻辑
        from libgitmusic.commands.publish import publish_logic
        to_process, error = publish_logic(metadata_manager)
        
        # 验证发布结果
        assert error is None, f"Publish failed: {error}"
        assert len(to_process) > 0, "No files to process in publish"
        
        # 3. 执行实际的发布操作
        from libgitmusic.commands.publish import execute_publish
        execute_publish(metadata_manager, to_process)
        
        # 验证文件已被处理
        # 注意：由于我们模拟了shutil.move，实际文件不会被移动
        
        # 验证元数据已保存
        entries = metadata_manager.load_all()
        assert len(entries) > 0, "No metadata entries after publish"
        
        # 验证元数据内容
        entry = entries[0]
        assert entry["title"] == "Test Song"
        assert entry["artists"] == ["Test Artist"]
        assert entry["album"] == "Test Album"
        assert "audio_oid" in entry
        
    def test_sync_workflow_mock(self, integration_test_env_mock, mock_audio_file):
        """测试同步工作流程（使用模拟）"""
        env = integration_test_env_mock
        context = env.context
        metadata_manager = MetadataManager(context)
        
        # 1. 先发布一些文件
        work_dir = context.work_dir
        audio_content = mock_audio_file("test_sync.mp3")
        test_file = work_dir / "test_sync.mp3"
        test_file.write_bytes(audio_content)
        
        from libgitmusic.commands.publish import publish_logic, execute_publish
        to_process, error = publish_logic(metadata_manager)
        assert error is None, f"Publish failed: {error}"
        execute_publish(metadata_manager, to_process)
        
        # 2. 执行同步
        from libgitmusic.commands.sync import sync_logic
        result = sync_logic(
            cache_root=context.cache_root,
            transport=env.transport,
            direction="upload",
            dry_run=False
        )
        
        # 验证同步结果
        assert result == 0, f"Sync failed with code: {result}"
        assert env.transport.upload.called, "Upload not called during sync"
        
    def test_release_workflow_mock(self, integration_test_env_mock, mock_audio_file):
        """测试发布工作流程（使用模拟）"""
        env = integration_test_env_mock
        context = env.context
        metadata_manager = MetadataManager(context)
        object_store = ObjectStore(context)
        
        # 1. 先发布一些文件
        work_dir = context.work_dir
        audio_content = mock_audio_file("test_release.mp3")
        test_file = work_dir / "test_release.mp3"
        test_file.write_bytes(audio_content)
        
        from libgitmusic.commands.publish import publish_logic, execute_publish
        to_process, error = publish_logic(metadata_manager)
        assert error is None, f"Publish failed: {error}"
        execute_publish(metadata_manager, to_process)
        
        # 2. 执行发布逻辑
        from libgitmusic.commands.release import release_logic, execute_release
        entries_to_process, error = release_logic(
            metadata_mgr=metadata_manager,
            object_store=object_store,
            release_dir=context.release_dir,
            mode="local"
        )
        
        # 验证发布逻辑结果
        assert error is None, f"Release logic failed: {error}"
        assert len(entries_to_process) > 0, "No entries to process in release"
        
        # 3. 执行实际的发布操作
        # 模拟文件复制
        with patch('shutil.copy2') as mock_copy:
            mock_copy.return_value = None
            
            success_count, total_count = execute_release(
                entries=entries_to_process,
                object_store=object_store,
                release_dir=context.release_dir
            )
            
            # 验证发布结果
            assert success_count > 0, "No files released successfully"
            assert success_count == total_count, f"Partial release failure: {success_count}/{total_count}"
            assert mock_copy.called, "File copy not called during release"
            
    def test_network_interruption_mock(self, integration_test_env_mock, mock_audio_file):
        """测试网络中断处理（使用模拟）"""
        env = integration_test_env_mock
        context = env.context
        metadata_manager = MetadataManager(context)
        
        # 设置网络中断场景
        env.create_test_scenario("network_interruption")
        
        # 配置传输适配器模拟网络问题
        call_count = 0
        def flaky_upload(src_path, dst_path):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:  # 前2次失败
                raise Exception(f"Network interruption during upload (attempt {call_count})")
            return True
            
        env.transport.upload = flaky_upload
        
        # 创建测试文件
        work_dir = context.work_dir
        audio_content = mock_audio_file("network_test.mp3")
        test_file = work_dir / "network_test.mp3"
        test_file.write_bytes(audio_content)
        
        # 执行发布
        from libgitmusic.commands.publish import publish_logic, execute_publish
        to_process, error = publish_logic(metadata_manager)
        assert error is None, f"Publish failed: {error}"
        execute_publish(metadata_manager, to_process)
        
        # 执行同步（应该处理网络中断）
        from libgitmusic.commands.sync import sync_logic
        result = sync_logic(
            cache_root=context.cache_root,
            transport=env.transport,
            direction="upload",
            retries=3,  # 设置重试次数
            dry_run=False
        )
        
        # 验证网络恢复
        assert call_count >= 3, "Not enough retry attempts"
        assert env.transport.upload.call_count >= 3, "Upload not retried properly"
        
    def test_hash_mismatch_mock(self, integration_test_env_mock, mock_audio_file):
        """测试哈希不匹配处理（使用模拟）"""
        env = integration_test_env_mock
        context = env.context
        metadata_manager = MetadataManager(context)
        
        # 设置哈希不匹配场景
        env.create_test_scenario("hash_mismatch")
        
        # 创建测试文件
        work_dir = context.work_dir
        audio_content = mock_audio_file("hash_test.mp3")
        test_file = work_dir / "hash_test.mp3"
        test_file.write_bytes(audio_content)
        
        # 执行发布（应该检测到哈希变化）
        from libgitmusic.commands.publish import publish_logic
        to_process, error = publish_logic(metadata_manager)
        
        # 验证发布结果
        assert error is None, f"Publish with hash mismatch failed: {error}"
        assert len(to_process) > 0, "No files to process with hash mismatch"
        
        # 验证使用了错误的哈希值
        from libgitmusic.commands.publish import execute_publish
        execute_publish(metadata_manager, to_process)
        
        # 验证元数据已保存
        entries = metadata_manager.load_all()
        assert len(entries) > 0, "No metadata entries after hash mismatch publish"
        
        # 验证使用了错误的哈希值
        entry = entries[0]
        assert entry["audio_oid"] == "sha256:wrong_hash_value"
        
    def test_concurrent_operations_mock(self, integration_test_env_mock, mock_audio_file):
        """测试并发操作（使用模拟）"""
        env = integration_test_env_mock
        context = env.context
        metadata_manager = MetadataManager(context)
        
        # 创建多个测试文件
        work_dir = context.work_dir
        test_files = []
        
        for i in range(5):
            filename = f"concurrent_test_{i}.mp3"
            audio_content = mock_audio_file(filename)
            test_file = work_dir / filename
            test_file.write_bytes(audio_content)
            test_files.append(test_file)
        
        # 执行批量发布
        from libgitmusic.commands.publish import publish_logic, execute_publish
        to_process, error = publish_logic(metadata_manager)
        assert error is None, f"Concurrent publish failed: {error}"
        assert len(to_process) == 5, f"Expected 5 files, got {len(to_process)}"
        
        execute_publish(metadata_manager, to_process)
        
        # 验证所有元数据都已保存
        entries = metadata_manager.load_all()
        assert len(entries) == 5, f"Expected 5 entries, got {len(entries)}"
        
        # 执行批量发布
        from libgitmusic.commands.release import release_logic, execute_release
        object_store = ObjectStore(context)
        
        entries_to_process, error = release_logic(
            metadata_mgr=metadata_manager,
            object_store=object_store,
            release_dir=context.release_dir,
            mode="local",
            workers=3  # 并发处理
        )
        
        assert error is None, f"Concurrent release failed: {error}"
        assert len(entries_to_process) == 5, f"Expected 5 entries to release, got {len(entries_to_process)}"
        
        # 执行并发发布
        with patch('shutil.copy2') as mock_copy:
            mock_copy.return_value = None
            
            success_count, total_count = execute_release(
                entries=entries_to_process,
                object_store=object_store,
                release_dir=context.release_dir,
                workers=3  # 并发处理
            )
            
            # 验证并发发布结果
            assert success_count == 5, f"Concurrent release only processed {success_count}/5 files"
            assert total_count == 5, f"Expected 5 total files, got {total_count}"
            assert mock_copy.call_count == 5, f"Expected 5 file copies, got {mock_copy.call_count}"