from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # When set (e.g. DEFAULT), host/token missing from .env are read from ~/.databrickscfg — same as AI dev kit MCP.
    databricks_config_profile: str = ""
    # Alternative to DATABRICKS_HTTP_PATH: builds /sql/1.0/warehouses/<id>
    databricks_warehouse_id: str = ""

    databricks_host: str = ""
    databricks_http_path: str = ""
    databricks_token: str = ""
    # If true: ignore DATABRICKS_TOKEN (use databricks auth login / SDK OAuth for MLflow + SQL).
    # Set when a stale PAT is still in .env or you only use CLI auth.
    databricks_oauth_only: bool = False

    mlflow_tracking_uri: str = "databricks"
    mlflow_registry_uri: str = "databricks-uc"

    predictions_table: str = "mle.batch_model_inference.predictions"
    mlflow_run_id: str = "9d740e9e5f544d9490100cef238bf074"
    # Subpath under the run for mlflow.lightgbm.log_model, or "auto" to find the folder that contains MLmodel
    mlflow_model_artifact_path: str = "auto"
    # If set (e.g. models:/catalog.schema.my_model/1), loads this URI instead of runs:/…/artifact_path
    mlflow_model_uri: str = ""
    local_model_path: str | None = None
    # Stop waiting on MLflow artifact download (urllib3 SSL retries can hang indefinitely).
    mlflow_load_timeout_seconds: int = 300

    app_host: str = "127.0.0.1"
    app_port: int = 8765

    def uses_databricks_pat(self) -> bool:
        """True when we should use DATABRICKS_TOKEN (Thrift + PAT-based MLflow env)."""
        if self.databricks_oauth_only:
            return False
        return bool((self.databricks_token or "").strip())


settings = Settings()
