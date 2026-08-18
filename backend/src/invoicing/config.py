from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INVOICING_", env_file=".env", extra="ignore")

    # 开发默认值：SQLite + 本地文件存储 + 进程内队列（无需 docker）；
    # 生产对齐：database_url 指向 postgres、storage_backend=s3、queue_backend=redis
    database_url: str = "sqlite:///./invoicing.db"
    redis_url: str = "redis://localhost:6379/0"
    storage_backend: str = "local"  # local | s3
    storage_root: str = "./data/originals"  # local 后端存储根目录
    queue_backend: str = "local"  # local（同步内联执行）| redis（arq worker）
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "invoice-originals"
    minio_secure: bool = False
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480
    mcp_token: str = "change-me"
    workbuddy_inbox_dir: str = ""  # WorkBuddy 附件目录信任边界；为空表示不限制，生产建议配置
    fernet_key: str = "change-me-32bytes-base64-key!!!"  # 生产环境必须覆盖
    admin_username: str = "admin"
    admin_password: str = "admin123"
    mock_verify_rules: str = '{"fail_prefixes": ["0000"], "error_prefixes": ["0001"]}'
    # LLM 引擎（OpenAI 兼容协议；未启用时解析链路降级为现状行为）
    llm_enabled: bool = False
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_api_key: str = ""
    llm_model_text: str = "qwen-plus"
    llm_model_vlm: str = "qwen-vl-plus"
    llm_timeout_seconds: float = 25.0
    llm_max_retries: int = 1
    scheduler_enabled: bool = True
    log_level: str = "INFO"
    # 数字员工 P2：企微群机器人 webhook（空=不启用通知）
    notify_webhook_url: str = ""
    # 数字员工 P3：渐进自主阈值（0=观察期全人工；>0 时预判 approve 且 conf≥阈值自动通过；拦截永不自动）
    auto_review_threshold: float = 0.0


settings = Settings()
