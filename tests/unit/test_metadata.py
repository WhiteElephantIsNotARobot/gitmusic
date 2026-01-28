import pytest
import tempfile
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import Mock

# Import the modules to test
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "repo"))
from libgitmusic.metadata import MetadataManager, ValidationError
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
def metadata_manager(context):
    """Create a MetadataManager instance with test context."""
    return MetadataManager(context)


def test_validate_entry_valid(metadata_manager):
    """Test validation of a valid metadata entry."""
    valid_entry = {
        "audio_oid": "sha256:" + "a" * 64,
        "title": "Test Song",
        "artists": ["Artist 1", "Artist 2"],
        "created_at": "2024-01-01T00:00:00Z",
        "date": "2024-01-01",
        "cover_oid": "sha256:" + "b" * 64,
        "album": "Test Album",
        "uslt": "Test lyrics",
    }

    # Should not raise any exception
    validated = metadata_manager.validate_entry(valid_entry.copy())

    # Check that date was normalized (should be unchanged since already YYYY-MM-DD)
    assert validated["date"] == "2024-01-01"
    # Check that audio_oid unchanged
    assert validated["audio_oid"] == valid_entry["audio_oid"]
    # Check that title unchanged
    assert validated["title"] == valid_entry["title"]


def test_validate_entry_missing_required(context):
    """Test validation with missing required fields."""
    manager = MetadataManager(context)

    # Missing audio_oid
    with pytest.raises(ValidationError, match="audio_oid 字段缺失"):
        manager.validate_entry(
            {"title": "Test", "artists": ["A"], "created_at": "2024-01-01T00:00:00Z"}
        )

    # Missing title
    with pytest.raises(ValidationError, match="title 字段缺失"):
        manager.validate_entry(
            {
                "audio_oid": "sha256:" + "a" * 64,
                "artists": ["A"],
                "created_at": "2024-01-01T00:00:00Z",
            }
        )

    # Missing artists
    with pytest.raises(ValidationError, match="artists 字段缺失"):
        manager.validate_entry(
            {
                "audio_oid": "sha256:" + "a" * 64,
                "title": "Test",
                "created_at": "2024-01-01T00:00:00Z",
            }
        )

    # Missing created_at
    with pytest.raises(ValidationError, match="created_at 字段缺失"):
        manager.validate_entry(
            {"audio_oid": "sha256:" + "a" * 64, "title": "Test", "artists": ["A"]}
        )


def test_validate_entry_invalid_audio_oid(context):
    """Test validation with invalid audio_oid format."""
    manager = MetadataManager(context)

    # Invalid hash length
    with pytest.raises(ValidationError, match="audio_oid 格式无效"):
        manager.validate_entry(
            {
                "audio_oid": "sha256:abc",
                "title": "Test",
                "artists": ["A"],
                "created_at": "2024-01-01T00:00:00Z",
            }
        )

    # Missing sha256: prefix
    with pytest.raises(ValidationError, match="audio_oid 格式无效"):
        manager.validate_entry(
            {
                "audio_oid": "a" * 64,
                "title": "Test",
                "artists": ["A"],
                "created_at": "2024-01-01T00:00:00Z",
            }
        )

    # Invalid characters
    with pytest.raises(ValidationError, match="audio_oid 格式无效"):
        manager.validate_entry(
            {
                "audio_oid": "sha256:" + "g" * 64,
                "title": "Test",
                "artists": ["A"],
                "created_at": "2024-01-01T00:00:00Z",
            }
        )


def test_validate_entry_invalid_title(context):
    """Test validation with invalid title."""
    manager = MetadataManager(context)

    # Empty title
    with pytest.raises(ValidationError, match="title 不能为空字符串"):
        manager.validate_entry(
            {
                "audio_oid": "sha256:" + "a" * 64,
                "title": "",
                "artists": ["A"],
                "created_at": "2024-01-01T00:00:00Z",
            }
        )

    # Whitespace-only title
    with pytest.raises(ValidationError, match="title 不能为空字符串"):
        manager.validate_entry(
            {
                "audio_oid": "sha256:" + "a" * 64,
                "title": "   ",
                "artists": ["A"],
                "created_at": "2024-01-01T00:00:00Z",
            }
        )

    # Non-string title
    with pytest.raises(ValidationError, match="title 必须是字符串"):
        manager.validate_entry(
            {
                "audio_oid": "sha256:" + "a" * 64,
                "title": 123,
                "artists": ["A"],
                "created_at": "2024-01-01T00:00:00Z",
            }
        )


