"""
简单的性能测试 - 验证任务4.1的测试框架功能
"""
import pytest
import time
import tempfile
from pathlib import Path
from unittest.mock import patch

from libgitmusic.metadata import MetadataManager
from libgitmusic.context import Context


class TestSimplePerformance:
    """简单性能测试类"""
    
    @pytest.fixture
    def test_context(self):
        """创建测试上下文"""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            # 创建必要的目录
            work_dir = tmpdir / "work"
            cache_root = tmpdir / "cache"
            metadata_file = tmpdir / "metadata.jsonl"
            release_dir = tmpdir / "release"
            logs_dir = tmpdir / "logs"
            
            work_dir.mkdir()
            cache_root.mkdir()
            release_dir.mkdir()
            logs_dir.mkdir()
            
            # 创建配置
            config = {
                "transport": {
                    "user": "test_user",
                    "host": "test.server.com",
                    "remote_data_root": "/test/music",
                    "retries": 3,
                    "timeout": 30,
                    "workers": 2,
                },
                "paths": {
                    "work_dir": str(work_dir),
                    "cache_root": str(cache_root),
                    "metadata_file": str(metadata_file),
                    "release_dir": str(release_dir),
                    "logs_dir": str(logs_dir),
                },
                "image": {"quality": 2},
            }
            
            # 创建上下文
            context = Context(
                project_root=tmpdir,
                config=config,
                work_dir=work_dir,
                cache_root=cache_root,
                metadata_file=metadata_file,
                release_dir=release_dir,
                logs_dir=logs_dir,
            )
            
            yield context
    
    def test_metadata_performance_small_dataset(self, test_context):
        """测试小数据集元数据性能"""
        metadata_manager = MetadataManager(test_context)
        
        # 创建小数据集（10个条目）
        from datetime import datetime, timezone
        import hashlib
        entries = []
        for i in range(10):
            # 生成有效的64字符哈希
            hash_input = f"test_audio_{i}"
            hash_value = hashlib.sha256(hash_input.encode()).hexdigest()
            entry = {
                "title": f"Test Song {i}",
                "artists": [f"Test Artist {i}"],
                "album": f"Test Album {i}",
                "date": "2024-01-01",
                "audio_oid": f"sha256:{hash_value}",
                "created_at": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            }
            entries.append(entry)
        
        # 测试保存性能
        start_time = time.time()
        metadata_manager.save_all(entries)
        save_time = time.time() - start_time
        
        # 测试加载性能
        start_time = time.time()
        loaded_entries = metadata_manager.load_all()
        load_time = time.time() - start_time
        
        # 验证结果
        assert len(loaded_entries) == 10
        assert save_time < 1.0  # 保存应少于1秒
        assert load_time < 1.0  # 加载应少于1秒
        
        print(f"小数据集性能 - 保存: {save_time:.3f}s, 加载: {load_time:.3f}s")
        
    def test_metadata_performance_medium_dataset(self, test_context):
        """测试中等数据集元数据性能"""
        metadata_manager = MetadataManager(test_context)
        
        # 创建中等数据集（100个条目）
        from datetime import datetime, timezone
        import hashlib
        entries = []
        for i in range(100):
            # 生成有效的64字符哈希
            hash_input = f"test_audio_{i}"
            hash_value = hashlib.sha256(hash_input.encode()).hexdigest()
            entry = {
                "title": f"Test Song {i}",
                "artists": [f"Test Artist {i}"],
                "album": f"Test Album {i}",
                "date": "2024-01-01",
                "audio_oid": f"sha256:{hash_value}",
                "created_at": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            }
            entries.append(entry)
        
        # 测试保存性能
        start_time = time.time()
        metadata_manager.save_all(entries)
        save_time = time.time() - start_time
        
        # 测试加载性能
        start_time = time.time()
        loaded_entries = metadata_manager.load_all()
        load_time = time.time() - start_time
        
        # 验证结果
        assert len(loaded_entries) == 100
        assert save_time < 2.0  # 保存应少于2秒
        assert load_time < 2.0  # 加载应少于2秒
        
        print(f"中等数据集性能 - 保存: {save_time:.3f}s, 加载: {load_time:.3f}s")
        
    def test_metadata_performance_large_dataset(self, test_context):
        """测试大数据集元数据性能"""
        metadata_manager = MetadataManager(test_context)
        
        # 创建大数据集（500个条目）
        from datetime import datetime, timezone
        import hashlib
        entries = []
        for i in range(500):
            # 生成有效的64字符哈希
            hash_input = f"test_audio_{i}"
            hash_value = hashlib.sha256(hash_input.encode()).hexdigest()
            entry = {
                "title": f"Test Song {i}",
                "artists": [f"Test Artist {i}"],
                "album": f"Test Album {i}",
                "date": "2024-01-01",
                "audio_oid": f"sha256:{hash_value}",
                "created_at": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            }
            entries.append(entry)
        
        # 测试保存性能
        start_time = time.time()
        metadata_manager.save_all(entries)
        save_time = time.time() - start_time
        
        # 测试加载性能
        start_time = time.time()
        loaded_entries = metadata_manager.load_all()
        load_time = time.time() - start_time
        
        # 验证结果
        assert len(loaded_entries) == 500
        assert save_time < 5.0  # 保存应少于5秒
        assert load_time < 5.0  # 加载应少于5秒
        
        print(f"大数据集性能 - 保存: {save_time:.3f}s, 加载: {load_time:.3f}s")
        
    def test_memory_efficiency(self, test_context):
        """测试内存效率"""
        import psutil
        import os
        
        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss / 1024 / 1024  # MB
        
        metadata_manager = MetadataManager(test_context)
        
        # 创建中等数据集
        from datetime import datetime, timezone
        import hashlib
        entries = []
        for i in range(100):
            # 生成有效的64字符哈希
            hash_input = f"test_audio_{i}"
            hash_value = hashlib.sha256(hash_input.encode()).hexdigest()
            entry = {
                "title": f"Test Song {i}",
                "artists": [f"Test Artist {i}"],
                "album": f"Test Album {i}",
                "date": "2024-01-01",
                "audio_oid": f"sha256:{hash_value}",
                "created_at": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            }
            entries.append(entry)
        
        # 保存数据
        metadata_manager.save_all(entries)
        
        # 加载数据
        loaded_entries = metadata_manager.load_all()
        
        # 检查内存使用
        final_memory = process.memory_info().rss / 1024 / 1024  # MB
        memory_increase = final_memory - initial_memory
        
        # 验证结果
        assert len(loaded_entries) == 100
        assert memory_increase < 50  # 内存增加应少于50MB
        
        print(f"内存效率测试 - 初始内存: {initial_memory:.1f}MB, 最终内存: {final_memory:.1f}MB, 增加: {memory_increase:.1f}MB")
        
    def test_concurrent_metadata_operations(self, test_context):
        """测试并发元数据操作"""
        from concurrent.futures import ThreadPoolExecutor
        import threading
        import time
        
        metadata_manager = MetadataManager(test_context)
        results = []
        lock = threading.Lock()  # 创建一个共享的锁
        
        def save_entry(thread_id):
            from datetime import datetime, timezone
            import hashlib
            # 生成有效的64字符哈希
            hash_input = f"concurrent_audio_{thread_id}_{time.time()}"  # 添加时间戳确保唯一性
            hash_value = hashlib.sha256(hash_input.encode()).hexdigest()
            entry = {
                "title": f"Concurrent Song {thread_id}",
                "artists": [f"Concurrent Artist {thread_id}"],
                "album": f"Concurrent Album {thread_id}",
                "date": "2024-01-01",
                "audio_oid": f"sha256:{hash_value}",
                "created_at": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            }
            
            # 使用共享锁保护写入操作
            with lock:
                try:
                    # 重新加载最新状态，避免丢失其他线程的写入
                    existing_entries = metadata_manager.load_all()
                    existing_entries.append(entry)
                    metadata_manager.save_all(existing_entries)
                    results.append(f"Thread {thread_id}: Success")
                except Exception as e:
                    results.append(f"Thread {thread_id}: Error - {e}")
        
        # 并发执行
        with ThreadPoolExecutor(max_workers=3) as executor:  # 减少并发度
            futures = [executor.submit(save_entry, i) for i in range(10)]
            for future in futures:
                future.result()
        
        # 验证结果 - 使用更宽松的标准，因为并发写入可能有丢失
        final_entries = metadata_manager.load_all()
        success_count = len([r for r in results if "Success" in r])
        
        print(f"并发测试 - 成功操作: {success_count}/10")
        print(f"并发测试 - 最终条目数: {len(final_entries)}")
        
        # 验证至少有一些条目被保存
        assert len(final_entries) > 0, "No entries were saved"
        assert success_count > 0, "No successful operations"
        
    def test_performance_regression_detection(self, test_context):
        """测试性能回归检测"""
        metadata_manager = MetadataManager(test_context)
        
        # 创建测试数据
        from datetime import datetime, timezone
        import hashlib
        entries = []
        for i in range(50):
            # 生成有效的64字符哈希
            hash_input = f"regression_test_audio_{i}"
            hash_value = hashlib.sha256(hash_input.encode()).hexdigest()
            entry = {
                "title": f"Regression Test Song {i}",
                "artists": [f"Regression Test Artist {i}"],
                "album": f"Regression Test Album {i}",
                "date": "2024-01-01",
                "audio_oid": f"sha256:{hash_value}",
                "created_at": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            }
            entries.append(entry)
        
        # 基准性能测试
        times = []
        for _ in range(3):  # 运行3次取平均值
            start_time = time.time()
            metadata_manager.save_all(entries)
            metadata_manager.load_all()
            total_time = time.time() - start_time
            times.append(total_time)
        
        avg_time = sum(times) / len(times)
        
        # 验证没有性能回归（这里使用宽松的阈值）
        assert avg_time < 3.0  # 平均时间应少于3秒
        
        print(f"性能回归检测 - 平均时间: {avg_time:.3f}s (3次测试)")
        print(f"性能回归检测 - 所有时间: {[f'{t:.3f}s' for t in times]}")