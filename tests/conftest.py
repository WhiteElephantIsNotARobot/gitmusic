import sys
import os
from pathlib import Path

# Add the repo directory to sys.path to allow importing libgitmusic
repo_dir = Path(__file__).parent.parent / "repo"
sys.path.insert(0, str(repo_dir))
