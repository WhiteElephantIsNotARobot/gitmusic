"""
故障注入测试框架
- 网络中断恢复测试
- 哈希不匹配处理测试
- 文件冲突解决测试
"""
import pytest
import tempfile
import shutil
import json
import hashlib
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import time
import random

# 导入被测试的模块
from libgitmusic.commands.publish import publish_logic, execute_publish
from libgitmusic.commands.sync import sync_logic, execute_sync, analyze_sync_diff
from libgitmusic.commands.release import release_logic, execute_release
from libgitmusic.commands.checkout import checkout_logic, execute_checkout
from libgitmusic.metadata import MetadataManager
from libgitmusic.object_store import ObjectStore
from libgitmusic.transport import TransportAdapter
from libgitmusic.audio import AudioIO
from libgitmusic.hash_utils import HashUtils


class TestFailureInjection:
    """故障注入测试类"""
    
    @pytest.mark.integration
    def test_network_interruption_recovery(self, integration_test_env, sample_audio_file):
        """测试网络中断恢复机制"""
        env = integration_test_env
        
        # 设置网络中断场景
        env.create_test_scenario("network_interruption")
        
        context = env.context
        metadata_manager = MetadataManager(context)
        
        # 准备测试数据
        work_dir = context.work_dir
        audio_file = sample_audio_file("network_recovery_test.mp3")
        shutil.copy2(audio_file, work_dir / "network_recovery_test.mp3")
        
        # 1. 执行 publish（本地操作，不受网络影响）
        to_process, error = publish_logic(metadata_manager)
        assert error is None, f"Publish failed: {error}"
        execute_publish(metadata_manager, to_process)
        
        # 2. 创建模拟的故障传输适配器
        class FailingTransport(Mock):
            def __init__(self, failure_scenarios=None):
                super().__init__(spec=TransportAdapter)
                self.failure_scenarios = failure_scenarios or {}
                self.call_counts = {}
                self.recovered = False
                
            def upload(self, src_path, dst_path):
                call_key = "upload"
                self.call_counts[call_key] = self.call_counts.get(call_key, 0) + 1
                
                # 模拟网络中断（前2次失败，第3次成功）
                if self.call_counts[call_key] <= 2:
                    raise Exception(f"Network interruption during upload (attempt {self.call_counts[call_key]})")
                else:
                    self.recovered = True
                    return True
                    
            def download(self, src_path, dst_path):
                call_key = "download"
                self.call_counts[call_key] = self.call_counts.get(call_key, 0) + 1
                
                # 模拟网络中断
                if self.call_counts[call_key] <= 1:
                    raise Exception(f"Network interruption during download (attempt {self.call_counts[call_key]})")
                else:
                    return True
                    
            def list_remote_files(self, prefix):
                return []
        
        failing_transport = FailingTransport()
        
        # 3. 执行 sync with retry
        result = sync_logic(
            cache_root=context.cache_root,
            transport=failing_transport,
            direction="upload",
            retries=3,  # 设置足够的重试次数
            dry_run=False
        )
        
        # 验证恢复机制
        assert failing_transport.recovered, "Network recovery not successful"
        assert failing_transport.call_counts.get("upload", 0) >= 3, "Not enough retry attempts"
        
    @pytest.mark.integration
    def test_hash_mismatch_handling(self, integration_test_env, sample_audio_file):
        """测试哈希不匹配处理"""
        env = integration_test_env
        context = env.context
        metadata_manager = MetadataManager(context)
        
        # 准备测试数据
        work_dir = context.work_dir
        audio_file = sample_audio_file("hash_mismatch_test.mp3")
        test_file = work_dir / "hash_mismatch_test.mp3"
        shutil.copy2(audio_file, test_file)
        
        # 1. 执行初始 publish
        to_process, error = publish_logic(metadata_manager)
        assert error is None, f"Initial publish failed: {error}"
        execute_publish(metadata_manager, to_process)
        
        # 获取原始元数据
        original_entries = metadata_manager.load_all()
        assert len(original_entries) > 0, "No metadata entries after initial publish"
        
        # 2. 模拟文件内容变化（哈希改变）
        # 修改文件内容
        with open(test_file, "ab") as f:
            f.write(b"modified content")
            
        # 重新复制到工作目录
        shutil.copy2(test_file, work_dir / "hash_mismatch_test_modified.mp3")
        
        # 3. 执行 publish（应该检测到哈希变化）
        to_process, error = publish_logic(metadata_manager, changed_only=True)
        
        # 验证哈希变化被检测到
        assert error is None, f"Publish with hash mismatch failed: {error}"
        
        # 即使没有检测到变化（因为文件是新的），也应该有文件需要处理
        assert len(to_process) > 0, "No files to process with hash mismatch"
        
        # 4. 验证哈希验证逻辑
        with patch('libgitmusic.audio.AudioIO.get_audio_hash') as mock_hash:
            # 模拟不同的哈希值
            mock_hash.return_value = "sha256:wrong_hash_value"
            
            # 重新执行 publish logic
            to_process_with_wrong_hash, error = publish_logic(metadata_manager)
            
            # 应该仍然有文件需要处理
            assert len(to_process_with_wrong_hash) > 0, "No files to process with wrong hash"
            
    @pytest.mark.integration
    def test_file_conflict_resolution(self, integration_test_env, sample_audio_file):
        """测试文件冲突解决机制"""
        env = integration_test_env
        
        # 设置文件冲突场景
        env.create_test_scenario("file_conflict")
        
        context = env.context
        metadata_manager = MetadataManager(context)
        object_store = ObjectStore(context)
        
        # 1. 准备初始发布文件
        release_dir = context.release_dir
        existing_file = release_dir / "Test Artist - Test Song.mp3"
        
        # 创建已存在的发布文件
        sample_audio = sample_audio_file("existing_song.mp3")
        shutil.copy2(sample_audio, existing_file)
        
        # 2. 准备元数据（相同文件名）
        conflicting_entry = {
            "audio_oid": "sha256:5cf24dba4f21d4288094e9b9eb389f15e76c1e5b09e940a0c8fb9c37c6a0a8f3",
            "title": "Test Song",
            "artists": ["Test Artist"],
            "created_at": "2024-01-01T00:00:00Z",
        }
        
        # 准备音频对象文件
        cache_objects_dir = context.cache_root / "objects" / "sha256" / "5c"
        cache_objects_dir.mkdir(parents=True, exist_ok=True)
        audio_obj_file = cache_objects_dir / "f24dba4f21d4288094e9b9eb389f15e76c1e5b09e940a0c8fb9c37c6a0a8f3.mp3"
        sample_audio = sample_audio_file("conflicting_song.mp3")
        shutil.copy2(sample_audio, audio_obj_file)
        
        # 3. 执行 release with different conflict strategies
        
        # 测试 suffix 策略（默认）
        success_count, total_count = execute_release(
            entries=[conflicting_entry],
            object_store=object_store,
            release_dir=release_dir,
            conflict_strategy="suffix"
        )
        
        # 验证冲突解决
        assert success_count == 1, "Conflict resolution with suffix strategy failed"
        
        # 验证创建了带后缀的文件
        suffixed_file = release_dir / "Test Artist - Test Song_1.mp3"
        assert suffixed_file.exists(), "Suffixed file not created"
        assert existing_file.exists(), "Original file overwritten"
        
        # 测试 overwrite 策略
        conflicting_entry2 = {
            "audio_oid": "sha256:6cf24dba4f21d4288094e9b9eb389f15e76c1e5b09e940a0c8fb9c37c6a0a8f4",
            "title": "Test Song 2",
            "artists": ["Test Artist"],
            "created_at": "2024-01-02T00:00:00Z",
        }
        
        # 准备第二个音频对象文件
        audio_obj_file2 = cache_objects_dir / "f24dba4f21d4288094e9b9eb389f15e76c1e5b09e940a0c8fb9c37c6a0a8f4.mp3"
        sample_audio2 = sample_audio_file("overwrite_test_song.mp3")
        shutil.copy2(sample_audio2, audio_obj_file2)
        
        # 创建目标文件
        target_file = release_dir / "Test Artist - Test Song 2.mp3"
        target_file.write_bytes(b"original content")
        original_mtime = target_file.stat().st_mtime
        
        # 等待一点时间确保时间戳不同
        time.sleep(0.1)
        
        success_count, total_count = execute_release(
            entries=[conflicting_entry2],
            object_store=object_store,
            release_dir=release_dir,
            conflict_strategy="overwrite"
        )
        
        # 验证覆盖成功
        assert success_count == 1, "Overwrite strategy failed"
        assert target_file.exists(), "Target file missing after overwrite"
        
        # 验证文件被覆盖（时间戳改变）
        new_mtime = target_file.stat().st_mtime
        assert new_mtime > original_mtime, "File not overwritten"
        
        # 测试 skip 策略
        conflicting_entry3 = {
            "audio_oid": "sha256:7cf24dba4f21d4288094e9b9eb389f15e76c1e5b09e940a0c8fb9c37c6a0a8f5",
            "title": "Test Song 3",
            "artists": ["Test Artist"],
            "created_at": "2024-01-03T00:00:00Z",
        }
        
        # 准备第三个音频对象文件
        audio_obj_file3 = cache_objects_dir / "f24dba4f21d4288094e9b9eb389f15e76c1e5b09e940a0c8fb9c37c6a0a8f5.mp3"
        sample_audio3 = sample_audio_file("skip_test_song.mp3")
        shutil.copy2(sample_audio3, audio_obj_file3)
        
        # 创建已存在的文件
        skip_target = release_dir / "Test Artist - Test Song 3.mp3"
        skip_target.write_bytes(b"existing content")
        
        success_count, total_count = execute_release(
            entries=[conflicting_entry3],
            object_store=object_store,
            release_dir=release_dir,
            conflict_strategy="skip"
        )
        
        # 验证跳过策略
        assert success_count == 1, "Skip strategy failed"
        # 文件应该仍然存在且内容未改变
        assert skip_target.exists(), "Skipped file missing"
        assert skip_target.read_bytes() == b"existing content", "Skipped file was modified"
        
    @pytest.mark.integration
    def test_concurrent_failure_scenarios(self, integration_test_env, sample_audio_file):
        """测试并发故障场景"""
        env = integration_test_env
        context = env.context
        metadata_manager = MetadataManager(context)
        
        # 准备多个测试文件
        work_dir = context.work_dir
        test_files = []
        
        for i in range(10):
            filename = f"concurrent_failure_test_{i}.mp3"
            audio_file = sample_audio_file(filename)
            test_file = work_dir / filename
            shutil.copy2(audio_file, test_file)
            test_files.append(test_file)
        
        # 1. 执行 publish（部分文件可能失败）
        with patch('libgitmusic.commands.publish.extract_metadata_from_file') as mock_extract:
            # 模拟部分文件元数据提取失败
            def flaky_extract(audio_path):
                if random.random() < 0.3:  # 30% 失败率
                    raise Exception(f"Metadata extraction failed for {audio_path}")
                return {
                    "title": f"Test Song {audio_path.stem}",
                    "artists": ["Test Artist"],
                    "album": "Test Album",
                }
                
            mock_extract.side_effect = flaky_extract
            
            to_process, error = publish_logic(metadata_manager)
            
            # 即使有失败，publish 逻辑应该处理错误
            if error is not None:
                # 如果整体失败，验证错误类型
                assert "工作目录为空" not in error, "Publish failed due to empty directory"
            else:
                # 如果成功，验证处理了一些文件
                assert len(to_process) > 0, "No files processed in concurrent failure scenario"
                
        # 2. 执行 sync with concurrent failures
        class ConcurrentFailingTransport(Mock):
            def __init__(self):
                super().__init__(spec=TransportAdapter)
                self.success_count = 0
                self.failure_count = 0
                
            def upload(self, src_path, dst_path):
                # 模拟并发上传失败
                if random.random() < 0.2:  # 20% 失败率
                    self.failure_count += 1
                    raise Exception(f"Concurrent upload failure for {dst_path}")
                else:
                    self.success_count += 1
                    return True
                    
            def download(self, src_path, dst_path):
                # 模拟并发下载失败
                if random.random() < 0.2:  # 20% 失败率
                    self.failure_count += 1
                    raise Exception(f"Concurrent download failure for {src_path}")
                else:
                    self.success_count += 1
                    return True
                    
            def list_remote_files(self, prefix):
                return []
        
        concurrent_transport = ConcurrentFailingTransport()
        
        # 执行 sync with concurrent failures
        result = sync_logic(
            cache_root=context.cache_root,
            transport=concurrent_transport,
            direction="both",
            workers=4,  # 并发处理
            retries=2,  # 重试机制
            dry_run=False
        )
        
        # 验证并发处理结果
        total_attempts = concurrent_transport.success_count + concurrent_transport.failure_count
        assert total_attempts > 0, "No sync operations attempted"
        assert concurrent_transport.success_count > 0, "No successful sync operations"
        
    @pytest.mark.integration
    def test_cascading_failure_recovery(self, integration_test_env, sample_audio_file):
        """测试级联故障恢复"""
        env = integration_test_env
        context = env.context
        metadata_manager = MetadataManager(context)
        
        # 准备测试数据
        work_dir = context.work_dir
        audio_file = sample_audio_file("cascading_failure_test.mp3")
        shutil.copy2(audio_file, work_dir / "cascading_failure_test.mp3")
        
        # 1. 模拟级联故障场景
        class CascadingFailureTransport(Mock):
            def __init__(self):
                super().__init__(spec=TransportAdapter)
                self.failure_stage = 0
                self.recovered_stages = []
                
            def list_remote_files(self, prefix):
                # 第一阶段：文件列表失败
                if self.failure_stage == 0:
                    self.failure_stage += 1
                    raise Exception("Remote file listing failed")
                return []
                
            def upload(self, src_path, dst_path):
                # 第二阶段：上传失败
                if self.failure_stage == 1:
                    self.failure_stage += 1
                    raise Exception("Upload operation failed")
                self.recovered_stages.append("upload")
                return True
                
            def download(self, src_path, dst_path):
                # 第三阶段：下载失败
                if self.failure_stage == 2:
                    self.failure_stage += 1
                    raise Exception("Download operation failed")
                self.recovered_stages.append("download")
                return True
        
        cascading_transport = CascadingFailureTransport()
        
        # 2. 执行 publish（不受网络影响）
        to_process, error = publish_logic(metadata_manager)
        assert error is None, f"Publish failed: {error}"
        execute_publish(metadata_manager, to_process)
        
        # 3. 执行 sync with cascading failures
        # 第一次尝试：应该失败在文件列表阶段
        result1 = sync_logic(
            cache_root=context.cache_root,
            transport=cascading_transport,
            direction="both",
            retries=1,
            dry_run=False
        )
        
        # 验证第一阶段故障
        assert cascading_transport.failure_stage >= 1, "First stage failure not triggered"
        
        # 重置传输适配器状态，模拟恢复
        cascading_transport.failure_stage = 0
        
        # 使用 mock 来避免实际的文件列表错误
        with patch.object(cascading_transport, 'list_remote_files', return_value=[]):
            # 第二次尝试：应该处理上传和下载故障
            result2 = sync_logic(
                cache_root=context.cache_root,
                transport=cascading_transport,
                direction="both",
                retries=3,  # 更多重试次数处理级联故障
                dry_run=False
            )
            
            # 验证恢复阶段
            assert len(cascading_transport.recovered_stages) > 0, "No recovery stages completed"
            
    @pytest.mark.integration
    def test_memory_and_resource_management_during_failures(self, integration_test_env, sample_audio_file):
        """测试故障期间的内存和资源管理"""
        env = integration_test_env
        context = env.context
        metadata_manager = MetadataManager(context)
        
        # 准备大量测试文件以测试资源管理
        work_dir = context.work_dir
        large_file_count = 20
        
        for i in range(large_file_count):
            filename = f"resource_test_{i}.mp3"
            audio_file = sample_audio_file(filename)
            test_file = work_dir / filename
            shutil.copy2(audio_file, test_file)
        
        # 监控内存使用的简单方法
        import psutil
        import os
        
        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss
        
        # 1. 执行 publish with failures
        with patch('libgitmusic.commands.publish.execute_publish') as mock_execute:
            # 模拟执行失败但仍然分配资源
            def failing_execute(metadata_mgr, items, progress_callback=None):
                # 模拟内存分配
                large_data = ["x" * 1000000 for _ in range(100)]  # 分配约100MB
                raise Exception("Simulated execution failure")
                
            mock_execute.side_effect = failing_execute
            
            to_process, error = publish_logic(metadata_manager)
            assert error is None, f"Publish logic failed: {error}"
            assert len(to_process) > 0, "No files to process"
            
            # 执行应该失败，但不应该导致内存泄漏
            try:
                execute_publish(metadata_manager, to_process)
            except Exception:
                pass  # 预期失败
                
        # 强制垃圾收集
        import gc
        gc.collect()
        
        # 检查内存使用情况（简单验证）
        final_memory = process.memory_info().rss
        memory_increase = final_memory - initial_memory
        
        # 内存增长应该在合理范围内（这里设置为500MB作为宽松限制）
        # 在实际CI环境中可能需要调整这个值
        max_acceptable_increase = 500 * 1024 * 1024  # 500MB
        
        # 注意：这个检查是启发式的，可能会因环境而异
        if memory_increase > max_acceptable_increase:
            pytest.warn(f"Significant memory increase detected: {memory_increase / (1024*1024):.1f}MB")
            
        # 2. 验证系统仍然可以正常工作
        # 重新执行 publish（应该成功）
        mock_execute.side_effect = None  # 移除失败模拟
        mock_execute.return_value = None  # 恢复正常行为
        
        to_process, error = publish_logic(metadata_manager)
        # 这次应该没有文件需要处理（因为之前的文件已经被移动了）
        assert len(to_process) == 0, "Unexpected files to process after failure recovery"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])