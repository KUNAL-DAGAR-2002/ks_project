"""Force every test module onto an isolated database before app imports occur."""
import os

os.environ["KIRANA_DATABASE_URL"] = "sqlite:///./test_kirana_saathi.db"
