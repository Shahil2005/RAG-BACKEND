import os

from dotenv import load_dotenv

# Load .env.test instead of .env when testing
env_file = ".env.test" if os.getenv("ENV_MODE") == "test" else ".env"
load_dotenv(env_file)  # Overrides system env vars with file contents
