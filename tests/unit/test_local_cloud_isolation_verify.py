from scripts.verify_local_cloud_isolation import validate_backend, validate_frontend


def test_local_isolation_verify_accepts_local_targets():
    checks, failures = validate_backend(
        {
            "APP_ENV": "local",
            "DATABASE_URL": "sqlite+pysqlite:///./data/edumind_demo.db",
            "VINCI_BASE_URL": "http://127.0.0.1:8010",
        }
    )
    assert checks
    assert failures == []


def test_local_isolation_verify_reports_cloud_database():
    _, failures = validate_backend(
        {
            "APP_ENV": "local",
            "DATABASE_URL": "mysql+pymysql://root:pwd@47.84.228.226:3306/edumind",
            "VINCI_BASE_URL": "http://127.0.0.1:8010",
        }
    )
    assert any(item["name"] == "database_local_only" for item in failures)
    assert any(item["name"] == "backend_env_no_cloud_marker" for item in failures)


def test_frontend_isolation_verify_reports_cloud_api_base():
    _, failures = validate_frontend(
        {
            "VITE_MOBILE_API_BASE_URL": "http://47.84.228.226:2004",
            "VITE_MOBILE_PROXY_TARGET": "http://127.0.0.1:2004",
        }
    )
    assert any(item["name"] == "frontend_api_base_local_only" for item in failures)
    assert any(item["name"] == "frontend_env_no_cloud_marker" for item in failures)
