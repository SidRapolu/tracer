from pathlib import Path

from dotenv import load_dotenv

# Pulls in repo-root .env into the environment
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
