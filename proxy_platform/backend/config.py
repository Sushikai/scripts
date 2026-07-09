import os
from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    APP_NAME: str = "ProxyPlatform"
    VERSION: str = "1.0.0"
    DEBUG: bool = True
    
    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./proxy_platform.db"
    
    # Security
    SECRET_KEY: str = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days
    
    # Xray config
    XRAY_BINARY_PATH: Optional[str] = os.getenv("XRAY_BINARY_PATH", "/usr/local/bin/xray")
    XRAY_CONFIG_PATH: str = "./xray_config.json"
    
    # Proxy settings
    DEFAULT_PORT_RANGE_START: int = 10000
    DEFAULT_PORT_RANGE_END: int = 60000
    MAX_USERS: int = 100
    
    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()