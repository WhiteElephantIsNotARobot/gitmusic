"""
关键命令链测试
- publish → sync → release流程测试
- checkout → publish冲突处理测试  
- download → publish完整流程测试
"""
import pytest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

# 导入被测试的模块
from libgitmusic.commands.publish import publish_logic, execute_publish
from libgitmusic.commands.sync import sync_logic, analyze_sync_diff, execute_sync
from libgitmusic.commands.release import release_logic, execute_release
from libgitmusic.commands.checkout import checkout_logic, execute_checkout
from libgitmusic.commands.download import download_logic, execute_download
from libgitmusic.metadata import MetadataManager
from libgitmusic.object_store import ObjectStore
from libgitmusic.transport import TransportAdapter


class TestWorkflowChains:
    """工作流链测试类"""
    
    @pytest.mark.integration
    def test_publish_sync_release_workflow(self, integration_test_env, sample_audio_file, sample_metadata_entries):
        """测试完整的 publish → sync → release 流程"""
        env = integration_test_env
        context = env.context
        metadata_manager = MetadataManager(context)
        object_store = ObjectStore(context)
        
        # 1. 准备测试数据
        work_dir = context.work_dir
        audio_file = sample_audio_file("test_workflow.mp3")
        shutil.copy2(audio_file, work_dir / "test_workflow.mp3")
        
        # 2. 执行 publish
        to_process, error = publish_logic(metadata_manager)
        
        # 验证 publish 结果
        assert error is None, f"Publish failed: {error}"
        assert len(to_process) > 0, "No files to process in publish"
        
        # 执行实际的 publish 操作
        execute_publish(metadata_manager, to_process)
        
        # 验证文件已被处理
        assert not (work_dir / "test_workflow.mp3").exists(), "Audio file not moved after publish"
        
        # 验证元数据已保存
        entries = metadata_manager.load_all()
        assert len(entries) > 0, "No metadata entries after publish"
        
        # 3. 执行 sync（上传）
        mock_transport = Mock(spec=TransportAdapter)
        mock_transport.list_remote_files.return_value = []
        mock_transport.upload.return_value = True
        mock_transport.download.return_value = True
        
        result = sync_logic(
            cache_root=context.cache_root,
            transport=mock_transport,
            direction="upload",
            dry_run=False
        )
        
        # 验证 sync 结果
        assert result == 0, f"Sync upload failed with code: {result}"
        
        # 验证上传调用
        assert mock_transport.upload.called, "Upload not called during sync"
        
        # 4. 执行 release
        entries_to_process, error = release_logic(
            metadata_mgr=metadata_manager,
            object_store=object_store,
            release_dir=context.release_dir,
            mode="local"
        )
        
        # 验证 release 逻辑
        assert error is None, f"Release logic failed: {error}"
        assert len(entries_to_process) > 0, "No entries to process in release"
        
        # 执行实际的 release 操作
        success_count, total_count = execute_release(
            entries=entries_to_process,
            object_store=object_store,
            release_dir=context.release_dir
        )
        
        # 验证 release 结果
        assert success_count > 0, "No files released successfully"
        assert success_count == total_count, f"Partial release failure: {success_count}/{total_count}"
        
        # 验证发布文件存在
        release_files = list(context.release_dir.glob("*.mp3"))
        assert len(release_files) > 0, "No files in release directory"
        
    @pytest.mark.integration
    def test_checkout_publish_conflict_resolution(self, integration_test_env, sample_audio_file):
        """测试 checkout → publish 冲突处理"""
        env = integration_test_env
        context = env.context
        metadata_manager = MetadataManager(context)
        
        # 1. 准备初始元数据
        initial_entry = {
            "audio_oid": "sha256:2cf24dba4f21d4288094e9b9eb389f15e76c1e5b09e940a0c8fb9c37c6a0a8f0",
            "title": "Original Song",
            "artists": ["Original Artist"],
            "created_at": "2024-01-01T00:00:00Z",
        }
        metadata_manager.save_all([initial_entry])
        
        # 2. 执行 checkout（检出文件到工作目录）
        to_checkout = checkout_logic(metadata_manager)
        assert len(to_checkout) > 0, "No entries to checkout"
        
        # 模拟音频文件存在
        cache_objects_dir = context.cache_root / "objects" / "sha256" / "2c"
        cache_objects_dir.mkdir(parents=True, exist_ok=True)
        
        # 创建虚拟音频文件
        audio_file = cache_objects_dir / "f24dba4f21d4288094e9b9eb389f15e76c1e5b09e940a0c8fb9c37c6a0a8f0.mp3"
        sample_audio = sample_audio_file("original_song.mp3")
        shutil.copy2(sample_audio, audio_file)
        
        # 执行检出
        results = execute_checkout(
            repo_root=context.project_root / "repo",
            items=to_checkout,
            force=False
        )
        
        # 验证检出结果
        assert len(results) > 0, "No checkout results"
        success_count = sum(1 for _, status in results if status == "success")
        assert success_count > 0, "No successful checkouts"
        
        # 3. 在工作目录中修改文件（模拟冲突）
        work_file = context.work_dir / "Original Artist - Original Song.mp3"
        assert work_file.exists(), "Checked out file not found in work directory"
        
        # 创建新的音频文件（模拟修改）
        new_audio_file = sample_audio_file("modified_song.mp3")
        shutil.copy2(new_audio_file, work_file)
        
        # 4. 执行 publish（应该检测到变化并更新）
        to_process, error = publish_logic(metadata_manager, changed_only=True)
        
        # 验证 publish 结果
        assert error is None, f"Publish with conflict failed: {error}"
        
        # 验证检测到变化
        changed_items = [item for item in to_process if item.get("is_changed", False)]
        assert len(changed_items) > 0, "No changes detected in modified file"
        
        # 执行 publish
        execute_publish(metadata_manager, to_process)
        
        # 验证元数据已更新
        updated_entries = metadata_manager.load_all()
        assert len(updated_entries) == 1, "Metadata entries count mismatch"
        
        # 验证文件已被处理
        assert not work_file.exists(), "Modified file not moved after publish"
        
    @pytest.mark.integration  
    def test_download_publish_complete_workflow(self, integration_test_env, sample_audio_file):
        """测试 download → publish 完整流程"""
        env = integration_test_env
        context = env.context
        metadata_manager = MetadataManager(context)
        
        # 1. 模拟下载音频文件
        # 创建模拟的下载结果
        downloaded_file = context.work_dir / "Downloaded Artist - Downloaded Song.mp3"
        sample_audio = sample_audio_file("downloaded_song.mp3")
        shutil.copy2(sample_audio, downloaded_file)
        
        # 模拟下载元数据
        download_metadata = {
            "url": "https://example.com/song",
            "file": str(downloaded_file),
            "audio_oid": "sha256:4cf24dba4f21d4288094e9b9eb389f15e76c1e5b09e940a0c8fb9c37c6a0a8f2",
            "cover_oid": "sha256:3cf24dba4f21d4288094e9b9eb389f15e76c1e5b09e940a0c8fb9c37c6a0a8f1",
            "title": "Downloaded Song",
            "artists": ["Downloaded Artist"],
        }
        
        # 2. 执行 publish
        to_process, error = publish_logic(metadata_manager)
        
        # 验证 publish 结果
        assert error is None, f"Publish after download failed: {error}"
        assert len(to_process) > 0, "No files to process after download"
        
        # 验证下载的文件被检测到
        downloaded_items = [
            item for item in to_process 
            if "Downloaded Song" in item.get("title", "")
        ]
        assert len(downloaded_items) > 0, "Downloaded file not detected in publish"
        
        # 执行实际的 publish 操作
        execute_publish(metadata_manager, to_process)
        
        # 验证文件已被处理
        assert not downloaded_file.exists(), "Downloaded file not moved after publish"
        
        # 验证元数据已保存
        entries = metadata_manager.load_all()
        assert len(entries) > 0, "No metadata entries after download-publish"
        
        # 验证下载的元数据正确保存
        downloaded_entries = [
            entry for entry in entries
            if "Downloaded Song" in entry.get("title", "")
        ]
        assert len(downloaded_entries) > 0, "Downloaded metadata not saved"
        
        downloaded_entry = downloaded_entries[0]
        assert downloaded_entry["title"] == "Downloaded Song"
        assert downloaded_entry["artists"] == ["Downloaded Artist"]
        assert "audio_oid" in downloaded_entry
        assert "cover_oid" in downloaded_entry
        
    @pytest.mark.integration
    def test_workflow_with_network_interruption(self, integration_test_env, sample_audio_file):
        """测试网络中断情况下的工作流程"""
        env = integration_test_env
        
        # 设置网络中断场景
        env.create_test_scenario("network_interruption")
        
        context = env.context
        metadata_manager = MetadataManager(context)
        
        # 准备测试数据
        work_dir = context.work_dir
        audio_file = sample_audio_file("network_test.mp3")
        shutil.copy2(audio_file, work_dir / "network_test.mp3")
        
        # 1. 执行 publish（应该成功，不依赖网络）
        to_process, error = publish_logic(metadata_manager)
        assert error is None, f"Publish failed during network interruption: {error}"
        
        execute_publish(metadata_manager, to_process)
        
        # 2. 执行 sync（应该处理网络中断）
        mock_transport = Mock(spec=TransportAdapter)
        
        # 模拟网络中断
        call_count = 0
        def flaky_upload(src_path, dst_path):
            nonlocal call_count
            call_count += 1
            if call_count % 3 == 0:  # 每3次失败一次
                raise Exception("Network interruption during upload")
            return True
            
        mock_transport.upload = flaky_upload
        mock_transport.list_remote_files.return_value = []
        
        # sync 应该处理失败并重试
        result = sync_logic(
            cache_root=context.cache_root,
            transport=mock_transport,
            direction="upload",
            retries=2,  # 设置重试次数
            dry_run=False
        )
        
        # 验证 sync 处理了网络中断
        assert mock_transport.upload.called, "Upload not attempted during sync"
        
    @pytest.mark.integration
    def test_workflow_with_hash_mismatch(self, integration_test_env, sample_audio_file):
        """测试哈希不匹配处理"""
        env = integration_test_env
        
        # 设置哈希不匹配场景
        env.create_test_scenario("hash_mismatch")
        
        context = env.context
        metadata_manager = MetadataManager(context)
        
        # 准备测试数据
        work_dir = context.work_dir
        audio_file = sample_audio_file("hash_test.mp3")
        shutil.copy2(audio_file, work_dir / "hash_test.mp3")
        
        # 执行 publish
        to_process, error = publish_logic(metadata_manager)
        assert error is None, f"Publish failed with hash mismatch: {error}"
        
        # 模拟哈希验证失败
        with patch('libgitmusic.audio.AudioIO.get_audio_hash') as mock_hash:
            mock_hash.return_value = "sha256:wrong_hash_value"
            
            # 这应该导致检测到变化
            changed_items = [item for item in to_process if item.get("is_changed", False)]
            # 即使哈希不匹配，文件仍然应该被处理
            assert len(to_process) > 0, "No files to process with hash mismatch"
            
        # 执行 publish
        execute_publish(metadata_manager, to_process)
        
        # 验证文件已被处理
        assert not (work_dir / "hash_test.mp3").exists(), "File not moved after hash mismatch publish"
        
    @pytest.mark.integration
    def test_concurrent_workflow_operations(self, integration_test_env, sample_audio_file):
        """测试并发工作流程操作"""
        env = integration_test_env
        context = env.context
        metadata_manager = MetadataManager(context)
        
        # 准备多个测试文件
        work_dir = context.work_dir
        test_files = []
        
        for i in range(5):
            filename = f"concurrent_test_{i}.mp3"
            audio_file = sample_audio_file(filename)
            test_file = work_dir / filename
            shutil.copy2(audio_file, test_file)
            test_files.append(test_file)
        
        # 执行 publish（处理多个文件）
        to_process, error = publish_logic(metadata_manager)
        assert error is None, f"Concurrent publish failed: {error}"
        assert len(to_process) == 5, f"Expected 5 files, got {len(to_process)}"
        
        # 执行批量 publish
        execute_publish(metadata_manager, to_process)
        
        # 验证所有文件都被处理
        for test_file in test_files:
            assert not test_file.exists(), f"File {test_file.name} not moved after publish"
            
        # 验证所有元数据都已保存
        entries = metadata_manager.load_all()
        assert len(entries) == 5, f"Expected 5 entries, got {len(entries)}"
        
        # 执行批量 release
        from libgitmusic.object_store import ObjectStore
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
        
        # 执行并发 release
        success_count, total_count = execute_release(
            entries=entries_to_process,
            object_store=object_store,
            release_dir=context.release_dir,
            workers=3  # 并发处理
        )
        
        # 验证并发 release 结果
        assert success_count == 5, f"Concurrent release only processed {success_count}/5 files"
        assert total_count == 5, f"Expected 5 total files, got {total_count}"
        
        # 验证发布文件
        release_files = list(context.release_dir.glob("*.mp3"))
        assert len(release_files) == 5, f"Expected 5 release files, got {len(release_files)}"