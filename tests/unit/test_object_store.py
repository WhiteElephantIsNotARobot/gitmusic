import pytest
import tempfile
import os
import hashlib
import sys
from pathlib import Path
from unittest.mock import Mock, patch

# Import the modules to test
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "repo"))
from libgitmusic.object_store import ObjectStore
from libgitmusic.context import Context


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test data."""
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
def context(temp_dir):
    """Create a Context object with temporary paths."""
    # Create a minimal config dict
    config = {
        "transport": {
            "host": "test.example.com",
            "user": "testuser",
            "path": "/tmp/test",
            "private_key": "/tmp/key",
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
def object_store(context):
    """Create an ObjectStore instance with test context."""
    return ObjectStore(context)


def test_init_creates_directories(object_store):
    """Test that ObjectStore creates necessary directories on init."""
    assert object_store.objects_dir.exists()
    assert object_store.covers_dir.exists()
    assert object_store.objects_dir.is_dir()
    assert object_store.covers_dir.is_dir()


def test_get_object_path_audio(object_store):
    """Test path generation for audio objects."""
    hexdigest = "a" * 64
    oid = f"sha256:{hexdigest}"

    path = object_store._get_object_path(oid)

    # Expected: objects/sha256/aa/aaaaaaaa... (remaining 62 chars).mp3
    expected = object_store.objects_dir / "sha256" / hexdigest[:2] / f"{hexdigest}.mp3"
    assert path == expected


def test_get_object_path_cover(object_store):
    """Test path generation for cover objects (with .jpg suffix)."""
    hexdigest = "b" * 64
    oid = f"sha256:{hexdigest}"

    # For cover, we need to add .jpg suffix when calling _get_object_path
    path = object_store._get_object_path(oid + ".jpg")

    # Expected: covers/sha256/bb/bbbbbb... (remaining 62 chars).jpg
    expected = object_store.covers_dir / "sha256" / hexdigest[:2] / f"{hexdigest}.jpg"
    assert path == expected


def test_store_cover(object_store):
    """Test storing cover image data."""
    cover_data = b"fake jpeg data"
    hexdigest = hashlib.sha256(cover_data).hexdigest()
    expected_oid = f"sha256:{hexdigest}"

    # Store cover
    result = object_store.store_cover(cover_data, compute_hash=True)

    assert result.success is True
    assert result.oid == expected_oid

    # Verify file exists
    cover_path = object_store.get_cover_path(result.oid)
    assert cover_path is not None
    assert cover_path.exists()

    # Verify content
    with open(cover_path, "rb") as f:
        assert f.read() == cover_data


def test_store_cover_already_exists(object_store):
    """Test storing cover that already exists (idempotent)."""
    cover_data = b"existing cover"
    result1 = object_store.store_cover(cover_data)

    # Store again
    result2 = object_store.store_cover(cover_data)

    assert result1.oid == result2.oid
    assert result2.message == "Cover object already exists"
    # Should not raise, just return existing OID


def test_store_audio_with_temp_file(object_store, temp_dir):
    """Test storing audio from a temporary file."""
    # Create a temporary audio file
    audio_data = b"fake mp3 data"
    temp_audio = temp_dir / "test.mp3"
    temp_audio.write_bytes(audio_data)

    # Mock AudioIO.get_audio_hash to return a known hash
    from libgitmusic.audio import AudioIO

    with patch.object(AudioIO, "get_audio_hash") as mock_hash:
        hexdigest = hashlib.sha256(audio_data).hexdigest()
        mock_hash.return_value = f"sha256:{hexdigest}"

        # Store audio
        result = object_store.store_audio(temp_audio, compute_hash=True)

        assert result.success is True
        assert result.oid == f"sha256:{hexdigest}"

        # Verify file exists
        audio_path = object_store.get_audio_path(result.oid)
        assert audio_path is not None
        assert audio_path.exists()


def test_store_audio_without_compute_hash(object_store, temp_dir):
    """Test storing audio with pre-computed hash (filename as hash)."""
    hexdigest = "c" * 64
    temp_audio = temp_dir / f"{hexdigest}.mp3"
    temp_audio.write_bytes(b"data")

    result = object_store.store_audio(temp_audio, compute_hash=False)

    # Should use stem (filename without extension) as hash
    assert result.success is True
    assert result.oid == f"sha256:{hexdigest}"

    # Verify file exists
    audio_path = object_store.get_audio_path(result.oid)
    assert audio_path is not None
    assert audio_path.exists()


def test_get_audio_path_not_found(object_store):
    """Test getting path for non-existent audio."""
    non_existent_oid = f"sha256:{'d' * 64}"
    path = object_store.get_audio_path(non_existent_oid)
    assert path is None


def test_get_cover_path_not_found(object_store):
    """Test getting path for non-existent cover."""
    non_existent_oid = f"sha256:{'e' * 64}"
    path = object_store.get_cover_path(non_existent_oid)
    assert path is None


def test_exists(object_store, temp_dir):
    """Test checking if object exists."""
    # Store an audio object to test
    from libgitmusic.audio import AudioIO

    with patch.object(AudioIO, "get_audio_hash") as mock_hash:
        audio_data = b"test audio"
        hexdigest = hashlib.sha256(audio_data).hexdigest()
        mock_hash.return_value = f"sha256:{hexdigest}"

        temp_audio = temp_dir / "audio.mp3"
        temp_audio.write_bytes(audio_data)
        result = object_store.store_audio(temp_audio, compute_hash=True)

    assert object_store.exists(result.oid) is True

    # Non-existent
    non_existent_oid = f"sha256:{'f' * 64}"
    assert object_store.exists(non_existent_oid) is False


def test_copy_to_workdir(object_store, temp_dir):
    """Test copying audio to work directory with metadata."""
    # Create a temporary audio file in object store
    audio_data = b"audio content"
    hexdigest = hashlib.sha256(audio_data).hexdigest()

    # We need to mock the AudioIO methods to avoid actual audio processing
    from libgitmusic.audio import AudioIO

    with patch.object(AudioIO, "get_audio_hash") as mock_hash:
        mock_hash.return_value = f"sha256:{hexdigest}"

        temp_audio = temp_dir / "source.mp3"
        temp_audio.write_bytes(audio_data)
        result = object_store.store_audio(temp_audio, compute_hash=True)

    # Mock embed_metadata to just copy the file
    with patch.object(AudioIO, "embed_metadata") as mock_embed:
        # Just copy the file
        def side_effect(src, metadata, cover_data, dst):
            import shutil

            shutil.copy2(src, dst)

        mock_embed.side_effect = side_effect

        target_path = temp_dir / "work" / "output.mp3"
        target_path.parent.mkdir(exist_ok=True)

        metadata = {"title": "Test", "artists": ["Artist"]}
        object_store.copy_to_workdir(result.oid, target_path, metadata)

        assert target_path.exists()
        # File should have been "copied" (mocked)


def test_copy_to_workdir_with_cover(object_store, temp_dir):
    """Test copying audio with cover to work directory."""
    # Store audio and cover
    audio_data = b"audio"
    cover_data = b"cover"

    from libgitmusic.audio import AudioIO

    with patch.object(AudioIO, "get_audio_hash") as mock_hash:
        hexdigest = hashlib.sha256(audio_data).hexdigest()
        mock_hash.return_value = f"sha256:{hexdigest}"

        temp_audio = temp_dir / "audio.mp3"
        temp_audio.write_bytes(audio_data)
        audio_result = object_store.store_audio(temp_audio, compute_hash=True)

    cover_result = object_store.store_cover(cover_data)

    # Mock embed_metadata
    with patch.object(AudioIO, "embed_metadata") as mock_embed:
        target_path = temp_dir / "work" / "with_cover.mp3"
        target_path.parent.mkdir(exist_ok=True)

        metadata = {"title": "With Cover"}
        object_store.copy_to_workdir(audio_result.oid, target_path, metadata, cover_result.oid)

        # Verify embed_metadata was called with cover data
        assert mock_embed.called
        call_args = mock_embed.call_args
        assert call_args[0][2] == cover_data  # cover_data argument


def test_copy_to_workdir_audio_not_found(object_store, temp_dir):
    """Test copying non-existent audio."""
    non_existent_oid = f"sha256:{'g' * 64}"
    target_path = temp_dir / "output.mp3"

    with pytest.raises(FileNotFoundError):
        object_store.copy_to_workdir(non_existent_oid, target_path, {})


def test_verify_integrity(object_store):
    """Test integrity verification of stored objects."""
    # Store a valid object
    cover_data = b"valid cover"
    result = object_store.store_cover(cover_data)

    total, errors, error_details = object_store.verify_integrity()

    # Should have at least the cover we stored
    assert total >= 1
    assert errors == 0
    assert len(error_details) == 0


def test_verify_integrity_corrupted(object_store, temp_dir):
    """Test integrity verification with corrupted object."""
    # Store a cover
    cover_data = b"original cover"
    result = object_store.store_cover(cover_data)

    # Corrupt the file
    cover_path = object_store.get_cover_path(result.oid)
    with open(cover_path, "wb") as f:
        f.write(b"corrupted data")

    total, errors, error_details = object_store.verify_integrity()

    # Should detect error
    assert errors >= 1
    assert len(error_details) >= 1
    assert "hash mismatch" in error_details[0].lower()


def test_store_cover_invalid_compute_hash_false(object_store):
    """Test store_cover with compute_hash=False should raise ValueError."""
    cover_data = b"data"

    result = object_store.store_cover(cover_data, compute_hash=False)
    assert result.success is False
    assert "Must compute hash" in result.message


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
