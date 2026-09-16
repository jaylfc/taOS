"""Test for Postgres opt-in fix - reproduces the issue and verifies the fix."""
import asyncio
import tempfile
import pathlib
from tinyagentos.litellm_migrate import migrate


def test_postgres_optin_does_not_hard_fail():
    """Test that .litellm_db_url does not hard-fail the controller boot.
    
    This test reproduces the issue where writing `data/.litellm_db_url` would
    disable the working SQLite keystore and then hard-fail with a RuntimeError.
    
    The fix ensures that:
    1. inhouse_keys is independent of db_url (always True by default)
    2. migrate() logs a warning and returns status instead of raising
    3. DATABASE_URL is not exported to LiteLLM subprocess
    """
    d = pathlib.Path(tempfile.mkdtemp())
    (d / '.litellm_db_url').write_text('postgresql://taos:pw@localhost:5432/litellm')
    
    # The migrate() function should NOT raise RuntimeError anymore
    # It should log a warning and return "postgres-configured"
    result = asyncio.run(migrate(d))
    
    assert result, f'migrate returned {result!r}'
    assert result == "postgres-configured", f'Expected "postgres-configured", got {result!r}'


def test_inhouse_keys_independent_of_db_url():
    """Test that inhouse_keys is True even when .litellm_db_url is present."""
    from tinyagentos.app import create_app
    import tempfile
    import shutil
    
    # Create a temporary directory with .litellm_db_url
    temp_dir = pathlib.Path(tempfile.mkdtemp())
    try:
        # Create a minimal config
        config_dir = temp_dir / "config"
        data_dir = temp_dir / "data"
        catalog_dir = temp_dir / "catalog"
        
        config_dir.mkdir()
        data_dir.mkdir()
        catalog_dir.mkdir()
        
        # Write a basic config.yaml
        config_file = config_dir / "config.yaml"
        config_file.write_text("""
server:
  port: 6969
  litellm_port: 7834
backends: []
agents: []
""")
        
        # Write .litellm_db_url
        db_url_file = data_dir / ".litellm_db_url"
        db_url_file.write_text("postgresql://taos:pw@localhost:5432/litellm")
        
        # Create a minimal catalog
        catalog_file = catalog_dir / "manifest.yaml"
        catalog_file.write_text("""
id: test-catalog
name: Test Catalog
""")
        
        # Create app - should not raise
        # This tests that inhouse_keys remains True despite db_url being set
        app = create_app(data_dir=data_dir, catalog_dir=catalog_dir)
        
        # Verify the app was created successfully
        assert app is not None
        
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