def test_validate_entry_invalid_artists(context):
    """Test validation with invalid artists."""
    manager = MetadataManager(context)

    # Empty artists list
    with pytest.raises(ValidationError, match="artists 不能为空数组"):
        manager.validate_entry(
            {
                "audio_oid": "sha256:" + "a" * 64,
                "title": "Test",
                "artists": [],
                "created_at": "2024-01-01T00:00:00Z",
            }
        )

    # Non-list artists
    with pytest.raises(ValidationError, match="artists 必须是数组"):
        manager.validate_entry(
            {
                "audio_oid": "sha256:" + "a" * 64,
                "title": "Test",
                "artists": "Artist",
                "created_at": "2024-01-01T00:00:00Z",
            }
        )

    # Empty artist string
    with pytest.raises(ValidationError, match="artists\\[0\\] 不能为空字符串"):
        manager.validate_entry(
            {
                "audio_oid": "sha256:" + "a" * 64,
                "title": "Test",
                "artists": [""],
                "created_at": "2024-01-01T00:00:00Z",
            }
        )

    # Non-string artist
    with pytest.raises(ValidationError, match="artists\\[0\\] 必须是字符串"):
        manager.validate_entry(
            {
                "audio_oid": "sha256:" + "a" * 64,
                "title": "Test",
                "artists": [123],
                "created_at": "2024-01-01T00:00:00Z",
            }
        )


def test_validate_entry_date_normalization(context):
    """Test date normalization and validation."""
    manager = MetadataManager(context)

    # Test YYYY format normalization
    entry = {
        "audio_oid": "sha256:" + "a" * 64,
        "title": "Test",
        "artists": ["A"],
        "created_at": "2024-01-01T00:00:00Z",
        "date": "2024",
    }
    validated = manager.validate_entry(entry.copy())
    assert validated["date"] == "2024-01-01"

    # Test YYYY-MM format normalization
    entry["date"] = "2024-06"
    validated = manager.validate_entry(entry.copy())
    assert validated["date"] == "2024-06-01"

    # Test invalid date format
    entry["date"] = "2024/06/01"
    with pytest.raises(ValidationError, match="date 格式无效"):
        manager.validate_entry(entry.copy())

    # Test invalid date (month 13)
    entry["date"] = "2024-13-01"
    with pytest.raises(ValidationError, match="date 不是有效日期"):
        manager.validate_entry(entry.copy())


