/**
 * PM2 Standard Configuration
 * Quản lý tập trung Backend (API + Frontend) và Hubbot (Telegram)
 */
const fs = require("fs");
const path = require("path");

const PROJECT_ROOT = process.env.PROJECT_ROOT || __dirname;
const LOG_DIR = process.env.CNTX_LOG_DIR || process.env.LOG_DIR || path.join(PROJECT_ROOT, "logs");
const BACKEND_DIR = path.join(PROJECT_ROOT, "backend_ai", "backend");
const HUBBOT_DIR = path.join(PROJECT_ROOT, "hubbot");

for (const dir of ["pm2", "backend", "hubbot", "runner"]) {
  fs.mkdirSync(path.join(LOG_DIR, dir), { recursive: true });
}

const logPath = (...parts) => path.join(LOG_DIR, ...parts);

module.exports = {
  apps: [
    {
      name: "spider-backend",
      cwd: BACKEND_DIR,
      script: path.join(BACKEND_DIR, "venv/bin/python3"),
      args: "scripts/run_api.py",
      interpreter: "none",
      exec_mode: "fork",
      instances: 2,
      instance_var: "INSTANCE_ID",
      env: {
        PYTHONPATH: ".",
        CNTX_ROLE: "api",
        CNTX_LOG_DIR: LOG_DIR,
        LOG_LEVEL: process.env.LOG_LEVEL || "INFO",
        API_PORT_BASE: "8002",
        SERVICE_MODE: "local",
      },
      autorestart: true,
      kill_timeout: 15000,
      max_memory_restart: "1500M",
      error_file: logPath("pm2", "spider-backend.error.log"),
      out_file: logPath("pm2", "spider-backend.out.log"),
      combine_logs: true,
      log_date_format: "YYYY-MM-DD HH:mm:ss Z",
    },
    {
      name: "spider-hubbot",
      cwd: HUBBOT_DIR,
      script: path.join(HUBBOT_DIR, "venv_hub/bin/python3"),
      args: "main.py",
      interpreter: "none",
      exec_mode: "fork",
      instances: 1,
      env: {
        CNTX_LOG_DIR: LOG_DIR,
        LOG_LEVEL: process.env.LOG_LEVEL || "INFO",
      },
      autorestart: true,
      max_memory_restart: "500M",
      error_file: logPath("pm2", "spider-hubbot.error.log"),
      out_file: logPath("pm2", "spider-hubbot.out.log"),
      combine_logs: true,
      log_date_format: "YYYY-MM-DD HH:mm:ss Z",
    }
  ],
};
