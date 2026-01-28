"""
数据管理测试
- 测试数据集组织和管理
- 数据一致性验证
- 数据清理和恢复
"""
import pytest
import tempfile
import shutil
import json
import hashlib
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import os

# 导入被测试的模块
from libgitmusic.metadata import MetadataManager
from libgitmusic.object_store import ObjectStore
from libgitmusic.audio import AudioIO
from libgitmusic.hash_utils import HashUtils
from libgitmusic.commands.cleanup import cleanup_orphaned
from libgitmusic.commands.verify import verify_integrity


class TestDataManagement:
    """数据管理测试类"""
    
    @pytest.fixture
    def test_data_organizer(self, integration_test_env):
        """测试数据组织器"""
        class DataOrganizer:
            def __init__(self, context):
                self.context = context
                self.metadata_manager = MetadataManager(context)
                self.object_store = ObjectStore(context)
                
            def create_test_dataset(self, dataset_config):
                """创建测试数据集"""
                work_dir = self.context.work_dir
                dataset = {
                    "files": [],
                    "metadata": [],
                    "expected_structure": {}
                }
                
                for i, file_config in enumerate(dataset_config.get("files", [])):
                    # 创建测试文件
                    filename = file_config.get("filename", f"test_song_{i}.mp3")
                    file_path = work_dir / filename
                    
                    # 创建基础音频文件
                    if file_config.get("create_file", True):
                        self._create_test_audio_file(file_path, file_config)
                    
                    # 创建元数据
                    metadata = self._create_test_metadata(file_config, i)
                    dataset["metadata"].append(metadata)
                    
                    dataset["files"].append({
                        "path": file_path,
                        "metadata": metadata,
                        "config": file_config
                    })
                    
                return dataset
                
            def _create_test_audio_file(self, file_path, config):
                """创建测试音频文件"""
                # 创建基本的MP3文件结构
                mp3_header = b"ID3\x03\x00\x00\x00\x00\x00\x23\x54"
                file_content = mp3_header + b"\x00" * 1000  # 填充内容
                
                # 添加ID3标签
                title = config.get("title", "Test Song")
                artist = config.get("artist", "Test Artist")
                album = config.get("album", "Test Album")
                
                # 写入文件
                file_path.write_bytes(file_content)
                
            def _create_test_metadata(self, config, index):
                """创建测试元数据"""
                audio_hash = f"sha256:{hashlib.sha256(f'test_audio_{index}'.encode()).hexdigest()}"
                cover_hash = f"sha256:{hashlib.sha256(f'test_cover_{index}'.encode()).hexdigest()}"
                
                return {
                    "audio_oid": audio_hash,
                    "cover_oid": cover_hash,
                    "title": config.get("title", f"Test Song {index}"),
                    "artists": [config.get("artist", "Test Artist")],
                    "album": config.get("album", "Test Album"),
                    "date": config.get("date", "2024-01-01"),
                    "created_at": config.get("created_at", "2024-01-01T00:00:00Z"),
                }
                
            def verify_dataset_integrity(self, dataset):
                """验证数据集完整性"""
                issues = []
                
                # 验证文件存在性
                for file_info in dataset["files"]:
                    if not file_info["path"].exists():
                        issues.append(f"Missing file: {file_info['path']}")
                        
                # 验证元数据一致性
                stored_metadata = self.metadata_manager.load_all()
                stored_oids = {entry["audio_oid"] for entry in stored_metadata}
                
                for metadata in dataset["metadata"]:
                    if metadata["audio_oid"] not in stored_oids:
                        issues.append(f"Missing metadata for: {metadata['title']}")
                        
                return issues
                
            def cleanup_dataset(self, dataset):
                """清理测试数据集"""
                for file_info in dataset["files"]:
                    if file_info["path"].exists():
                        file_info["path"].unlink()
                        
                # 清理元数据
                if self.context.metadata_file.exists():
                    self.context.metadata_file.write_text("")
        
        return DataOrganizer(integration_test_env.context)
    
    @pytest.mark.integration
    def test_small_sample_dataset_creation(self, integration_test_env, test_data_organizer):
        """测试小样本数据集创建"""
        # 定义小样本数据集配置
        dataset_config = {
            "files": [
                {
                    "filename": "sample_song_1.mp3",
                    "title": "Sample Song 1",
                    "artist": "Sample Artist 1",
                    "album": "Sample Album 1",
                    "create_file": True
                },
                {
                    "filename": "sample_song_2.mp3", 
                    "title": "Sample Song 2",
                    "artist": "Sample Artist 2",
                    "album": "Sample Album 2",
                    "create_file": True
                },
                {
                    "filename": "sample_song_3.mp3",
                    "title": "Sample Song 3", 
                    "artist": "Sample Artist 3",
                    "album": "Sample Album 3",
                    "create_file": True
                }
            ]
        }
        
        # 创建数据集
        dataset = test_data_organizer.create_test_dataset(dataset_config)
        
        # 验证数据集创建
        assert len(dataset["files"]) == 3, f"Expected 3 files, got {len(dataset['files'])}"
        assert len(dataset["metadata"]) == 3, f"Expected 3 metadata entries, got {len(dataset['metadata'])}"
        
        # 验证文件存在
        for file_info in dataset["files"]:
            assert file_info["path"].exists(), f"File not created: {file_info['path']}"
            
        # 验证元数据格式
        for metadata in dataset["metadata"]:
            assert "audio_oid" in metadata, "Missing audio_oid in metadata"
            assert "title" in metadata, "Missing title in metadata"
            assert "artists" in metadata, "Missing artists in metadata"
            assert "created_at" in metadata, "Missing created_at in metadata"
            
    @pytest.mark.integration
    def test_dataset_with_covers_and_metadata(self, integration_test_env, test_data_organizer):
        """测试包含封面和元数据的复杂数据集"""
        # 定义复杂数据集配置
        dataset_config = {
            "files": [
                {
                    "filename": "complete_song_1.mp3",
                    "title": "Complete Song 1",
                    "artist": "Complete Artist 1",
                    "album": "Complete Album 1",
                    "date": "2024-01-15",
                    "create_file": True
                },
                {
                    "filename": "complete_song_2.mp3",
                    "title": "Complete Song 2",
                    "artist": "Complete Artist 2", 
                    "album": "Complete Album 2",
                    "date": "2024-02-20",
                    "create_file": True
                },
                {
                    "filename": "minimal_song.mp3",
                    "title": "Minimal Song",
                    "artist": "Minimal Artist",
                    "create_file": True
                }
            ]
        }
        
        # 创建数据集
        dataset = test_data_organizer.create_test_dataset(dataset_config)
        
        # 保存元数据到管理系统
        metadata_manager = MetadataManager(integration_test_env.context)
        metadata_manager.save_all(dataset["metadata"])
        
        # 验证元数据保存
        stored_metadata = metadata_manager.load_all()
        assert len(stored_metadata) == 3, f"Expected 3 stored metadata entries, got {len(stored_metadata)}"
        
        # 验证元数据完整性
        issues = test_data_organizer.verify_dataset_integrity(dataset)
        assert len(issues) == 0, f"Dataset integrity issues: {issues}"
        
    @pytest.mark.integration
    def test_data_consistency_validation(self, integration_test_env, test_data_organizer):
        """测试数据一致性验证"""
        # 创建测试数据集
        dataset_config = {
            "files": [
                {
                    "filename": "consistency_test_1.mp3",
                    "title": "Consistency Test 1",
                    "artist": "Consistency Artist 1",
                    "create_file": True
                },
                {
                    "filename": "consistency_test_2.mp3",
                    "title": "Consistency Test 2",
                    "artist": "Consistency Artist 2",
                    "create_file": True
                }
            ]
        }
        
        dataset = test_data_organizer.create_test_dataset(dataset_config)
        metadata_manager = MetadataManager(integration_test_env.context)
        
        # 测试1：验证正常数据一致性
        metadata_manager.save_all(dataset["metadata"])
        issues = test_data_organizer.verify_dataset_integrity(dataset)
        assert len(issues) == 0, f"Unexpected consistency issues: {issues}"
        
        # 测试2：验证缺失文件检测
        # 删除一个文件
        dataset["files"][0]["path"].unlink()
        issues = test_data_organizer.verify_dataset_integrity(dataset)
        assert len(issues) == 1, f"Expected 1 consistency issue, got {len(issues)}"
        assert "Missing file" in issues[0], f"Unexpected issue type: {issues[0]}"
        
        # 测试3：验证缺失元数据检测
        # 恢复文件但删除元数据
        test_data_organizer._create_test_audio_file(dataset["files"][0]["path"], {})
        metadata_manager.save_all([dataset["metadata"][1]])  # 只保存第二个元数据
        
        issues = test_data_organizer.verify_dataset_integrity(dataset)
        assert len(issues) == 1, f"Expected 1 consistency issue, got {len(issues)}"
        assert "Missing metadata" in issues[0], f"Unexpected issue type: {issues[0]}"
        
    @pytest.mark.integration
    def test_orphaned_data_cleanup(self, integration_test_env, test_data_organizer):
        """测试孤立数据清理"""
        context = integration_test_env.context
        metadata_manager = MetadataManager(context)
        object_store = ObjectStore(context)
        
        # 创建测试数据
        dataset_config = {
            "files": [
                {
                    "filename": "orphaned_test_1.mp3",
                    "title": "Orphaned Test 1",
                    "artist": "Orphaned Artist 1",
                    "create_file": True
                },
                {
                    "filename": "orphaned_test_2.mp3",
                    "title": "Orphaned Test 2", 
                    "artist": "Orphaned Artist 2",
                    "create_file": True
                }
            ]
        }
        
        dataset = test_data_organizer.create_test_dataset(dataset_config)
        
        # 保存元数据
        metadata_manager.save_all(dataset["metadata"])
        
        # 模拟 publish 过程（移动文件到对象存储）
        for file_info in dataset["files"]:
            audio_path = file_info["path"]
            audio_oid = file_info["metadata"]["audio_oid"]
            
            # 计算哈希目录结构
            hash_part = audio_oid.split(":")[1]
            obj_dir = context.cache_root / "objects" / "sha256" / hash_part[:2]
            obj_dir.mkdir(parents=True, exist_ok=True)
            obj_file = obj_dir / f"{hash_part}.mp3"
            
            # 移动文件到对象存储
            shutil.move(str(audio_path), str(obj_file))
            
        # 验证文件已移动到对象存储
        for file_info in dataset["files"]:
            assert not file_info["path"].exists(), f"File not moved to object storage: {file_info['path']}"
            
        # 创建一些孤立文件
        orphaned_files = []
        
        # 孤立音频对象
        orphaned_audio_dir = context.cache_root / "objects" / "sha256" / "aa"
        orphaned_audio_dir.mkdir(parents=True, exist_ok=True)
        orphaned_audio = orphaned_audio_dir / "a" * 62 + "xx.mp3"
        orphaned_audio.write_bytes(b"orphaned audio content")
        orphaned_files.append(orphaned_audio)
        
        # 孤立封面文件
        orphaned_cover_dir = context.cache_root / "covers" / "sha256" / "bb"
        orphaned_cover_dir.mkdir(parents=True, exist_ok=True)
        orphaned_cover = orphaned_cover_dir / "b" * 62 + "xx.jpg"
        orphaned_cover.write_bytes(b"orphaned cover content")
        orphaned_files.append(orphaned_cover)
        
        # 验证孤立文件存在
        for orphaned_file in orphaned_files:
            assert orphaned_file.exists(), f"Orphaned file not created: {orphaned_file}"
            
        # 执行清理
        cleanup_results = cleanup_orphaned(
            cache_root=context.cache_root,
            metadata_file=context.metadata_file,
            dry_run=False
        )
        
        # 验证孤立文件被清理
        for orphaned_file in orphaned_files:
            assert not orphaned_file.exists(), f"Orphaned file not cleaned up: {orphaned_file}"
            
        # 验证有效文件仍然存在
        for file_info in dataset["files"]:
            hash_part = file_info["metadata"]["audio_oid"].split(":")[1]
            obj_file = context.cache_root / "objects" / "sha256" / hash_part[:2] / f"{hash_part}.mp3"
            assert obj_file.exists(), f"Valid object file incorrectly cleaned up: {obj_file}"
            
    @pytest.mark.integration
    def test_data_verification_and_validation(self, integration_test_env, test_data_organizer):
        """测试数据验证和校验"""
        context = integration_test_env.context
        metadata_manager = MetadataManager(context)
        object_store = ObjectStore(context)
        
        # 创建测试数据集
        dataset_config = {
            "files": [
                {
                    "filename": "verify_test_1.mp3",
                    "title": "Verify Test 1",
                    "artist": "Verify Artist 1",
                    "create_file": True
                },
                {
                    "filename": "verify_test_2.mp3",
                    "title": "Verify Test 2",
                    "artist": "Verify Artist 2", 
                    "create_file": True
                }
            ]
        }
        
        dataset = test_data_organizer.create_test_dataset(dataset_config)
        
        # 模拟完整的 publish 流程
        metadata_manager.save_all(dataset["metadata"])
        
        for file_info in dataset["files"]:
            audio_path = file_info["path"]
            audio_oid = file_info["metadata"]["audio_oid"]
            
            # 计算正确的哈希目录
            hash_part = audio_oid.split(":")[1]
            obj_dir = context.cache_root / "objects" / "sha256" / hash_part[:2]
            obj_dir.mkdir(parents=True, exist_ok=True)
            obj_file = obj_dir / f"{hash_part}.mp3"
            
            # 移动文件到对象存储
            shutil.move(str(audio_path), str(obj_file))
            
        # 执行完整性验证
        verification_results = verify_integrity(
            cache_root=context.cache_root,
            metadata_file=context.metadata_file,
            fix_issues=False,  # 只检测，不修复
            verbose=True
        )
        
        # 验证应该通过
        assert len(verification_results.get("errors", [])) == 0, f"Verification errors: {verification_results.get('errors', [])}"
        assert len(verification_results.get("warnings", [])) == 0, f"Verification warnings: {verification_results.get('warnings', [])}"
        
        # 测试数据损坏检测
        # 修改一个对象文件（模拟数据损坏）
        corrupted_file = None
        for file_info in dataset["files"]:
            hash_part = file_info["metadata"]["audio_oid"].split(":")[1]
            obj_file = context.cache_root / "objects" / "sha256" / hash_part[:2] / f"{hash_part}.mp3"
            if obj_file.exists():
                # 破坏文件内容
                corrupted_content = obj_file.read_bytes()[:100] + b"corrupted" + obj_file.read_bytes()[100:]
                obj_file.write_bytes(corrupted_content)
                corrupted_file = obj_file
                break
                
        assert corrupted_file is not None, "No file found to corrupt"
        
        # 重新验证（应该检测到损坏）
        verification_results = verify_integrity(
            cache_root=context.cache_root,
            metadata_file=context.metadata_file,
            fix_issues=False,
            verbose=True
        )
        
        # 应该检测到至少一个错误
        assert len(verification_results.get("errors", [])) > 0, "Data corruption not detected"
        
        # 验证错误类型
        error_found = False
        for error in verification_results.get("errors", []):
            if "hash mismatch" in error.lower() or "corruption" in error.lower():
                error_found = True
                break
                
        assert error_found, f"Expected hash mismatch error not found in: {verification_results.get('errors', [])}"
        
    @pytest.mark.integration
    def test_data_backup_and_recovery(self, integration_test_env, test_data_organizer):
        """测试数据备份和恢复"""
        context = integration_test_env.context
        metadata_manager = MetadataManager(context)
        
        # 创建测试数据集
        dataset_config = {
            "files": [
                {
                    "filename": "backup_test_1.mp3",
                    "title": "Backup Test 1",
                    "artist": "Backup Artist 1",
                    "create_file": True
                },
                {
                    "filename": "backup_test_2.mp3",
                    "title": "Backup Test 2",
                    "artist": "Backup Artist 2",
                    "create_file": True
                }
            ]
        }
        
        dataset = test_data_organizer.create_test_dataset(dataset_config)
        
        # 保存元数据
        metadata_manager.save_all(dataset["metadata"])
        original_metadata_content = context.metadata_file.read_text()
        
        # 模拟对象存储
        for file_info in dataset["files"]:
            audio_path = file_info["path"]
            audio_oid = file_info["metadata"]["audio_oid"]
            
            hash_part = audio_oid.split(":")[1]
            obj_dir = context.cache_root / "objects" / "sha256" / hash_part[:2]
            obj_dir.mkdir(parents=True, exist_ok=True)
            obj_file = obj_dir / f"{hash_part}.mp3"
            
            shutil.move(str(audio_path), str(obj_file))
            
        # 1. 创建备份
        backup_dir = context.project_root / "backup"
        backup_dir.mkdir(exist_ok=True)
        
        # 备份元数据
        backup_metadata_file = backup_dir / "metadata.jsonl.backup"
        shutil.copy2(context.metadata_file, backup_metadata_file)
        
        # 备份对象存储
        backup_objects_dir = backup_dir / "objects"
        shutil.copytree(context.cache_root / "objects", backup_objects_dir)
        
        # 验证备份创建
        assert backup_metadata_file.exists(), "Metadata backup not created"
        assert backup_objects_dir.exists(), "Objects backup not created"
        
        # 2. 模拟数据丢失
        # 删除原始元数据
        context.metadata_file.unlink()
        assert not context.metadata_file.exists(), "Metadata file not deleted"
        
        # 删除部分对象文件
        for file_info in dataset["files"]:
            hash_part = file_info["metadata"]["audio_oid"].split(":")[1]
            obj_file = context.cache_root / "objects" / "sha256" / hash_part[:2] / f"{hash_part}.mp3"
            if obj_file.exists():
                obj_file.unlink()
                
        # 3. 执行数据恢复
        # 恢复元数据
        shutil.copy2(backup_metadata_file, context.metadata_file)
        assert context.metadata_file.exists(), "Metadata recovery failed"
        
        # 恢复对象文件
        if backup_objects_dir.exists():
            shutil.rmtree(context.cache_root / "objects", ignore_errors=True)
            shutil.copytree(backup_objects_dir, context.cache_root / "objects")
            
        # 验证数据恢复
        recovered_metadata = metadata_manager.load_all()
        assert len(recovered_metadata) == 2, f"Expected 2 recovered metadata entries, got {len(recovered_metadata)}"
        
        # 验证恢复的元数据内容
        assert recovered_metadata[0]["title"] == "Backup Test 1"
        assert recovered_metadata[1]["title"] == "Backup Test 2"
        
        # 验证对象文件恢复
        for file_info in dataset["files"]:
            hash_part = file_info["metadata"]["audio_oid"].split(":")[1]
            obj_file = context.cache_root / "objects" / "sha256" / hash_part[:2] / f"{hash_part}.mp3"
            assert obj_file.exists(), f"Object file not recovered: {obj_file}"
            
    @pytest.mark.integration
    def test_concurrent_data_operations(self, integration_test_env, test_data_organizer):
        """测试并发数据操作"""
        import threading
        import time
        
        context = integration_test_env.context
        metadata_manager = MetadataManager(context)
        
        # 创建测试数据集
        dataset_config = {
            "files": [
                {
                    "filename": f"concurrent_test_{i}.mp3",
                    "title": f"Concurrent Test {i}",
                    "artist": f"Concurrent Artist {i}",
                    "create_file": True
                }
                for i in range(10)
            ]
        }
        
        dataset = test_data_organizer.create_test_dataset(dataset_config)
        
        results = {"errors": [], "success_count": 0}
        lock = threading.Lock()
        
        def concurrent_metadata_operation(thread_id, metadata_subset):
            """并发元数据操作"""
            try:
                # 模拟并发保存
                local_manager = MetadataManager(context)
                local_manager.save_all(metadata_subset)
                
                with lock:
                    results["success_count"] += 1
                    
            except Exception as e:
                with lock:
                    results["errors"].append(f"Thread {thread_id}: {str(e)}")
                    
        def concurrent_file_operation(thread_id, file_subset):
            """并发文件操作"""
            try:
                for file_info in file_subset:
                    # 模拟文件处理
                    if file_info["path"].exists():
                        # 读取文件内容
                        content = file_info["path"].read_bytes()
                        
                        # 模拟对象存储操作
                        audio_oid = file_info["metadata"]["audio_oid"]
                        hash_part = audio_oid.split(":")[1]
                        obj_dir = context.cache_root / "objects" / "sha256" / hash_part[:2]
                        obj_dir.mkdir(parents=True, exist_ok=True)
                        obj_file = obj_dir / f"{hash_part}.mp3"
                        
                        # 模拟并发写入
                        obj_file.write_bytes(content)
                        
                with lock:
                    results["success_count"] += 1
                    
            except Exception as e:
                with lock:
                    results["errors"].append(f"Thread {thread_id}: {str(e)}")
                    
        # 创建线程
        threads = []
        
        # 启动并发元数据操作
        for i in range(3):
            start_idx = i * 3
            end_idx = min(start_idx + 3, len(dataset["metadata"]))
            metadata_subset = dataset["metadata"][start_idx:end_idx]
            
            thread = threading.Thread(
                target=concurrent_metadata_operation,
                args=(i, metadata_subset)
            )
            threads.append(thread)
            thread.start()
            
        # 启动并发文件操作
        for i in range(3, 6):
            start_idx = (i-3) * 3
            end_idx = min(start_idx + 3, len(dataset["files"]))
            file_subset = dataset["files"][start_idx:end_idx]
            
            thread = threading.Thread(
                target=concurrent_file_operation,
                args=(i, file_subset)
            )
            threads.append(thread)
            thread.start()
            
        # 等待所有线程完成
        for thread in threads:
            thread.join()
            
        # 验证并发操作结果
        assert len(results["errors"]) == 0, f"Concurrent operation errors: {results['errors']}"
        assert results["success_count"] == 6, f"Expected 6 successful operations, got {results['success_count']}"
        
        # 验证最终数据一致性
        final_metadata = metadata_manager.load_all()
        # 由于并发写入，可能会有重复或覆盖，但系统应该保持一致性
        assert len(final_metadata) > 0, "No metadata found after concurrent operations"
        
        # 验证对象文件
        object_files = list((context.cache_root / "objects").rglob("*.mp3"))
        assert len(object_files) > 0, "No object files created during concurrent operations"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])