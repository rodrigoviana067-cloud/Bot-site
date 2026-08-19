#!/usr/bin/env python3
import os
import secrets
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    SECRET_KEY: str = secrets.token_hex(32)
    JWT_SECRET: str = secrets.token_hex(32)
    JWT_EXPIRE_MINUTES: int = 1440
    DB_PATH: str = "./affiliate.db"
    DB_POOL_SIZE: int = 5
    DB_TIMEOUT: int = 30
    WA_BRIDGE_URL: str = "http://127.0.0.1:3000"
    WA_BRIDGE_TIMEOUT: int = 15
    WA_BRIDGE_MAX_RETRIES: int = 3
    WA_BRIDGE_RETRY_DELAY: float = 2.0
    WA_MAX_MESSAGES_PER_MINUTE: int = 20
    WA_COOLDOWN_BETWEEN_MESSAGES: float = 3.0
    SHOPEE_API_URL: str = "https://open-api.affiliate.shopee.com.br/graphql"
    SHOPEE_CACHE_TTL_SECONDS: int = 180
    SHOPEE_REQUEST_COOLDOWN: float = 10.0
    SHOPEE_MAX_PRODUCTS_PER_REQUEST: int = 50
    MP_ACCESS_TOKEN: str = ""
    MP_WEBHOOK_SECRET: str = ""
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASS: str = ""
    SMTP_FROM: str = ""
    DISCORD_WEBHOOK_URL: str = ""
    ALERT_ON_ERROR_COUNT: int = 5
    AUTOPOST_CHECK_INTERVAL: int = 30
    AUTOPOST_MAX_POSTS_PER_DAY: int = 50
    AUTOPOST_MAX_ERRORS_BEFORE_PAUSE: int = 3
    AUTOPOST_PAUSE_MINUTES: int = 30
    AUTOPOST_BACKOFF_MULTIPLIER: float = 2.0
    AUTOPOST_MAX_BACKOFF_MINUTES: int = 120
    AB_TEST_MIN_SAMPLES: int = 30
    AB_TEST_MIN_DIFFERENCE: float = 0.15
    REMARKETING_ENABLED: bool = True
    REMARKETING_HOURS_1: int = 24
    REMARKETING_HOURS_2: int = 72
    REMARKETING_HOURS_3: int = 168
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "text"
    LOG_MAX_BYTES: int = 10485760
    LOG_BACKUP_COUNT: int = 5
    PROMETHEUS_PORT: int = 9090
    METRICS_ENABLED: bool = True

settings = Settings()
