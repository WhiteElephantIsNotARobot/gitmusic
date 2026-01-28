"""
基本集成测试 - 验证任务4.1的测试框架功能
"""
import pytest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

# 导入被测试的模块
from libgitmusic.metadata import MetadataManager
from libgitmusic.object_store import ObjectStore
from libgitmusic.context import Context


class TestBasicIntegration:
    """基本集成测试类"""
    
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
    
    @pytest.fixture
    def mock_transport(self):
        """模拟传输适配器"""
        transport = Mock()
        transport.upload.return_value = True
        transport.download.return_value = True
        transport.list_remote_files.return_value = []
        return transport
    
    def test_metadata_manager_creation(self, test_context):
        """测试元数据管理器创建"""
        metadata_manager = MetadataManager(test_context)
        
        # 验证元数据管理器已创建
        assert metadata_manager is not None
        assert hasattr(metadata_manager, 'load_all')
        assert hasattr(metadata_manager, 'save_all')
        
    def test_object_store_creation(self, test_context):
        """测试对象存储创建"""
        object_store = ObjectStore(test_context)
        
        # 验证对象存储已创建
        assert object_store is not None
        assert hasattr(object_store, 'exists')
        assert hasattr(object_store, 'store_audio')
        assert hasattr(object_store, 'get_audio_path')
        
    def test_context_creation(self, test_context):
        """测试上下文创建"""
        # 验证上下文已创建
        assert test_context is not None
        assert hasattr(test_context, 'work_dir')
        assert hasattr(test_context, 'cache_root')
        assert hasattr(test_context, 'release_dir')
        assert hasattr(test_context, 'transport_config')  # transport配置在transport_config中
        
        # 验证目录存在
        assert Path(test_context.work_dir).exists()
        assert Path(test_context.cache_root).exists()
        assert Path(test_context.release_dir).exists()
        
    def test_mock_transport(self, mock_transport):
        """测试模拟传输适配器"""
        # 测试上传
        result = mock_transport.upload("src", "dst")
        assert result is True
        mock_transport.upload.assert_called_once_with("src", "dst")
        
        # 测试下载
        result = mock_transport.download("remote", "local")
        assert result is True
        mock_transport.download.assert_called_once_with("remote", "local")
        
        # 测试文件列表
        files = mock_transport.list_remote_files()
        assert files == []
        mock_transport.list_remote_files.assert_called_once()
        
    def test_metadata_operations(self, test_context):
        """测试元数据操作"""
        metadata_manager = MetadataManager(test_context)
        
        # 测试加载所有元数据（应该为空）
        entries = metadata_manager.load_all()
        assert entries == []
        
        # 测试保存元数据 - 使用有效的audio_oid格式和必需的字段
        from datetime import datetime, timezone
        test_entry = {
            "title": "Test Song",
            "artists": ["Test Artist"],
            "album": "Test Album",
            "date": "2024-01-01",
            "audio_oid": "sha256:2cf24dba4f21d4288094e9b9eb389f15e76c1e5b09e940a0c8fb9c37c6a0a8f0",  # 有效的64字符哈希
            "created_at": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),  # 必需的ISO时间戳
        }
        
        metadata_manager.save_all([test_entry])
        
        # 验证元数据已保存
        entries = metadata_manager.load_all()
        assert len(entries) == 1
        assert entries[0]["title"] == "Test Song"
        assert entries[0]["artists"] == ["Test Artist"]
        assert entries[0]["album"] == "Test Album"
        
    def test_object_store_operations(self, test_context):
        """测试对象存储操作"""
        object_store = ObjectStore(test_context)
        
        # 测试对象不存在
        exists = object_store.exists("sha256:2cf24dba4f21d4288094e9b9eb389f15e76c1e5b09e940a0c8fb9c37c6a0a8f0")
        assert exists is False
        
        # ObjectStore使用store_audio/store_cover方法，不是直接的put方法
        # 这里只测试exists方法，因为store_audio需要实际的音频文件
        
    def test_integration_with_mocking(self, test_context, mock_transport):
        """测试集成与模拟"""
        # 创建元数据管理器
        metadata_manager = MetadataManager(test_context)
        
        # 创建对象存储
        object_store = ObjectStore(test_context)
        
        # 模拟音频文件处理
        with patch('libgitmusic.audio.AudioIO.get_audio_hash') as mock_hash:
            mock_hash.return_value = "sha256:2cf24dba4f21d4288094e9b9eb389f15e76c1e5b09e940a0c8fb9c37c6a0a8f0"
            
            # 创建测试文件
            work_dir = Path(test_context.work_dir)
            test_file = work_dir / "test_song.mp3"
            test_file.write_text("fake mp3 content")
            
            # 保存元数据条目 - 使用save_all方法
            from datetime import datetime, timezone
            entry = {
                "title": "Mock Song",
                "artists": ["Mock Artist"],
                "album": "Mock Album",
                "date": "2024-01-01",
                "audio_oid": mock_hash.return_value,
                "created_at": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),  # 必需的ISO时间戳
            }
            
            metadata_manager.save_all([entry])
            
            # 验证集成
            entries = metadata_manager.load_all()
            assert len(entries) == 1
            assert entries[0]["audio_oid"] == mock_hash.return_value
            
    def test_error_handling(self, test_context):
        """测试错误处理"""
        metadata_manager = MetadataManager(test_context)
        
        # 测试处理无效元数据
        with pytest.raises(Exception):
            metadata_manager.save({"invalid": "entry"})
            
    def test_file_operations(self, test_context):
        """测试文件操作"""
        work_dir = Path(test_context.work_dir)
        
        # 创建测试文件
        test_file = work_dir / "test.mp3"
        test_content = b"fake mp3 content"
        test_file.write_bytes(test_content)
        
        # 验证文件存在
        assert test_file.exists()
        assert test_file.read_bytes() == test_content
        
        # 测试文件删除
        test_file.unlink()
        assert not test_file.exists()
        
    def test_directory_cleanup(self, test_context):
        """测试目录清理"""
        work_dir = Path(test_context.work_dir)
        
        # 创建测试文件和目录
        test_file = work_dir / "test.mp3"
        test_file.write_text("test content")
        
        test_subdir = work_dir / "subdir"
        test_subdir.mkdir()
        sub_file = test_subdir / "sub_test.mp3"
        sub_file.write_text("sub test content")
        
        # 验证文件存在
        assert test_file.exists()
        assert sub_file.exists()
        
        # 清理目录
        for item in work_dir.iterdir():
            if item.is_file():
                item.unlink()
            elif item.is_dir():
                shutil.rmtree(item)
        
        # 验证目录已清理
        assert not test_file.exists()
        assert not test_subdir.exists()
        assert len(list(work_dir.iterdir())) == 0