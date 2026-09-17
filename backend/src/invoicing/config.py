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
    mcp_token: str = "change-me"  # legacy 内建令牌（兼容期，建议改用个人令牌）
    # MCP 身份与权限（design/2026-09-13-MCP身份与权限设计.md）
    mcp_issuer_url: str = "http://localhost:8000"  # 阶段 1 仅作 claims["iss"]；阶段 2 接 IdP 时替换
    mcp_resource_url: str = "http://localhost:8000/mcp"  # RFC 8707 audience / PRM 元数据地址
    mcp_required_scopes: str = ""  # 服务器级最低门槛，逗号分隔；空=不设（权限全交工具级，见 R2）
    mcp_token_default_expires_days: int = 90  # 签发默认有效期
    workbuddy_inbox_dir: str = ""  # WorkBuddy 附件目录信任边界；为空表示不限制，生产建议配置
    fernet_key: str = "change-me-32bytes-base64-key!!!"  # 生产环境必须覆盖
    admin_username: str = "admin"
    # 审计保留期：登录成功行按天清理（失败登录与业务操作长期保留）
    audit_login_retention_days: int = 90
    # 小额零星（增值税起征点，按次 300-500 元各省自定）阈值：超过则无票支出需取得发票
    expense_petty_cash_threshold: int = 500
    # 差旅伙食补助日标准（元/天）：补助无发票，按 天数 × 本标准 自动生成内部凭证
    travel_allowance_daily_standard: int = 100
    admin_password: str = "admin123"
    # bcrypt cost（口令哈希强度）：生产保持 12；测试调低可省大量时间
    # （本机 cost=12 一次哈希约 4.5 秒，测试里大量建用户会白付）
    password_hash_rounds: int = 12
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
    # 启动时后台预热 OCR 引擎（模型首次加载 10-30s；未装 ocr extra 时为空转）
    ocr_preload: bool = True
    log_level: str = "INFO"
    # 运维兜底：日志落盘轮转（log_level 首次被消费，见 ops/logging_setup.py）
    log_dir: str = "./logs"
    log_max_bytes: int = 10 * 1024 * 1024  # 单文件 10MB
    log_backup_count: int = 9              # 保留 9 个轮转文件
    # 运维兜底：启动自检与备份（design/2026-09-16-运维兜底设计.md §5/§8）
    startup_checks_strict: bool = False  # true 时自检 warn/fail 项升级为阻断启动
    ops_backup_dir: str = "./data/backups"
    ops_backup_retention: int = 7        # 备份保留份数
    alert_cooldown_hours: int = 4        # 同 rule_key 冷却期（防抖）
    # 数字员工 P2：企微群机器人 webhook（空=不启用通知）
    notify_webhook_url: str = ""
    # 数字员工 P3：渐进自主阈值（0=观察期全人工；>0 时预判 approve 且 conf≥阈值自动通过；拦截永不自动）
    auto_review_threshold: float = 0.0


settings = Settings()
