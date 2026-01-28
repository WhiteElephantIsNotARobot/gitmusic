"""
边界情况测试
测试GitMusic在各种边界条件下的行为
"""
import pytest
import tempfile
import shutil
import json
from pathlib import Path
from unittest.mock import Mock, patch
import os

# 导入被测试的模块
from libgitmusic.metadata import MetadataManager
from libgitmusic.object_store import ObjectStore
from libgitmusic.context import Context
from libgitmusic.commands.publish import publish_logic, execute_publish
from libgitmusic.commands.sync import sync_logic
from libgitmusic.commands.release import release_logic, execute_release


class TestEdgeCases:
    """边界情况测试类"""
    
    @pytest.fixture
    def edge_case_environment(self):
        """创建边界情况测试环境"""
        class EdgeCaseEnvironment:
            def __init__(self):
                self.temp_dir = None
                self.context = None
                
            def setup(self):
                """设置边界情况测试环境"""
                self.temp_dir = Path(tempfile.mkdtemp(prefix="gitmusic_edge_case_test_"))
                
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
                        "host": "edge.test.com",
                        "user": "edge_user",
                        "path": "/edge/test",
                        "private_key": "/edge/key",
                    },
                    "edge_cases": {
                        "test_mode": True,
                        "handle_boundaries": True,
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
                
            def cleanup(self):
                """清理边界情况测试环境"""
                if self.temp_dir and self.temp_dir.exists():
                    shutil.rmtree(self.temp_dir, ignore_errors=True)
                    
            def create_edge_case_file(self, case_type, **kwargs):
                """创建边界情况文件"""
                if case_type == "empty":
                    return self._create_empty_file(**kwargs)
                elif case_type == "very_large":
                    return self._create_very_large_file(**kwargs)
                elif case_type == "special_chars":
                    return self._create_special_chars_file(**kwargs)
                elif case_type == "unicode":
                    return self._create_unicode_file(**kwargs)
                elif case_type == "long_path":
                    return self._create_long_path_file(**kwargs)
                elif case_type == "no_extension":
                    return self._create_no_extension_file(**kwargs)
                else:
                    raise ValueError(f"Unknown edge case: {case_type}")
                    
            def _create_empty_file(self, filename="empty_test.mp3"):
                """创建空文件"""
                test_file = self.context.work_dir / filename
                test_file.write_text("")
                return test_file
                
            def _create_very_large_file(self, filename="large_test.mp3", size_mb=100):
                """创建大文件"""
                test_file = self.context.work_dir / filename
                # 创建指定大小的文件
                with open(test_file, 'wb') as f:
                    f.write(b'X' * (size_mb * 1024 * 1024))
                return test_file
                
            def _create_special_chars_file(self, filename="special@chars#test$.mp3"):
                """创建包含特殊字符的文件名"""
                test_file = self.context.work_dir / filename
                content = b"SPECIAL_CHARS_TEST_CONTENT"
                test_file.write_bytes(content)
                return test_file
                
            def _create_unicode_file(self, filename="测试音乐文件🎵.mp3"):
                """创建Unicode文件名"""
                test_file = self.context.work_dir / filename
                content = b"UNICODE_TEST_CONTENT"
                test_file.write_bytes(content)
                return test_file
                
            def _create_long_path_file(self, filename="a" * 200 + ".mp3"):
                """创建长路径文件"""
                test_file = self.context.work_dir / filename
                content = b"LONG_PATH_TEST_CONTENT"
                test_file.write_bytes(content)
                return test_file
                
            def _create_no_extension_file(self, filename="no_extension_test"):
                """创建无扩展名文件"""
                test_file = self.context.work_dir / filename
                content = b"NO_EXTENSION_TEST_CONTENT"
                test_file.write_bytes(content)
                return test_file
                
            def create_edge_case_metadata(self, case_type, **kwargs):
                """创建边界情况元数据"""
                if case_type == "empty_fields":
                    return self._create_empty_fields_metadata(**kwargs)
                elif case_type == "very_long_strings":
                    return self._create_very_long_strings_metadata(**kwargs)
                elif case_type == "unicode_metadata":
                    return self._create_unicode_metadata(**kwargs)
                elif case_type == "special_chars_metadata":
                    return self._create_special_chars_metadata(**kwargs)
                elif case_type == "missing_required":
                    return self._create_missing_required_metadata(**kwargs)
                elif case_type == "invalid_dates":
                    return self._create_invalid_dates_metadata(**kwargs)
                else:
                    raise ValueError(f"Unknown metadata edge case: {case_type}")
                    
            def _create_empty_fields_metadata(self):
                """创建空字段元数据"""
                return {
                    "audio_oid": "sha256:empty_test_hash",
                    "title": "",
                    "artists": [],
                    "album": "",
                    "date": "",
                    "created_at": "2024-01-01T00:00:00Z",
                }
                
            def _create_very_long_strings_metadata(self):
                """创建超长字符串元数据"""
                return {
                    "audio_oid": "sha256:long_string_test_hash",
                    "title": "A" * 1000,
                    "artists": ["B" * 500] * 10,
                    "album": "C" * 1000,
                    "date": "2024-01-01",
                    "created_at": "2024-01-01T00:00:00Z",
                }
                
            def _create_unicode_metadata(self):
                """创建Unicode元数据"""
                return {
                    "audio_oid": "sha256:unicode_test_hash",
                    "title": "测试歌曲标题🎵",
                    "artists": ["测试艺术家1🎤", "测试艺术家2🎸"],
                    "album": "测试专辑名称💿",
                    "date": "2024-01-01",
                    "created_at": "2024-01-01T00:00:00Z",
                }
                
            def _create_special_chars_metadata(self):
                """创建特殊字符元数据"""
                return {
                    "audio_oid": "sha256:special_chars_test_hash",
                    "title": "Song@#$%^&*()_+",
                    "artists": ["Artist!@#", "Band$%^"],
                    "album": "Album<>?:\"{}|",
                    "date": "2024-01-01",
                    "created_at": "2024-01-01T00:00:00Z",
                }
                
            def _create_missing_required_metadata(self):
                """创建缺少必填字段的元数据"""
                return {
                    # 缺少 audio_oid
                    "title": "Missing Required Fields",
                    "artists": ["Test Artist"],
                    "created_at": "2024-01-01T00:00:00Z",
                }
                
            def _create_invalid_dates_metadata(self):
                """创建无效日期元数据"""
                return {
                    "audio_oid": "sha256:invalid_date_test_hash",
                    "title": "Invalid Date Test",
                    "artists": ["Test Artist"],
                    "date": "invalid-date-string",
                    "created_at": "not-a-valid-timestamp",
                }
                
            def get_metadata_manager(self):
                """获取元数据管理器"""
                return MetadataManager(self.context)
                
            def get_object_store(self):
                """获取对象存储"""
                return ObjectStore(self.context)
                
        env = EdgeCaseEnvironment()
        env.setup()
        yield env
        env.cleanup()
        
    @pytest.mark.edge_cases
    def test_empty_file_handling(self, edge_case_environment):
        """测试空文件处理"""
        env = edge_case_environment
        
        # 创建空文件
        empty_file = env.create_edge_case_file("empty")
        assert empty_file.exists(), "Empty file creation failed"
        assert empty_file.stat().st_size == 0, "Empty file not actually empty"
        
        # 测试发布逻辑对空文件的处理
        metadata_manager = env.get_metadata_manager()
        to_process, error = publish_logic(metadata_manager)
        
        # 空文件应该被正确处理（可能被跳过或报错）
        # 具体行为取决于实现
        assert error is None or "empty" in str(error).lower(), "Empty file handling inconsistent"
        
    @pytest.mark.edge_cases
    def test_very_large_file_handling(self, edge_case_environment):
        """测试大文件处理"""
        env = edge_case_environment
        
        # 创建大文件（10MB）
        large_file = env.create_edge_case_file("very_large", size_mb=10)
        assert large_file.exists(), "Large file creation failed"
        assert large_file.stat().st_size == 10 * 1024 * 1024, "Large file size incorrect"
        
        # 测试发布逻辑对大文件的处理
        metadata_manager = env.get_metadata_manager()
        to_process, error = publish_logic(metadata_manager)
        
        # 大文件应该被正确处理
        assert error is None or "large" in str(error).lower(), "Large file handling failed"
        
    @pytest.mark.edge_cases
    def test_special_characters_filename(self, edge_case_environment):
        """测试特殊字符文件名"""
        env = edge_case_environment
        
        # 创建特殊字符文件
        special_file = env.create_edge_case_file("special_chars")
        assert special_file.exists(), "Special chars file creation failed"
        assert "@" in special_file.name, "Special characters not preserved in filename"
        
        # 测试文件系统操作
        assert special_file.is_file(), "Special chars file system operations failed"
        content = special_file.read_bytes()
        assert content == b"SPECIAL_CHARS_TEST_CONTENT", "Special chars file content corrupted"
        
    @pytest.mark.edge_cases
    def test_unicode_filename_handling(self, edge_case_environment):
        """测试Unicode文件名处理"""
        env = edge_case_environment
        
        # 创建Unicode文件
        unicode_file = env.create_edge_case_file("unicode")
        assert unicode_file.exists(), "Unicode file creation failed"
        assert "测试" in unicode_file.name, "Unicode characters not preserved"
        assert "🎵" in unicode_file.name, "Emoji characters not preserved"
        
        # 测试文件系统操作
        assert unicode_file.is_file(), "Unicode file system operations failed"
        content = unicode_file.read_bytes()
        assert content == b"UNICODE_TEST_CONTENT", "Unicode file content corrupted"
        
    @pytest.mark.edge_cases
    def test_long_path_handling(self, edge_case_environment):
        """测试长路径处理"""
        env = edge_case_environment
        
        # 创建长路径文件
        long_path_file = env.create_edge_case_file("long_path")
        
        # 长路径可能在某些系统上有问题，需要特殊处理
        try:
            assert long_path_file.exists(), "Long path file creation failed"
            content = long_path_file.read_bytes()
            assert content == b"LONG_PATH_TEST_CONTENT", "Long path file content corrupted"
        except (OSError, FileNotFoundError) as e:
            # 长路径在某些系统上可能不受支持
            pytest.skip(f"Long path not supported on this system: {e}")
            
    @pytest.mark.edge_cases
    def test_no_extension_file_handling(self, edge_case_environment):
        """测试无扩展名文件处理"""
        env = edge_case_environment
        
        # 创建无扩展名文件
        no_ext_file = env.create_edge_case_file("no_extension")
        assert no_ext_file.exists(), "No extension file creation failed"
        assert not no_ext_file.suffix, "File should have no extension"
        
        # 测试发布逻辑对无扩展名文件的处理
        metadata_manager = env.get_metadata_manager()
        to_process, error = publish_logic(metadata_manager)
        
        # 无扩展名文件应该被正确处理（可能被跳过或特殊处理）
        # 具体行为取决于实现
        assert error is None or "extension" in str(error).lower(), "No extension file handling inconsistent"
        
    @pytest.mark.edge_cases
    def test_empty_metadata_fields(self, edge_case_environment):
        """测试空元数据字段"""
        env = edge_case_environment
        
        # 创建空字段元数据
        empty_metadata = env.create_edge_case_metadata("empty_fields")
        
        metadata_manager = env.get_metadata_manager()
        
        # 保存空字段元数据
        metadata_manager.save_all([empty_metadata])
        
        # 加载并验证
        loaded_entries = metadata_manager.load_all()
        assert len(loaded_entries) == 1, "Empty metadata not saved correctly"
        
        loaded_entry = loaded_entries[0]
        assert loaded_entry["title"] == "", "Empty title not preserved"
        assert loaded_entry["artists"] == [], "Empty artists list not preserved"
        assert loaded_entry["album"] == "", "Empty album not preserved"
        
    @pytest.mark.edge_cases
    def test_very_long_strings_metadata(self, edge_case_environment):
        """测试超长字符串元数据"""
        env = edge_case_environment
        
        # 创建超长字符串元数据
        long_strings_metadata = env.create_edge_case_metadata("very_long_strings")
        
        metadata_manager = env.get_metadata_manager()
        
        # 保存超长字符串元数据
        metadata_manager.save_all([long_strings_metadata])
        
        # 加载并验证
        loaded_entries = metadata_manager.load_all()
        assert len(loaded_entries) == 1, "Long strings metadata not saved correctly"
        
        loaded_entry = loaded_entries[0]
        assert len(loaded_entry["title"]) == 1000, "Long title not preserved"
        assert len(loaded_entry["artists"]) == 10, "Long artists list not preserved"
        assert len(loaded_entry["artists"][0]) == 500, "Long artist name not preserved"
        
    @pytest.mark.edge_cases
    def test_unicode_metadata_handling(self, edge_case_environment):
        """测试Unicode元数据处理"""
        env = edge_case_environment
        
        # 创建Unicode元数据
        unicode_metadata = env.create_edge_case_metadata("unicode_metadata")
        
        metadata_manager = env.get_metadata_manager()
        
        # 保存Unicode元数据
        metadata_manager.save_all([unicode_metadata])
        
        # 加载并验证
        loaded_entries = metadata_manager.load_all()
        assert len(loaded_entries) == 1, "Unicode metadata not saved correctly"
        
        loaded_entry = loaded_entries[0]
        assert "测试" in loaded_entry["title"], "Chinese characters not preserved"
        assert "🎵" in loaded_entry["title"], "Emoji characters not preserved"
        assert "测试" in loaded_entry["artists"][0], "Chinese artist name not preserved"
        assert "🎤" in loaded_entry["artists"][0], "Emoji in artist name not preserved"
        
    @pytest.mark.edge_cases
    def test_special_characters_metadata(self, edge_case_environment):
        """测试特殊字符元数据"""
        env = edge_case_environment
        
        # 创建特殊字符元数据
        special_metadata = env.create_edge_case_metadata("special_chars_metadata")
        
        metadata_manager = env.get_metadata_manager()
        
        # 保存特殊字符元数据
        metadata_manager.save_all([special_metadata])
        
        # 加载并验证
        loaded_entries = metadata_manager.load_all()
        assert len(loaded_entries) == 1, "Special chars metadata not saved correctly"
        
        loaded_entry = loaded_entries[0]
        assert "@#$%^&*()_+" in loaded_entry["title"], "Special chars in title not preserved"
        assert "!@#" in loaded_entry["artists"][0], "Special chars in artist not preserved"
        assert "<>?:\"{}|" in loaded_entry["album"], "Special chars in album not preserved"
        
    @pytest.mark.edge_cases
    def test_missing_required_metadata_fields(self, edge_case_environment):
        """测试缺少必填字段的元数据"""
        env = edge_case_environment
        
        # 创建缺少必填字段的元数据
        missing_metadata = env.create_edge_case_metadata("missing_required")
        
        metadata_manager = env.get_metadata_manager()
        
        # 尝试保存缺少必填字段的元数据
        try:
            metadata_manager.save_all([missing_metadata])
            # 如果保存成功，验证加载行为
            loaded_entries = metadata_manager.load_all()
            # 具体行为取决于实现的验证逻辑
        except (KeyError, ValueError, TypeError) as e:
            # 预期可能会因为缺少必填字段而失败
            pass
            
    @pytest.mark.edge_cases
    def test_invalid_dates_metadata(self, edge_case_environment):
        """测试无效日期元数据"""
        env = edge_case_environment
        
        # 创建无效日期元数据
        invalid_dates_metadata = env.create_edge_case_metadata("invalid_dates")
        
        metadata_manager = env.get_metadata_manager()
        
        # 保存无效日期元数据
        metadata_manager.save_all([invalid_dates_metadata])
        
        # 加载并验证
        loaded_entries = metadata_manager.load_all()
        assert len(loaded_entries) == 1, "Invalid dates metadata not saved"
        
        loaded_entry = loaded_entries[0]
        # 无效日期的处理取决于具体实现
        # 这里只是验证数据被保存，不验证日期格式
        assert "invalid-date-string" == loaded_entry["date"], "Invalid date not preserved as-is"
        assert "not-a-valid-timestamp" == loaded_entry["created_at"], "Invalid timestamp not preserved as-is"
        
    @pytest.mark.edge_cases
    def test_zero_sized_cache_handling(self, edge_case_environment):
        """测试零大小缓存处理"""
        env = edge_case_environment
        
        # 清空缓存目录
        for item in env.context.cache_root.iterdir():
            if item.is_file():
                item.unlink()
            elif item.is_dir():
                shutil.rmtree(item)
                
        # 验证缓存目录为空
        cache_items = list(env.context.cache_root.iterdir())
        assert len(cache_items) == 0, "Cache directory not empty"
        
        # 测试在空缓存情况下的操作
        metadata_manager = env.get_metadata_manager()
        to_process, error = publish_logic(metadata_manager)
        
        # 空缓存应该被正确处理
        assert error is None, "Empty cache handling failed"
        
    @pytest.mark.edge_cases
    def test_concurrent_file_operations(self, edge_case_environment):
        """测试并发文件操作"""
        import threading
        import time
        
        env = edge_case_environment
        
        results = {"errors": [], "success_count": 0}
        lock = threading.Lock()
        
        def concurrent_file_operation(thread_id):
            """并发文件操作"""
            try:
                # 创建文件
                test_file = env.context.work_dir / f"concurrent_edge_test_{thread_id}.mp3"
                content = f"CONCURRENT_EDGE_TEST_{thread_id}".encode()
                test_file.write_bytes(content)
                
                # 验证文件
                assert test_file.exists(), f"Thread {thread_id}: File creation failed"
                read_content = test_file.read_bytes()
                assert read_content == content, f"Thread {thread_id}: Content mismatch"
                
                # 删除文件
                test_file.unlink()
                assert not test_file.exists(), f"Thread {thread_id}: File deletion failed"
                
                with lock:
                    results["success_count"] += 1
                    
            except Exception as e:
                with lock:
                    results["errors"].append(f"Thread {thread_id}: {str(e)}")
                    
        # 创建并发线程
        threads = []
        for i in range(10):
            thread = threading.Thread(target=concurrent_file_operation, args=(i,))
            threads.append(thread)
            thread.start()
            
        # 等待所有线程完成
        for thread in threads:
            thread.join()
            
        # 验证并发操作结果
        assert len(results["errors"]) == 0, f"Concurrent operations failed: {results['errors']}"
        assert results["success_count"] == 10, f"Expected 10 successful operations, got {results['success_count']}"
        
    @pytest.mark.edge_cases
    def test_boundary_value_conditions(self, edge_case_environment):
        """测试边界值条件"""
        env = edge_case_environment
        
        # 测试各种边界值
        boundary_cases = [
            {"name": "single_character", "value": "a"},
            {"name": "single_artist", "value": ["single"]},
            {"name": "empty_list", "value": []},
            {"name": "max_int", "value": 2147483647},
            {"name": "min_int", "value": -2147483648},
            {"name": "zero", "value": 0},
            {"name": "negative", "value": -1},
        ]
        
        metadata_manager = env.get_metadata_manager()
        
        for case in boundary_cases:
            test_entry = {
                "audio_oid": f"sha256:boundary_test_{case['name']}",
                "title": case["value"] if isinstance(case["value"], str) else f"Boundary Test {case['name']}",
                "artists": case["value"] if isinstance(case["value"], list) else ["Test Artist"],
                "album": f"Boundary Album {case['name']}",
                "created_at": "2024-01-01T00:00:00Z",
            }
            
            # 保存边界值元数据
            metadata_manager.save_all([test_entry])
            
            # 加载并验证
            loaded_entries = metadata_manager.load_all()
            assert len(loaded_entries) == 1, f"Boundary case {case['name']} failed"
            
            # 清理以便下一个测试
            if env.context.metadata_file.exists():
                env.context.metadata_file.write_text("")
                

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])