def test_validate_entry_created_at(context):
    """Test created_at validation."""
    manager = MetadataManager(context)

    # Valid ISO8601 with timezone
    entry = {
        "audio_oid": "sha256:" + "a" * 64,
        "title": "Test",
        "artists": ["A"],
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    validated = manager.validate_entry(entry.copy())
    assert validated["created_at"] == "2024-01-01T00:00:00+00:00"

    # Zulu time format
    entry["created_at"] = "2024-01-01T00:00:00Z"
    validated = manager.validate_entry(entry.copy())
    assert validated["created_at"] == "2024-01-01T00:00:00Z"

    # Missing timezone
    entry["created_at"] = "2024-01-01T00:00:00"
    with pytest.raises(ValidationError, match="created_at 不是有效的 ISO8601 时间戳"):
        manager.validate_entry(entry.copy())

    # Invalid format
    entry["created_at"] = "2024-01-01"
    with pytest.raises(ValidationError, match="created_at 不是有效的 ISO8601 时间戳"):
        manager.validate_entry(entry.copy())


def test_validate_entry_optional_fields(context):
    """Test validation of optional fields."""
    manager = MetadataManager(context)

    # Valid cover_oid
    entry = {
        "audio_oid": "sha256:" + "a" * 64,
        "title": "Test",
        "artists": ["A"],
        "created_at": "2024-01-01T00:00:00Z",
        "cover_oid": "sha256:" + "b" * 64,
    }
    validated = manager.validate_entry(entry.copy())
    assert validated["cover_oid"] == entry["cover_oid"]

    # Invalid cover_oid format
    entry["cover_oid"] = "invalid"
    with pytest.raises(ValidationError, match="cover_oid 格式无效"):
        manager.validate_entry(entry.copy())

    # Valid album
    entry = {
        "audio_oid": "sha256:" + "a" * 64,
        "title": "Test",
        "artists": ["A"],
        "created_at": "2024-01-01T00:00:00Z",
        "album": "Test Album",
    }
    validated = manager.validate_entry(entry.copy())
    assert validated["album"] == "Test Album"

    # Empty album string
    entry["album"] = ""
    with pytest.raises(ValidationError, match="album 不能为空字符串"):
        manager.validate_entry(entry.copy())


def test_duplicate_check(metadata_manager, temp_dir):
    """Test duplicate audio_oid detection."""
    # Create a metadata file with one entry
    entries = [
        {
            "audio_oid": "sha256:" + "a" * 64,
            "title": "Song 1",
            "artists": ["Artist"],
            "created_at": "2024-01-01T00:00:00Z",
        }
    ]

    # Save the initial entry
    metadata_manager.save_all(entries)

    # Try to save entries with duplicate
    entries_with_duplicate = entries + [
        {
            "audio_oid": "sha256:" + "a" * 64,  # Duplicate!
            "title": "Song 2",
            "artists": ["Artist"],
            "created_at": "2024-01-02T00:00:00Z",
        }
    ]

    with pytest.raises(ValidationError, match="发现重复的 audio_oid"):
        metadata_manager.save_all(entries_with_duplicate)


def test_save_and_load_all(metadata_manager, temp_dir):
    """Test saving and loading metadata entries."""
    entries = [
        {
            "audio_oid": "sha256:" + "a" * 64,
            "title": "Song 1",
            "artists": ["Artist 1"],
            "created_at": "2024-01-01T00:00:00Z",
            "date": "2024-01-01",
        },
        {
            "audio_oid": "sha256:" + "b" * 64,
            "title": "Song 2",
            "artists": ["Artist 2", "Artist 3"],
            "created_at": "2024-01-02T00:00:00Z",
            "album": "Album 1",
        },
    ]

    # Save entries
    metadata_manager.save_all(entries)

    # Load entries
    loaded = metadata_manager.load_all()

    # Check that loaded entries match saved entries (order preserved)
    assert len(loaded) == 2
    assert loaded[0]["audio_oid"] == entries[0]["audio_oid"]
    assert loaded[0]["title"] == entries[0]["title"]
    assert loaded[1]["audio_oid"] == entries[1]["audio_oid"]
    assert loaded[1]["album"] == entries[1]["album"]

    # Check that file exists
    assert metadata_manager.file_path.exists()


def test_update_entry(metadata_manager, temp_dir):
    """Test updating an existing metadata entry."""
    # Create initial entry
    initial_entry = {
        "audio_oid": "sha256:" + "a" * 64,
        "title": "Old Title",
        "artists": ["Old Artist"],
        "created_at": "2024-01-01T00:00:00Z",
    }
    metadata_manager.save_all([initial_entry])

    # Update the entry
    updates = {"title": "New Title", "artists": ["New Artist"], "album": "New Album"}
    metadata_manager.update_entry("sha256:" + "a" * 64, updates)

    # Load and verify update
    loaded = metadata_manager.load_all()
    assert len(loaded) == 1
    assert loaded[0]["title"] == "New Title"
    assert loaded[0]["artists"] == ["New Artist"]
    assert loaded[0]["album"] == "New Album"
    # Unchanged fields should remain
    assert loaded[0]["audio_oid"] == "sha256:" + "a" * 64
    assert loaded[0]["created_at"] == "2024-01-01T00:00:00Z"


def test_update_entry_new(metadata_manager, temp_dir):
    """Test updating a non-existent entry (creates new entry)."""
    # Update non-existent entry
    updates = {
        "title": "New Song",
        "artists": ["New Artist"],
        "created_at": "2024-01-01T00:00:00Z",
    }
    metadata_manager.update_entry("sha256:" + "c" * 64, updates)

    # Load and verify new entry
    loaded = metadata_manager.load_all()
    assert len(loaded) == 1
    assert loaded[0]["audio_oid"] == "sha256:" + "c" * 64
    assert loaded[0]["title"] == "New Song"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
