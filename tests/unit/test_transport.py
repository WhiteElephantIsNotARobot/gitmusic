import pytest
import tempfile
import os
import sys
from pathlib import Path
from unittest.mock import Mock, patch, call
import subprocess
import hashlib

# Import the modules to test
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "repo"))
from libgitmusic.transport import TransportAdapter
from libgitmusic.context import Context
from libgitmusic.results import RemoteResult
from libgitmusic.exceptions import TransportError


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test data."""
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
def context(temp_dir):
    """Create a Context object with temporary paths."""
    # Create a minimal config dict matching new API
    config = {
        "transport": {
            "host": "test.example.com",
            "user": "testuser",
            "remote_data_root": "/tmp/test",
            "retries": 3,
            "timeout": 60,
            "workers": 4,
        }
    }

    # Create paths
    work_dir = temp_dir / "work"
    cache_root = temp_dir / "cache"
    metadata_file = temp_dir / "metadata.jsonl"
    release_dir = temp_dir / "release"
    logs_dir = temp_dir / "logs"

    # Create directories
    for dir_path in [work_dir, cache_root, release_dir, logs_dir]:
        dir_path.mkdir(parents=True, exist_ok=True)

    # Create Context instance
    ctx = Context(
        project_root=temp_dir,
        config=config,
        work_dir=work_dir,
        cache_root=cache_root,
        metadata_file=metadata_file,
        release_dir=release_dir,
        logs_dir=logs_dir,
    )

    return ctx


@pytest.fixture
def transport_adapter(context):
    """Create a TransportAdapter instance with test context."""
    return TransportAdapter(context)


def test_init_with_config(transport_adapter, context):
    """Test initialization with config."""
    assert transport_adapter.host == "test.example.com"
    assert transport_adapter.user == "testuser"
    assert transport_adapter.remote_data_root == "/tmp/test"
    assert transport_adapter.retries == 3
    assert transport_adapter.timeout == 60
    assert transport_adapter.workers == 4


def test_list_remote_files(transport_adapter):
    """Test listing remote files."""
    with patch('subprocess.run') as mock_run:
        mock_result = Mock()
        mock_result.stdout = "song1.mp3\nsong2.mp3\ncover1.jpg\n"
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        files = transport_adapter.list_remote_files("music")
        
        assert files == ["song1.mp3", "song2.mp3", "cover1.jpg"]
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "ssh" in cmd
        assert "testuser@test.example.com" in cmd
        # The find command is part of the ssh command string
        assert any("find" in str(arg) for arg in cmd)


def test_list_remote_files_empty(transport_adapter):
    """Test listing remote files with empty result."""
    with patch('subprocess.run') as mock_run:
        mock_result = Mock()
        mock_result.stdout = ""
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        files = transport_adapter.list_remote_files("empty")
        
        assert files == []


def test_list_remote_files_error(transport_adapter):
    """Test listing remote files with error."""
    with patch('subprocess.run') as mock_run:
        mock_run.side_effect = subprocess.CalledProcessError(1, "find")

        files = transport_adapter.list_remote_files("error")
        
        assert files == []


def test_remote_exec_success(transport_adapter):
    """Test successful remote command execution."""
    with patch('subprocess.run') as mock_run:
        mock_result = Mock()
        mock_result.stdout = "command output"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        stdout, stderr = transport_adapter._remote_exec("ls -la")
        
        assert stdout == "command output"
        assert stderr == ""
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "ssh" in cmd
        assert "testuser@test.example.com" in cmd
        assert "ls -la" in cmd


def test_remote_exec_failure(transport_adapter):
    """Test failed remote command execution."""
    with patch('subprocess.run') as mock_run:
        mock_run.side_effect = subprocess.CalledProcessError(1, "ssh", stderr="Command failed")

        with pytest.raises(subprocess.CalledProcessError):
            transport_adapter._remote_exec("invalid command")


def test_remote_exec_timeout(transport_adapter):
    """Test remote command execution timeout."""
    with patch('subprocess.run') as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired("ssh", 60)

        with pytest.raises(subprocess.TimeoutExpired):
            transport_adapter._remote_exec("sleep 100")


def test_get_remote_hash_success(transport_adapter):
    """Test getting remote file hash successfully."""
    with patch.object(transport_adapter, '_remote_exec') as mock_exec:
        expected_hash = "a" * 64
        mock_exec.return_value = (f"{expected_hash}  /tmp/test/file.txt\n", "")

        result = transport_adapter._get_remote_hash("/tmp/test/file.txt")
        
        assert result == expected_hash
        mock_exec.assert_called_once_with("sha256sum /tmp/test/file.txt")


def test_get_remote_hash_invalid_output(transport_adapter):
    """Test getting remote hash with invalid output."""
    with patch.object(transport_adapter, '_remote_exec') as mock_exec:
        mock_exec.return_value = ("invalid output\n", "")

        result = transport_adapter._get_remote_hash("/tmp/test/file.txt")
        
        assert result is None


def test_get_remote_hash_file_not_found(transport_adapter):
    """Test getting remote hash for non-existent file."""
    with patch.object(transport_adapter, '_remote_exec') as mock_exec:
        mock_exec.side_effect = subprocess.CalledProcessError(1, "sha256sum")

        result = transport_adapter._get_remote_hash("/tmp/test/nonexistent.txt")
        
        assert result is None


def test_get_remote_hash_timeout(transport_adapter):
    """Test getting remote hash with timeout."""
    with patch.object(transport_adapter, '_remote_exec') as mock_exec:
        mock_exec.side_effect = subprocess.TimeoutExpired("sha256sum", 60)

        with pytest.raises(subprocess.TimeoutExpired):
            transport_adapter._get_remote_hash("/tmp/test/file.txt")


def test_upload_success(transport_adapter, temp_dir):
    """Test successful file upload."""
    test_file = temp_dir / "test.txt"
    content = b"test content"
    test_file.write_bytes(content)
    
    expected_hash = hashlib.sha256(content).hexdigest()
    
    with patch.object(transport_adapter, '_get_remote_hash') as mock_hash, \
         patch('subprocess.run') as mock_run:
        
        # Mock hash checks - file doesn't exist initially
        mock_hash.side_effect = [None, expected_hash, expected_hash]
        mock_run.return_value = Mock(returncode=0)
        
        result = transport_adapter.upload(test_file, "test.txt")
        
        assert result.success is True
        assert result.message == "Upload successful"
        assert result.remote_path == "test.txt"
        assert mock_run.call_count == 3  # mkdir, scp, mv


def test_upload_skip_existing(transport_adapter, temp_dir):
    """Test upload skip when file already exists with matching hash."""
    test_file = temp_dir / "test.txt"
    content = b"test content"
    test_file.write_bytes(content)
    
    expected_hash = hashlib.sha256(content).hexdigest()
    
    with patch.object(transport_adapter, '_get_remote_hash') as mock_hash:
        # Mock hash check - file exists with matching hash
        mock_hash.return_value = expected_hash
        
        result = transport_adapter.upload(test_file, "test.txt")
        
        assert result.success is True
        assert "Remote file already exists with matching hash" in result.message
        assert result.remote_path == "test.txt"
        mock_hash.assert_called_once()


def test_upload_overwrite_existing(transport_adapter, temp_dir):
    """Test upload overwrite when file exists with different hash."""
    test_file = temp_dir / "test.txt"
    content = b"test content"
    test_file.write_bytes(content)
    
    local_hash = hashlib.sha256(content).hexdigest()
    remote_hash = "b" * 64  # Different hash
    
    with patch.object(transport_adapter, '_get_remote_hash') as mock_hash, \
         patch('subprocess.run') as mock_run:
        
        # Mock hash checks - file exists with different hash, then successful upload
        mock_hash.side_effect = [remote_hash, local_hash, local_hash]
        mock_run.return_value = Mock(returncode=0)
        
        result = transport_adapter.upload(test_file, "test.txt")
        
        assert result.success is True
        assert result.message == "Upload successful"
        assert mock_run.call_count == 3  # mkdir, scp, mv


def test_upload_hash_mismatch(transport_adapter, temp_dir):
    """Test upload failure due to hash mismatch."""
    test_file = temp_dir / "test.txt"
    content = b"test content"
    test_file.write_bytes(content)
    
    local_hash = hashlib.sha256(content).hexdigest()
    wrong_hash = "wrong_hash"
    
    with patch.object(transport_adapter, '_get_remote_hash') as mock_hash, \
         patch('subprocess.run') as mock_run, \
         patch('time.sleep'):
        
        # Mock hash checks - file doesn't exist, but upload always has wrong hash
        # Need to provide enough wrong hash values for all retries
        mock_hash.side_effect = [None, wrong_hash, wrong_hash, wrong_hash, wrong_hash]
        mock_run.return_value = Mock(returncode=0)
        
        result = transport_adapter.upload(test_file, "test.txt")
        
        assert result.success is False
        assert "Hash mismatch" in result.message
        assert result.error is not None


def test_upload_with_retry(transport_adapter, temp_dir):
    """Test upload with retry mechanism."""
    test_file = temp_dir / "test.txt"
    content = b"test content"
    test_file.write_bytes(content)
    
    expected_hash = hashlib.sha256(content).hexdigest()
    
    with patch.object(transport_adapter, '_get_remote_hash') as mock_hash, \
         patch('subprocess.run') as mock_run, \
         patch('time.sleep') as mock_sleep:
        
        # Mock hash checks
        mock_hash.side_effect = [None, expected_hash, expected_hash]
        
        # Mock run failures then success
        mock_run.side_effect = [
            Mock(returncode=0),  # mkdir succeeds
            subprocess.CalledProcessError(1, "scp"),  # First SCP fails
            Mock(returncode=0),  # Second SCP succeeds
            Mock(returncode=0),  # mv succeeds
        ]
        
        result = transport_adapter.upload(test_file, "test.txt")
        
        assert result.success is True
        assert mock_run.call_count == 4  # mkdir + 2 SCP + mv
        assert mock_sleep.call_count == 1  # One retry with sleep


def test_upload_all_retries_fail(transport_adapter, temp_dir):
    """Test upload when all retries fail."""
    test_file = temp_dir / "test.txt"
    content = b"test content"
    test_file.write_bytes(content)
    
    with patch.object(transport_adapter, '_get_remote_hash') as mock_hash, \
         patch('subprocess.run') as mock_run, \
         patch('time.sleep'):
        
        # Mock hash check - file doesn't exist
        mock_hash.return_value = None
        
        # Mock all SCP attempts fail, but mkdir succeeds
        mock_run.side_effect = [
            Mock(returncode=0),  # mkdir succeeds
            subprocess.CalledProcessError(1, "scp"),  # First SCP fails
            subprocess.CalledProcessError(1, "scp"),  # Second SCP fails
            subprocess.CalledProcessError(1, "scp"),  # Third SCP fails
            subprocess.CalledProcessError(1, "scp"),  # Fourth SCP fails
        ]
        
        result = transport_adapter.upload(test_file, "test.txt")
        
        assert result.success is False
        assert "Upload failed after" in result.message
        assert result.error is not None
        # Should be called retries + 1 times for SCP, plus 1 for mkdir
        assert mock_run.call_count == transport_adapter.retries + 2


def test_upload_nonexistent_file(transport_adapter, temp_dir):
    """Test uploading a non-existent file."""
    non_existent = temp_dir / "nonexistent.txt"
    
    result = transport_adapter.upload(non_existent, "test.txt")
    
    assert result.success is False
    assert "Failed to compute local hash" in result.message
    assert result.error is not None


def test_download_success(transport_adapter, temp_dir):
    """Test successful file download."""
    local_path = temp_dir / "downloaded.txt"
    tmp_path = local_path.with_suffix(".tmp")
    
    with patch('subprocess.run') as mock_run, \
         patch('os.replace') as mock_replace:
        
        mock_run.return_value = Mock(returncode=0)
        mock_replace.return_value = None
        
        transport_adapter.download("remote.txt", local_path)
        
        assert mock_run.call_count == 1
        cmd = mock_run.call_args[0][0]
        assert "scp" in cmd
        assert "testuser@test.example.com:/tmp/test/remote.txt" in cmd
        assert str(tmp_path) in cmd
        mock_replace.assert_called_once_with(tmp_path, local_path)


def test_download_with_directory_creation(transport_adapter, temp_dir):
    """Test download creates parent directory."""
    local_path = temp_dir / "subdir" / "downloaded.txt"
    
    with patch('subprocess.run') as mock_run, \
         patch('os.replace') as mock_replace:
        
        mock_run.return_value = Mock(returncode=0)
        mock_replace.return_value = None
        
        transport_adapter.download("remote.txt", local_path)
        
        assert local_path.parent.exists()
        assert mock_run.call_count == 1
        mock_replace.assert_called_once()


def test_config_with_custom_values(temp_dir):
    """Test TransportAdapter with custom config values."""
    # Create a new context with custom config
    config = {
        "transport": {
            "host": "custom.example.com",
            "user": "customuser",
            "remote_data_root": "/custom/path",
            "retries": 5,
            "timeout": 120,
            "workers": 8,
        }
    }

    # Create paths
    work_dir = temp_dir / "work"
    cache_root = temp_dir / "cache"
    metadata_file = temp_dir / "metadata.jsonl"
    release_dir = temp_dir / "release"
    logs_dir = temp_dir / "logs"

    # Create directories
    for dir_path in [work_dir, cache_root, release_dir, logs_dir]:
        dir_path.mkdir(parents=True, exist_ok=True)

    # Create Context instance
    ctx = Context(
        project_root=temp_dir,
        config=config,
        work_dir=work_dir,
        cache_root=cache_root,
        metadata_file=metadata_file,
        release_dir=release_dir,
        logs_dir=logs_dir,
    )

    adapter = TransportAdapter(ctx)

    assert adapter.host == "custom.example.com"
    assert adapter.user == "customuser"
    assert adapter.remote_data_root == "/custom/path"
    assert adapter.retries == 5
    assert adapter.timeout == 120
    assert adapter.workers == 8


def test_config_with_missing_values(temp_dir):
    """Test TransportAdapter with minimal config."""
    # Create a new context with minimal config
    config = {
        "transport": {
            "host": "minimal.example.com",
            "user": "minimaluser",
        }
    }

    # Create paths
    work_dir = temp_dir / "work"
    cache_root = temp_dir / "cache"
    metadata_file = temp_dir / "metadata.jsonl"
    release_dir = temp_dir / "release"
    logs_dir = temp_dir / "logs"

    # Create directories
    for dir_path in [work_dir, cache_root, release_dir, logs_dir]:
        dir_path.mkdir(parents=True, exist_ok=True)

    # Create Context instance
    ctx = Context(
        project_root=temp_dir,
        config=config,
        work_dir=work_dir,
        cache_root=cache_root,
        metadata_file=metadata_file,
        release_dir=release_dir,
        logs_dir=logs_dir,
    )

    adapter = TransportAdapter(ctx)

    assert adapter.host == "minimal.example.com"
    assert adapter.user == "minimaluser"
    assert adapter.remote_data_root == ""  # Default empty
    assert adapter.retries == 3  # Default
    assert adapter.timeout == 60  # Default
    assert adapter.workers == 4  # Default


if __name__ == "__main__":
    pytest.main([__file__, "-v"])